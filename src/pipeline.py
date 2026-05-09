"""전체 파이프라인 — universe → analyze → label → 결과."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from src.data.fetcher import (
    get_universe, fetch_ohlcv, fetch_supply_demand, fetch_market_cap,
)
from src.analysis.score import (
    supply_demand_score, detect_accumulation, chart_metrics,
)
from src.signals.departure import short_term_departure, tenbagger_departure
from src.classifier.labeler import classify

logger = logging.getLogger(__name__)


def analyze_one(stock: dict) -> dict | None:
    """한 종목 분석."""
    ticker = stock["ticker"]
    try:
        ohlcv = fetch_ohlcv(ticker, days=400)
        if ohlcv is None or len(ohlcv) < 60:
            return None
        # 약 6페이지 ≈ 90일 (네이버 한 페이지당 ~15행)
        supply = fetch_supply_demand(ticker, max_pages=6)
        mcap = stock.get("marcap") or fetch_market_cap(ticker)

        score = supply_demand_score(supply, ohlcv, mcap)
        accum = detect_accumulation(ohlcv)
        metrics = chart_metrics(ohlcv)

        st_sig = short_term_departure(ohlcv, supply, metrics, accum)
        tb_sig = tenbagger_departure(ohlcv, supply, metrics, accum)

        labels = classify(st_sig, tb_sig, accum, score["total"])
        if not labels:
            return None  # 아무 라벨도 없으면 결과에서 제외

        return {
            "ticker": ticker,
            "name": stock["name"],
            "market": stock.get("market"),
            "marcap": mcap,
            "labels": labels,
            "score": score,
            "accumulation": accum,
            "metrics": {k: v for k, v in metrics.items() if k != "ma240"},
            "short_term": st_sig,
            "tenbagger": tb_sig,
            "naver_url": f"https://finance.naver.com/item/main.naver?code={ticker}",
        }
    except Exception as e:
        logger.warning(f"[analyze] {ticker}: {e}")
        return None


def run_pipeline(limit: int | None = None, max_workers: int = 4) -> dict:
    """전체 파이프라인 실행."""
    started = time.time()
    universe = get_universe()
    if limit:
        universe = universe[:limit]
    logger.info(f"[pipeline] {len(universe)} 종목 분석 시작")

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(analyze_one, s): s for s in universe}
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            if r:
                results.append(r)
            if i % 50 == 0:
                logger.info(f"[pipeline] 진행 {i}/{len(universe)}, 통과 {len(results)}")

    # 점수 내림차순 정렬
    results.sort(key=lambda r: r["score"]["total"], reverse=True)

    elapsed = time.time() - started
    return {
        "generated_at": datetime.now().isoformat(),
        "elapsed_sec": round(elapsed, 1),
        "universe_size": len(universe),
        "passed_count": len(results),
        "results": results,
    }


def save_results(payload: dict, path: str = "results.json") -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    import sys, io
    # Windows cp949 콘솔에서도 이모지 출력 가능하도록 UTF-8 강제
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace")
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    payload = run_pipeline(limit=limit)
    save_results(payload)
    print(f"\n=== 결과 ===")
    print(f"전체 유니버스: {payload['universe_size']}")
    print(f"라벨 부여 종목: {payload['passed_count']}")
    print(f"소요시간: {payload['elapsed_sec']}초")
    print(f"\n상위 10개:")
    for r in payload["results"][:10]:
        labels = " ".join(r["labels"])
        print(f"  {r['ticker']} {r['name']:10s} {labels} "
              f"점수 {r['score']['total']} 매집 {r['accumulation']['duration']}일")
