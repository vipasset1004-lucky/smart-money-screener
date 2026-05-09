"""데이터 수집 — pykrx + FinanceDataReader 하이브리드.

수급(외인/기관)은 pykrx로, 종목 유니버스는 FDR로 가져온다.
네이버는 결과 화면에서 종목 링크아웃에만 사용.
"""

from __future__ import annotations

import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def get_universe(min_mktcap: int = 100_000_000_000,
                 min_amount: int = 3_000_000_000) -> list[dict]:
    """KRX 보통주 중 시총·거래대금 필터 통과 종목."""
    import FinanceDataReader as fdr
    df = fdr.StockListing("KRX")
    df = df[df["MarketId"].isin(["STK", "KSQ"])]
    df = df[df["Code"].str.endswith("0")]  # 보통주만
    df = df[(df["Marcap"] >= min_mktcap) &
            (df["Amount"] >= min_amount) &
            (df["Amount"] > 0)]
    out = []
    for _, row in df.iterrows():
        out.append({
            "ticker": str(row["Code"]).zfill(6),
            "name": str(row["Name"]),
            "market": "KOSPI" if row["MarketId"] == "STK" else "KOSDAQ",
            "marcap": int(row["Marcap"]),
        })
    logger.info(f"[universe] {len(out)} 종목")
    return out


def fetch_ohlcv(ticker: str, days: int = 400) -> Optional[pd.DataFrame]:
    """OHLCV 일봉 (pykrx)."""
    from pykrx import stock
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 60)).strftime("%Y%m%d")
    try:
        df = stock.get_market_ohlcv(start, end, ticker)
        if df is None or df.empty:
            return None
        df = df.rename(columns={
            "시가": "open", "고가": "high", "저가": "low",
            "종가": "close", "거래량": "volume", "거래대금": "amount",
        })
        return df[["open", "high", "low", "close", "volume", "amount"]]
    except Exception as e:
        logger.warning(f"[ohlcv] {ticker}: {e}")
        return None


def fetch_supply_demand(ticker: str, days: int = 120) -> Optional[pd.DataFrame]:
    """투자자별 순매수 (외인/기관/개인 등) — pykrx.

    Returns DataFrame with columns:
        외국인, 기관합계, 개인, 기타법인, 금융투자, 투신, 연기금, 사모, 보험, 은행
    """
    from pykrx import stock
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")
    try:
        df = stock.get_market_trading_value_by_date(start, end, ticker)
        if df is None or df.empty:
            return None
        keep = [c for c in ["외국인합계", "기관합계", "개인", "기타법인",
                            "금융투자", "투신", "연기금", "사모", "보험", "은행",
                            "외국인"]
                if c in df.columns]
        df = df[keep].copy()
        if "외국인합계" in df.columns and "외국인" not in df.columns:
            df = df.rename(columns={"외국인합계": "외국인"})
        return df
    except Exception as e:
        logger.warning(f"[supply] {ticker}: {e}")
        return None


def fetch_market_cap(ticker: str) -> Optional[int]:
    """가장 최근 시가총액."""
    from pykrx import stock
    today = datetime.now().strftime("%Y%m%d")
    try:
        df = stock.get_market_cap_by_date(
            (datetime.now() - timedelta(days=10)).strftime("%Y%m%d"),
            today, ticker)
        if df is None or df.empty:
            return None
        return int(df["시가총액"].iloc[-1])
    except Exception as e:
        logger.warning(f"[mcap] {ticker}: {e}")
        return None


def fetch_all(ticker: str, ohlcv_days: int = 400, supply_days: int = 120,
              sleep: float = 0.15) -> Optional[dict]:
    """한 종목의 OHLCV + 수급을 한 번에. 호출 사이에 sleep."""
    ohlcv = fetch_ohlcv(ticker, days=ohlcv_days)
    time.sleep(sleep)
    supply = fetch_supply_demand(ticker, days=supply_days)
    if ohlcv is None:
        return None
    return {"ticker": ticker, "ohlcv": ohlcv, "supply": supply}
