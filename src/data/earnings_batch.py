"""유니버스 일괄 실적 fetch — earnings.json 생성.

GitHub Actions에서 주 1회 실행 권장 (실적은 분기별 갱신이라 매 스캔 불필요).
종목당 ~0.5초 + sleep 0.1초, 4 worker 병렬 → 800종목 약 2~3분.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from src.data.earnings_fetcher import fetch_and_analyze
from src.data.fetcher import get_universe

logger = logging.getLogger(__name__)


def fetch_one(stock: dict) -> tuple[str, dict | None]:
    ticker = stock["ticker"]
    try:
        result = fetch_and_analyze(ticker)
        time.sleep(0.1)  # Naver rate-limit 회피
        return ticker, result
    except Exception as e:
        logger.debug(f"[earnings] {ticker}: {e}")
        return ticker, None


def build_earnings_map(universe: list[dict] | None = None,
                       max_workers: int = 4,
                       output_path: str = "earnings.json") -> dict:
    """유니버스 전체 실적 fetch → JSON 저장."""
    started = time.time()
    if universe is None:
        universe = get_universe()
    logger.info(f"[earnings] 시작: {len(universe)} 종목, {max_workers} worker")

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_one, s): s for s in universe}
        done = 0
        for f in as_completed(futs):
            ticker, result = f.result()
            if result and result.get("available"):
                out[ticker] = {
                    "label": result["label"],
                    "yoy_pct": result.get("yoy_pct"),
                    "period": result.get("period"),
                    "status": result.get("quarterly", {}).get("status") or
                              result.get("annual", {}).get("status"),
                    # 보조 정보
                    "quarterly_label": result.get("quarterly", {}).get("label"),
                    "quarterly_yoy_pct": result.get("quarterly", {}).get("yoy_pct"),
                    "annual_label": result.get("annual", {}).get("label"),
                    "annual_yoy_pct": result.get("annual", {}).get("yoy_pct"),
                }
            done += 1
            if done % 100 == 0:
                logger.info(f"[earnings] {done}/{len(universe)} "
                            f"성공 {len(out)} 경과 {time.time()-started:.0f}s")

    elapsed = time.time() - started
    logger.info(f"[earnings] 완료: {len(out)}/{len(universe)} ({elapsed:.0f}s)")

    payload = {
        "generated_at": datetime.now().isoformat(),
        "elapsed_sec": round(elapsed, 1),
        "universe_size": len(universe),
        "fetched_count": len(out),
        "earnings": out,
    }
    Path(output_path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return payload


if __name__ == "__main__":
    import sys
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    universe = get_universe()
    if limit:
        universe = universe[:limit]
    payload = build_earnings_map(universe)
    print(f"\n=== 실적 분류 분포 ===")
    from collections import Counter
    counter = Counter(e["label"] for e in payload["earnings"].values() if e["label"])
    for label, count in counter.most_common():
        print(f"  {label}: {count}")
