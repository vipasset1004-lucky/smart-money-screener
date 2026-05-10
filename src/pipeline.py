"""파이프라인 — 2-Stage.

Stage 1: 1500종목 → ~300 (가벼운 OHLCV만, 30~60s)
Stage 2: 300 종목 → 분석/라벨 (네이버 수급 포함, 5~7min)
"""

from __future__ import annotations

import gc
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from src.data.fetcher import (
    get_universe, fetch_ohlcv, fetch_supply_demand, fetch_market_cap,
)
from src.data.bulk_fetcher import (
    bulk_fetch_universe, fetch_kospi_history, fetch_kosdaq_history,
)
from src.data.earnings_batch import build_earnings_map
from src.analysis.prefilter import run_prefilter
from src.analysis.score import (
    supply_demand_score, detect_accumulation, chart_metrics,
)
from src.analysis.risk import detect_risk_signals
from src.analysis.position import classify_position
from src.analysis.masters import evaluate_all
from src.analysis.regime import market_regime
from src.analysis.sector import build_correlation_clusters, confluence_count
from src.analysis.weekly import (
    to_weekly, to_weekly_supply, detect_weekly_accumulation,
    detect_vcp, detect_volume_dry_explode, smart_money_weekly,
    weekly_breakout,
)
from src.analysis.wyckoff import wyckoff_pack as wyckoff_diagnose
from src.analysis.vcp import detect_vcp_precise
from src.analysis.mansfield import mansfield_rs
from src.analysis.ensemble import evaluate_ensemble
from src.signals.departure import short_term_departure, tenbagger_departure
from src.classifier.labeler import classify

logger = logging.getLogger(__name__)


# ── Stage 2 ──────────────────────────────────────────────

def analyze_stage2(stock: dict, prefilter_score: dict,
                   ohlcv_light=None, supply_pages: int = 5,
                   market_close=None, earnings: dict | None = None) -> dict | None:
    """Stage 2 정밀 분석 (1종목)."""
    ticker = stock["ticker"]
    try:
        # OHLCV 풀 (300일) — MA240 + 여유 17일. Starter 메모리 핏 위해 축소.
        ohlcv = fetch_ohlcv(ticker, days=300)
        if ohlcv is None or len(ohlcv) < 60:
            # fallback: stage1의 30일 데이터로라도
            ohlcv = ohlcv_light
            if ohlcv is None or len(ohlcv) < 30:
                return None

        # 네이버 수급 (느림)
        supply = fetch_supply_demand(ticker, max_pages=supply_pages)
        mcap = stock.get("marcap") or fetch_market_cap(ticker)

        score = supply_demand_score(supply, ohlcv, mcap)
        accum = detect_accumulation(ohlcv)
        metrics = chart_metrics(ohlcv, market_close=market_close)

        # 주봉 변환 + 텐버거용 weekly_pack
        weekly = to_weekly(ohlcv)
        weekly_supply = to_weekly_supply(supply)
        weekly_pack = None
        if weekly is not None and len(weekly) >= 26:
            w_accum = detect_weekly_accumulation(weekly)
            weekly_pack = {
                "accum": w_accum,
                "vcp": detect_vcp(weekly),
                "dry_explode": detect_volume_dry_explode(weekly),
                "smart_money_w": smart_money_weekly(weekly_supply),
                "breakout_w": weekly_breakout(weekly, w_accum),
            }
        # 중간 DataFrame 즉시 폐기 — weekly_pack에 결과만 보존
        del weekly, weekly_supply

        # Wyckoff Spring/SOS/VSA + VCP 정밀 + Mansfield RS
        wyckoff = wyckoff_diagnose(ohlcv, accum)
        vcp_pack = detect_vcp_precise(ohlcv)
        mansfield = mansfield_rs(ohlcv["close"], market_close) \
            if market_close is not None else {"available": False}

        # 출발 시그널 (기존 로직, weekly_pack 활용)
        st_sig = short_term_departure(ohlcv, supply, metrics, accum)
        tb_sig = tenbagger_departure(ohlcv, supply, metrics, accum,
                                      score=score, marcap=mcap,
                                      weekly_pack=weekly_pack)

        # 6명 대가 앙상블
        ensemble = evaluate_ensemble(
            ohlcv, supply, score, accum, metrics,
            weekly_pack, wyckoff, vcp_pack, mansfield,
        )

        risk = detect_risk_signals(ohlcv, supply, accum, metrics)
        position = classify_position(
            ohlcv, supply, accum, metrics, score["total"],
            st_sig["triggered"], tb_sig["triggered"], risk,
        )
        masters = evaluate_all(ohlcv, supply, score, accum, metrics)
        labels = classify(st_sig, tb_sig, accum, score["total"],
                          marcap=mcap, ensemble=ensemble, vcp_pack=vcp_pack,
                          earnings=earnings)

        return {
            "ticker": ticker,
            "name": stock["name"],
            "market": stock.get("market"),
            "marcap": mcap,
            "labels": labels,
            "position": position,
            "risk": risk,
            "score": score,
            "stage1_score": prefilter_score.get("total"),
            "accumulation": accum,
            "metrics": {k: v for k, v in metrics.items() if k != "ma240"},
            "weekly_pack": weekly_pack,
            "wyckoff": wyckoff,
            "vcp": vcp_pack,
            "mansfield": mansfield,
            "ensemble": ensemble,
            "short_term": st_sig,
            "tenbagger": tb_sig,
            "masters": masters,
            "earnings": earnings,
            "naver_url": f"https://finance.naver.com/item/main.naver?code={ticker}",
        }
    except Exception as e:
        logger.warning(f"[stage2] {ticker}: {e}")
        return None


