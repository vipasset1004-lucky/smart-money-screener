"""시장 환경 엔진 — 코스피/코스닥 추세 + 시장 거래대금 + 신고가 종목 수.

GPT 설계서 4번 항목. 시장이 강세/방어 어느 쪽인지 판단해서
사용자에게 컨텍스트 제공.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def _ma_position_score(close: pd.Series) -> float:
    """현재가가 60일선 위(+) 아래(-) 얼마나 있는가. -1~+1로 정규화."""
    if close is None or len(close) < 60:
        return 0.0
    ma60 = close.rolling(60).mean().iloc[-1]
    diff = (close.iloc[-1] - ma60) / ma60
    return float(np.clip(diff * 5, -1, 1))  # ±20%면 만점


def _ma_slope_score(close: pd.Series, n: int = 30) -> float:
    """60일선의 30일간 기울기. -1~+1로 정규화."""
    if close is None or len(close) < 60 + n:
        return 0.0
    ma = close.rolling(60).mean().dropna()
    if len(ma) < n:
        return 0.0
    slope = (ma.iloc[-1] - ma.iloc[-n]) / abs(ma.iloc[-n])
    return float(np.clip(slope * 10, -1, 1))  # 10%면 만점


def _amount_trend_score(ohlcv_map: dict) -> float:
    """전체 시장 거래대금 최근 5일 vs 직전 15일 비율."""
    if not ohlcv_map:
        return 0.0
    daily_sum = {}
    for tk, df in ohlcv_map.items():
        if df is None or "amount" not in df.columns:
            continue
        for d, v in df["amount"].tail(20).items():
            daily_sum[d] = daily_sum.get(d, 0) + int(v)
    if len(daily_sum) < 20:
        return 0.0
    series = pd.Series(daily_sum).sort_index()
    recent5 = series.tail(5).mean()
    prior15 = series.iloc[-20:-5].mean()
    if prior15 == 0:
        return 0.0
    ratio = recent5 / prior15
    # 1.3배면 만점, 0.7배면 -1
    return float(np.clip((ratio - 1.0) / 0.3, -1, 1))


def _high_proximity_breadth(ohlcv_map: dict, threshold: float = 0.92) -> float:
    """52일 고점의 92% 이상에 위치한 종목 비율."""
    if not ohlcv_map:
        return 0.0
    near_high = 0
    total = 0
    for tk, df in ohlcv_map.items():
        if df is None or len(df) < 30:
            continue
        high_max = df["high"].tail(min(60, len(df))).max()
        if high_max <= 0:
            continue
        if df["close"].iloc[-1] >= high_max * threshold:
            near_high += 1
        total += 1
    if total == 0:
        return 0.0
    ratio = near_high / total
    # 30% 이상이면 만점
    return float(np.clip(ratio / 0.30, 0, 1))


def market_regime(kospi_close: Optional[pd.Series],
                  kosdaq_close: Optional[pd.Series],
                  ohlcv_map: dict) -> dict:
    """전체 시장 모드 판정 (0~100점)."""
    components = {}

    # KOSPI 추세 (25점)
    kospi_pos = _ma_position_score(kospi_close)
    kospi_slope = _ma_slope_score(kospi_close)
    kospi_score = (kospi_pos + kospi_slope) / 2  # -1~+1
    components["kospi"] = round((kospi_score + 1) / 2 * 25, 1)

    # KOSDAQ 추세 (15점)
    kosdaq_pos = _ma_position_score(kosdaq_close)
    kosdaq_slope = _ma_slope_score(kosdaq_close)
    kosdaq_score = (kosdaq_pos + kosdaq_slope) / 2
    components["kosdaq"] = round((kosdaq_score + 1) / 2 * 15, 1)

    # 시장 거래대금 (25점)
    amt = _amount_trend_score(ohlcv_map)
    components["amount"] = round((amt + 1) / 2 * 25, 1)

    # 신고가 종목 비율 (35점)
    breadth = _high_proximity_breadth(ohlcv_map)
    components["breadth"] = round(breadth * 35, 1)

    total = sum(components.values())

    if total >= 80:
        mode, mode_label, mode_color = "strong", "🔥 강세장", "good"
    elif total >= 60:
        mode, mode_label, mode_color = "offensive", "📈 공격 가능", "alert"
    elif total >= 40:
        mode, mode_label, mode_color = "neutral", "⚖ 중립", "watch"
    elif total >= 20:
        mode, mode_label, mode_color = "defensive", "🛡 방어", "warn"
    else:
        mode, mode_label, mode_color = "cash", "💰 현금 우위", "danger"

    return {
        "score": round(total, 1),
        "mode": mode,
        "label": mode_label,
        "color": mode_color,
        "components": components,
        "advice": _mode_advice(mode),
    }


def _mode_advice(mode: str) -> str:
    return {
        "strong":    "신고가·돌파 종목 적극 추종, 단타·텐버거 모두 활용",
        "offensive": "주도주·강세섹터 위주, 진입 조절",
        "neutral":   "눌림목·매집 종목 위주, 신중 진입",
        "defensive": "현금 비중 확대, 시장 대비 강한 종목만",
        "cash":      "관망 권장, 신규 진입 자제",
    }.get(mode, "")
