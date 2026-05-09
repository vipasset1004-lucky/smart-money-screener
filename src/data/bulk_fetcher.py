"""대량 데이터 수집 — Stage 1 프리필터용.

핵심: 종목별 무거운 데이터(네이버 수급) 없이, OHLCV만 빠르게.
 - 1500종목 × 30일치 OHLCV를 32 worker 병렬로 ~30~60초.
 - 코스피 지수도 함께 받아 RS 계산에 사용.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def fetch_ohlcv_light(ticker: str, days: int = 30,
                       retry: int = 2) -> Optional[pd.DataFrame]:
    """가벼운 OHLCV (Stage 1 전용). 거래대금 volume×close 근사. retry 추가."""
    from pykrx import stock
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 14)).strftime("%Y%m%d")
    for attempt in range(retry + 1):
        try:
            df = stock.get_market_ohlcv(start, end, ticker)
            if df is not None and not df.empty:
                df = df.rename(columns={
                    "시가": "open", "고가": "high", "저가": "low",
                    "종가": "close", "거래량": "volume",
                })
                df["amount"] = (df["volume"].astype("int64")
                                * df["close"].astype("int64"))
                return df[["open", "high", "low", "close", "volume", "amount"]]
        except Exception as e:
            if attempt < retry:
                time.sleep(0.5 + attempt * 0.5)
                continue
            logger.debug(f"[ohlcv_light] {ticker}: {e}")
        if attempt < retry:
            time.sleep(0.5 + attempt * 0.5)
    return None


def fetch_kospi_history(days: int = 30) -> Optional[pd.Series]:
    """코스피 일별 종가. pykrx KRX endpoint 불안정 → FDR 우선."""
    end = datetime.now()
    start = end - timedelta(days=days + 14)
    # 1) FDR (가장 안정)
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader("KS11", start, end)
        if df is not None and not df.empty:
            return df["Close"].astype(float)
    except Exception as e:
        logger.debug(f"[kospi fdr] {e}")
    # 2) pykrx fallback
    try:
        from pykrx import stock
        df = stock.get_index_ohlcv_by_date(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "1001")
        if df is not None and not df.empty:
            close_col = next((c for c in df.columns
                              if "종가" in c or "close" in c.lower()),
                             df.columns[3])
            return df[close_col].astype(float)
    except Exception as e:
        logger.warning(f"[kospi pykrx] {e}")
    return None


def fetch_kosdaq_history(days: int = 30) -> Optional[pd.Series]:
    end = datetime.now()
    start = end - timedelta(days=days + 14)
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader("KQ11", start, end)
        if df is not None and not df.empty:
            return df["Close"].astype(float)
    except Exception as e:
        logger.debug(f"[kosdaq fdr] {e}")
    return None


def bulk_fetch_universe(universe: list[dict], days: int = 30,
                        max_workers: int = 12) -> dict[str, pd.DataFrame]:
    """종목 리스트 → {ticker: OHLCV DataFrame} 병렬 수집."""
    started = time.time()
    out: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_ohlcv_light, s["ticker"], days): s
                for s in universe}
        done = 0
        for f in as_completed(futs):
            stock = futs[f]
            df = f.result()
            if df is not None and len(df) >= 15:
                out[stock["ticker"]] = df
            done += 1
            if done % 200 == 0:
                logger.info(f"[bulk] {done}/{len(universe)} "
                            f"성공 {len(out)} 경과 {time.time()-started:.0f}s")
    logger.info(f"[bulk] 완료 {len(out)}/{len(universe)} "
                f"({time.time()-started:.0f}s)")
    return out