# ── 파이프라인 ───────────────────────────────────────────

def run_pipeline(limit: int | None = None, max_workers_s2: int = 4,
                 stage1_threshold: float = 60.0,
                 stage1_max_passed: int = 500,
                 supply_pages: int = 5) -> dict:
    started = time.time()

    # Universe
    universe = get_universe()
    if limit:
        universe = universe[:limit]
    universe_size = len(universe)

    # ── Stage 1: 가벼운 OHLCV bulk ─────────────────
    logger.info(f"[pipeline] Stage 1 시작: {universe_size}종목 OHLCV bulk")
    s1_start = time.time()
    # 250일 코스피: chart_metrics RS 120d 계산용
    kospi = fetch_kospi_history(days=250)
    kosdaq = fetch_kosdaq_history(days=30)
    ohlcv_map = bulk_fetch_universe(universe, days=30, max_workers=12)
    prefiltered = run_prefilter(ohlcv_map, kospi,
                                threshold=stage1_threshold,
                                max_passed=stage1_max_passed)
    s1_elapsed = time.time() - s1_start
    logger.info(f"[pipeline] Stage 1 완료: "
                f"{len(prefiltered)}종목 통과 ({s1_elapsed:.0f}s)")

    # ── 시장 모드 + 섹터 클러스터 (Stage 1 결과 기반) ─
    regime = market_regime(kospi, kosdaq, ohlcv_map)
    logger.info(f"[regime] {regime['label']} 점수 {regime['score']}")
    # 섹터 클러스터: Stage 1 통과 종목들끼리만 (계산 부담 ↓)
    s1_tickers = {t for t, _ in prefiltered}
    s1_ohlcv = {t: ohlcv_map[t] for t in s1_tickers if t in ohlcv_map}
    clusters = build_correlation_clusters(s1_ohlcv, days=30, threshold=0.65)

    # ── Stage 2: 정밀 분석 ─────────────────────────
    by_ticker = {s["ticker"]: s for s in universe}
    s2_input = [(by_ticker[t], score, ohlcv_map.get(t))
                for t, score in prefiltered if t in by_ticker]
    # 메모리 절감: 전체 ohlcv_map 폐기 (s2_input이 필요한 데이터 다 보유)
    del ohlcv_map, s1_ohlcv
    gc.collect()

    # ── 실적 데이터 (Stage 1 통과분만, Naver 일괄) ──
    s_start = time.time()
    s2_universe = [stock for stock, _, _ in s2_input]
    earnings_payload = build_earnings_map(
        s2_universe, max_workers=4, output_path="earnings.json",
    )
    earnings_map = earnings_payload.get("earnings", {})
    logger.info(f"[earnings] {len(earnings_map)}/{len(s2_universe)} 종목 "
                f"실적 분류 완료 ({time.time()-s_start:.0f}s)")

    logger.info(f"[pipeline] Stage 2 시작: {len(s2_input)}종목 정밀 분석")
    s2_start = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=max_workers_s2) as ex:
        futs = {ex.submit(analyze_stage2, st, sc, ol, supply_pages, kospi,
                          earnings_map.get(st["ticker"])):
                st["ticker"]
                for (st, sc, ol) in s2_input}
        done = 0
        for f in as_completed(futs):
            r = f.result()
            if r:
                results.append(r)
            done += 1
            if done % 50 == 0:
                logger.info(f"[pipeline] Stage 2 진행 {done}/{len(s2_input)}, "
                            f"라벨 {len(results)}")
    s2_elapsed = time.time() - s2_start
    logger.info(f"[pipeline] Stage 2 완료: "
                f"{len(results)}종목 라벨 ({s2_elapsed:.0f}s)")
    gc.collect()

    # 섹터 동조도 부여 (라벨 부여 종목 기준)
    labels_map = {r["ticker"]: r.get("labels", []) for r in results}
    for r in results:
        r["confluence"] = confluence_count(r["ticker"], labels_map, clusters)

    # 정렬: 라벨 우선순위 → 리스크 페널티 적용 점수
    # 위험·경고 종목은 같은 점수여도 아래로
    def sort_key(r):
        labels = r.get("labels", [])
        priority = 0
        if "⭐황금자리" in labels: priority = 100
        elif "💎텐버거" in labels: priority = 80
        elif "🏛대가합의" in labels: priority = 75
        elif "🌅폭발임박" in labels: priority = 70  # 출발 직전 (사용자 통찰)
        elif "🎯VCP" in labels: priority = 65  # 백테스트 검증 60d 75%
        elif "⚡단타" in labels: priority = 60
        elif "🛡️안정형" in labels: priority = 50  # 백테스트 60d 승률 76%
        elif "🔍매집중" in labels: priority = 40
        sev = (r.get("risk") or {}).get("severity", "safe")
        penalty = {"safe": 0, "watch": -3, "warning": -15,
                   "danger": -40, "unknown": -5}.get(sev, 0)
        return (priority, r["score"]["total"] + penalty)

    results.sort(key=sort_key, reverse=True)

    elapsed = time.time() - started
    return {
        "generated_at": datetime.now().isoformat(),
        "elapsed_sec": round(elapsed, 1),
        "stage1_elapsed": round(s1_elapsed, 1),
        "stage2_elapsed": round(s2_elapsed, 1),
        "universe_size": universe_size,
        "stage1_passed": len(prefiltered),
        "stage2_passed": len(results),
        "passed_count": len(results),
        "regime": regime,
        "results": results,
    }


