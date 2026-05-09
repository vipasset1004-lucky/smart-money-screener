"""Stage 1 경량 프리필터 — 1500종목 → ~300종목.

목적: 돈이 안 들어오는 종목을 빠르게 제거.
사용 데이터: OHLCV 30일치 (네이버 수급 X).
점수 100점:
  - 거래대금 가속도 (recent3 / avg20): 30점
  - 추세 (close > MA20*0.95):            25점
  - 시장 대비 강함 (RS):                 25점
  - 고점 근접 (52일 고점의 92% 이상):    20점
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def relative_strength(close: pd.Series, market_close: pd.Series,
                      window: int = 20) -> float:
    """종목 수익률 - 시장 수익률 (window일 기준)."""
    if len(close) < window or len(market_close) < window:
        return 0.0
    stock_ret = (close.iloc[-1] / close.iloc[-window]) - 1
    mkt_ret = (market_close.iloc[-1] / market_close.iloc[-window]) - 1
    return (stock_ret - mkt_ret) * 100  # %p 차이


def prefilter_score(ohlcv: pd.DataFrame,
                    market_close: Optional[pd.Series] = None) -> dict:
    """단일 종목 prefilter 점수."""
    if ohlcv is None or len(ohlcv) < 20:
        return {"total": 0.0, "passed": False}

    close = ohlcv["close"]
    amount = ohlcv["amount"]

    # 1) 거래대금 가속도
    avg_amt_20 = amount.tail(20).mean()
    recent_amt = amount.tail(3).mean()
    amt_ratio = recent_amt / avg_amt_20 if avg_amt_20 > 0 else 0
    s_amt = float(np.clip(amt_ratio / 1.5, 0, 1) * 30)

    # 2) 추세 (MA20 위)
    ma20 = close.tail(20).mean()
    trend_ok = close.iloc[-1] >= ma20 * 0.95
    s_trend = 25 if trend_ok else 0
    # 60일선 위면 +5 보너스 (length 허용 시)
    if len(close) >= 60:
        ma60 = close.tail(60).mean()
        if close.iloc[-1] >= ma60:
            s_trend = min(25, s_trend + 5)

    # 3) RS (시장 대비 강함)
    rs_pp = relative_strength(close, market_close, window=20) \
        if market_close is not None else 0
    # +5%p 이상 강하면 만점, -5%p면 0
    s_rs = float(np.clip((rs_pp + 5) / 10, 0, 1) * 25)

    # 4) 고점 근접 (가능한 범위에서)
    high_lookback = min(len(ohlcv), 60)  # bulk는 30일이지만 풀 fetch면 60+
    high_max = ohlcv["high"].tail(high_lookback).max()
    proximity = close.iloc[-1] / high_max if high_max > 0 else 0
    # 95% 이상이면 만점, 85% 미만이면 0
    s_prox = float(np.clip((proximity - 0.85) / 0.10, 0, 1) * 20)

    total = s_amt + s_trend + s_rs + s_prox

    return {
        "total": round(total, 1),
        "passed": total >= 50,  # 임계
        "amount_ratio": round(float(amt_ratio), 2),
        "trend_ok": trend_ok,
        "rs_pp": round(rs_pp, 2),
        "high_proximity": round(proximity * 100, 1),
        "components": {
            "amount": round(s_amt, 1),
            "trend": round(s_trend, 1),
            "rs": round(s_rs, 1),
            "proximity": round(s_prox, 1),
        },
    }


def run_prefilter(ohlcv_map: dict, market_close: Optional[pd.Series] = None,
                  threshold: float = 50.0,
                  max_passed: int = 300) -> list[tuple[str, dict]]:
    """전체 종목 prefilter. 점수 내림차순 정렬, 상위 max_passed개 반환."""
    scored = []
    for ticker, df in ohlcv_map.items():
        score = prefilter_score(df, market_close)
        if score["total"] >= threshold:
            scored.append((ticker, score))
    scored.sort(key=lambda x: x[1]["total"], reverse=True)
    return scored[:max_passed]
