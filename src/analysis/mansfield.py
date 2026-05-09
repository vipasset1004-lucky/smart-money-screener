"""Mansfield Relative Strength — Stan Weinstein.

종목/시장 비율의 52주 이동평균 정규화.
음→양 전환 시점이 Stage 1 → Stage 2 전환의 표준 신호.

공식:
  RS_raw = (종목 종가 / 시장 종가)
  RS_norm = (RS_raw / RS_raw.MA52주) - 1
  → 0보다 크면 시장보다 강함
  → 음→양 전환 = Mansfield Buy Signal
"""

from __future__ import annotations

import pandas as pd


def mansfield_rs(close: pd.Series, market_close: pd.Series,
                 ma_weeks: int = 52) -> dict:
    """Mansfield RS + 양수 전환 검출.

    daily 기준이라 ma_weeks * 5 = 260일 이평 사용.
    """
    if close is None or market_close is None:
        return {"available": False}
    # 일봉 기준 정렬
    df = pd.concat([close.rename("c"), market_close.rename("m")],
                   axis=1, join="inner").dropna()
    if len(df) < ma_weeks * 5:
        return {"available": False}

    rs_raw = df["c"] / df["m"]
    ma = rs_raw.rolling(ma_weeks * 5).mean()
    rs_norm = (rs_raw / ma) - 1
    rs_norm = rs_norm.dropna()
    if len(rs_norm) < 60:
        return {"available": False}

    current = float(rs_norm.iloc[-1] * 100)  # %로
    prev_30 = float(rs_norm.iloc[-30] * 100) if len(rs_norm) >= 30 else current

    # 음→양 전환 검출 (최근 30일 내)
    recent_60 = rs_norm.iloc[-60:]
    crossing_up = False
    cross_idx = None
    for i in range(1, len(recent_60)):
        if recent_60.iloc[i-1] < 0 and recent_60.iloc[i] >= 0:
            crossing_up = True
            cross_idx = i
    days_since_cross = (len(recent_60) - 1 - cross_idx) if cross_idx else None

    # 추세 방향
    trend_up = current > prev_30

    return {
        "available": True,
        "rs_norm_pct": round(current, 2),
        "rs_30d_ago_pct": round(prev_30, 2),
        "trend_up": bool(trend_up),
        "positive": current > 0,
        "crossing_up_recent": bool(crossing_up),
        "days_since_cross": days_since_cross,
        # Mansfield Buy: 양수 전환 후 N일 내 + 트렌드 상승
        "mansfield_buy": bool(crossing_up and days_since_cross is not None
                              and days_since_cross <= 20 and trend_up),
    }