def save_results(payload: dict, path: str = "results.json",
                 archive: bool = False) -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    # 포워드 추적용 archive (21:00 evening_refresh에서만 True로 호출)
    if archive:
        try:
            from src.tracking import archive_current_results
            archive_current_results(path)
        except Exception as e:
            logger.debug(f"[archive] err: {e}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    import sys
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace")
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    payload = run_pipeline(limit=limit)
    # GitHub Actions/standalone 실행 시 항상 archive (포워드 추적 데이터 누적)
    save_results(payload, archive=True)
    print(f"\n=== 결과 ===")
    print(f"전체 유니버스: {payload['universe_size']}")
    print(f"Stage1 통과:   {payload['stage1_passed']} ({payload['stage1_elapsed']}s)")
    print(f"Stage2 라벨:   {payload['stage2_passed']} ({payload['stage2_elapsed']}s)")
    print(f"총 소요시간:   {payload['elapsed_sec']}s")
    print(f"\n상위 10개:")
    for r in payload["results"][:10]:
        labels = " ".join(r["labels"]) or "-"
        pos = r["position"]["label"]
        sev = r["risk"]["severity"]
        print(f"  {r['ticker']} {r['name']:14s} {labels:20s} "
              f"점수 {r['score']['total']:4.1f}  위치 {pos:6s}  리스크 {sev}")
