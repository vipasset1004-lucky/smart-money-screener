"""VCP (Volatility Contraction Pattern) — Mark Minervini 핵심 패턴.

폭발 직전 종목의 4가지 특징:
1. 박스 폭이 N개 구간 동안 점진 감소 (변동성 수축)
2. 거래량이 점진 마름
3. ATR (Average True Range)이 함께 줄어듦
4. 마지막 수축에서 거래량 가장 낮음

수치화: 0~100 점수 (90+ 이면 폭발 임박)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def atr(daily: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    h = daily["high"]
    l = daily["low"]
    c = daily["close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def atr_ratio(daily: pd.DataFrame, recent: int = 14, base: int = 60) -> float:
    """최근 ATR / 기준 ATR. 1보다 작으면 변동성 수축."""
    if daily is None or len(daily) < base + recent:
        return 1.0
    atr_series = atr(daily, period=recent).dropna()
    if len(atr_series) < base:
        return 1.0
    recent_atr = atr_series.iloc[-recent:].mean()
    base_atr = atr_series.iloc[-base:].mean()
    return float(recent_atr / base_atr) if base_atr > 0 else 1.0


def detect_vcp_precise(daily: pd.DataFrame, lookback: int = 60,
                       n_segments: int = 4) -> dict:
    """VCP 정밀 검출 — Minervini SEPA의 핵심 패턴.

    - 박스 폭 점진 감소 (각 구간 이전 대비 ≤ 80%)
    - 거래량 점진 감소 (마지막 구간이 가장 낮음)
    - ATR 수축 (최근 14일 / 60일 ≤ 0.85)
    """
    if daily is None or len(daily) < lookback:
        return {"vcp_score": 0, "vcp_detected": False}

    win = daily.tail(lookback)
    seg_len = lookback // n_segments
    segments = [win.iloc[i*seg_len:(i+1)*seg_len] for i in range(n_segments)]

    widths = []
    vols = []
    for s in segments:
        if s.empty: continue
        h, l = s["high"].max(), s["low"].min()
        mid = (h + l) / 2
        w = (h - l) / mid * 100 if mid > 0 else 0
        widths.append(w)
        vols.append(float(s["volume"].mean()))

    if len(widths) < 3:
        return {"vcp_score": 0, "vcp_detected": False}

    # 1) 폭 점진 감소도 (각 구간이 이전 구간의 80% 이하면 +25점)
    width_steps = sum(1 for i in range(len(widths) - 1)
                      if widths[i+1] <= widths[i] * 0.85)
    width_score = (width_steps / (len(widths) - 1)) * 30

    # 2) 거래량 점진 감소 (각 구간이 이전의 90% 이하면 +20점)
    vol_steps = sum(1 for i in range(len(vols) - 1)
                    if vols[i+1] <= vols[i] * 0.9)
    vol_score = (vol_steps / (len(vols) - 1)) * 25

    # 3) 마지막 구간 거래량이 베이스 평균의 70% 미만 (+25점)
    base_vol_avg = np.mean(vols[:-1]) if len(vols) > 1 else vols[-1]
    last_vol_dry = vols[-1] < base_vol_avg * 0.7
    last_vol_score = 25 if last_vol_dry else 0

    # 4) ATR 수축 (+20점)
    atr_r = atr_ratio(daily, recent=14, base=60)
    atr_compressed = atr_r <= 0.85
    atr_score = max(0, min(20, (1 - atr_r) / 0.3 * 20)) if atr_r < 1 else 0

    total = round(width_score + vol_score + last_vol_score + atr_score, 1)
    return {
        "vcp_score": total,
        "vcp_detected": total >= 60,
        "widths_pct": [round(w, 2) for w in widths],
        "atr_ratio": round(atr_r, 2),
        "atr_compressed": bool(atr_compressed),
        "last_vol_dry": bool(last_vol_dry),
        "components": {
            "width_contraction": round(width_score, 1),
            "vol_contraction": round(vol_score, 1),
            "last_vol_dry": last_vol_score,
            "atr_compression": round(atr_score, 1),
        },
    }
