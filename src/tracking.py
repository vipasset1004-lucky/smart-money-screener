"""포워드 추적 — 매 스캔 결과를 날짜별 archive 후 후속 수익률 계산.

원리:
1. 매 스캔(16:00, 21:00) 결과를 archives/YYYY-MM-DD_HHMM.json 으로 저장
2. 현재(마지막 거래일) 가격을 fetch
3. 각 archive의 종목별 진입가 → 현재가 = 보유 수익률
4. 라벨/날짜별 집계 → UI 표시
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from src.data.bulk_fetcher import fetch_ohlcv_light

logger = logging.getLogger(__name__)

ARCHIVE_DIR = Path("archives")
ARCHIVE_DIR.mkdir(exist_ok=True)


def archive_current_results(results_path: str = "results.json") -> Optional[str]:
    """results.json을 날짜·시간 stamp로 복사."""
    src = Path(results_path)
    if not src.exists():
        return None
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
        gen_at = data.get("generated_at", datetime.now().isoformat())
        # 파일명: YYYY-MM-DD_HHMM
        stamp = gen_at.replace(":", "").replace("-", "")[:13]  # 20260509_2126
        dst = ARCHIVE_DIR / f"results_{stamp}.json"
        # 라벨 있는 종목만 저장 (저장 공간 절약)
        labeled = [r for r in (data.get("results") or []) if r.get("labels")]
        slim = {
            "generated_at": gen_at,
            "regime": data.get("regime"),
            "results": [
                {
                    "ticker": r["ticker"],
                    "name": r["name"],
                    "labels": r["labels"],
                    "score": r.get("score"),
                    "ensemble": r.get("ensemble", {}).get("ensemble"),
                    "vcp_score": (r.get("vcp") or {}).get("vcp_score"),
                    "entry_close": (r.get("metrics") or {}).get("close"),
                    "marcap": r.get("marcap"),
                }
                for r in labeled
            ],
        }
        dst.write_text(json.dumps(slim, ensure_ascii=False, indent=2,
                                   default=str), encoding="utf-8")
        logger.info(f"[archive] saved {dst.name} ({len(slim['results'])} 종목)")
        return str(dst)
    except Exception as e:
        logger.warning(f"[archive] err: {e}")
        return None


def list_archives(limit_days: int = 60) -> list[Path]:
    """최근 N일 내 archive 파일 리스트 (오래된 순)."""
    files = sorted(ARCHIVE_DIR.glob("results_*.json"))
    if not files:
        return []
    cutoff = datetime.now().timestamp() - limit_days * 86400
    return [f for f in files if f.stat().st_mtime >= cutoff]


def load_archives(limit_days: int = 60) -> list[dict]:
    """archive 데이터 로드."""
    out = []
    for f in list_archives(limit_days):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            d["_file"] = f.name
            out.append(d)
        except Exception as e:
            logger.debug(f"archive load {f}: {e}")
    return out


def fetch_current_closes(tickers: list[str], max_workers: int = 16) -> dict:
    """모든 ticker의 마지막 거래일 close fetch (병렬)."""
    out = {}
    if not tickers:
        return out
    started = time.time()

    def _one(t):
        df = fetch_ohlcv_light(t, days=10)
        if df is None or df.empty:
            return t, None
        return t, float(df["close"].iloc[-1])

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_one, t): t for t in tickers}
        for f in as_completed(futs):
            t, c = f.result()
            if c is not None:
                out[t] = c
    logger.info(f"[track] {len(out)}/{len(tickers)} prices in {time.time()-started:.0f}s")
    return out


def build_tracking() -> dict:
    """모든 archive에서 라벨 종목 추출 + 현재가 fetch + 수익률 집계.

    Returns:
        {
          "generated_at": ...,
          "by_date": [
            {date, regime, items: [{ticker, name, labels, entry_close, current, ret_pct, days_held}]},
            ...
          ],
          "summary_by_label": {label: {count, avg_ret, win_rate}},
        }
    """
    archives = load_archives(limit_days=60)
    if not archives:
        return {"generated_at": datetime.now().isoformat(),
                "by_date": [], "summary_by_label": {},
                "message": "아직 archive 데이터 없음 — 첫 스캔 후 누적됩니다"}

    # 모든 ticker 수집
    all_tickers = set()
    for ar in archives:
        for r in ar.get("results", []):
            all_tickers.add(r["ticker"])

    # 현재가 fetch
    current = fetch_current_closes(sorted(all_tickers))

    # archive별 추적 결과 빌드
    by_date = []
    today = datetime.now().date()
    label_buckets = {}

    for ar in archives:
        gen = ar.get("generated_at", "")
        try:
            ar_date = datetime.fromisoformat(gen).date()
        except Exception:
            continue
        days_held = (today - ar_date).days
        items = []
        for r in ar.get("results", []):
            tk = r["ticker"]
            entry = r.get("entry_close")
            now = current.get(tk)
            if not entry or not now or entry <= 0:
                continue
            ret = (now - entry) / entry * 100
            items.append({
                "ticker": tk,
                "name": r["name"],
                "labels": r.get("labels", []),
                "score": (r.get("score") or {}).get("total"),
                "ensemble": r.get("ensemble"),
                "vcp_score": r.get("vcp_score"),
                "entry_close": entry,
                "current_close": now,
                "ret_pct": round(ret, 2),
                "days_held": days_held,
                "naver_url": f"https://finance.naver.com/item/main.naver?code={tk}",
            })
            # label 집계
            for l in r.get("labels", []):
                b = label_buckets.setdefault(l, {"count": 0, "wins": 0,
                                                  "ret_sum": 0.0,
                                                  "by_age": {}})
                b["count"] += 1
                b["ret_sum"] += ret
                if ret > 0: b["wins"] += 1
                age_bin = ("0d" if days_held == 0 else
                           "1-3d" if days_held <= 3 else
                           "4-7d" if days_held <= 7 else
                           "8-14d" if days_held <= 14 else
                           "15-30d" if days_held <= 30 else
                           "30d+")
                ab = b["by_age"].setdefault(age_bin, {"count": 0, "wins": 0,
                                                       "ret_sum": 0.0})
                ab["count"] += 1
                ab["ret_sum"] += ret
                if ret > 0: ab["wins"] += 1

        # 점수 내림차순
        items.sort(key=lambda x: x["ret_pct"], reverse=True)
        by_date.append({
            "date": ar_date.isoformat(),
            "generated_at": gen,
            "days_held": days_held,
            "regime": (ar.get("regime") or {}).get("label"),
            "items": items,
        })

    # 최신 날짜가 위로
    by_date.sort(key=lambda x: x["date"], reverse=True)

    # 라벨 요약
    summary = {}
    for l, b in label_buckets.items():
        summary[l] = {
            "count": b["count"],
            "win_rate": round(b["wins"] / b["count"] * 100, 1) if b["count"] else 0,
            "avg_ret_pct": round(b["ret_sum"] / b["count"], 2) if b["count"] else 0,
            "by_age": {ab: {
                "count": v["count"],
                "win_rate": round(v["wins"] / v["count"] * 100, 1) if v["count"] else 0,
                "avg_ret_pct": round(v["ret_sum"] / v["count"], 2) if v["count"] else 0,
            } for ab, v in b["by_age"].items()},
        }

    return {
        "generated_at": datetime.now().isoformat(),
        "archives_count": len(archives),
        "by_date": by_date,
        "summary_by_label": summary,
    }
