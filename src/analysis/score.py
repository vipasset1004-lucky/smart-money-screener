"""수급 점수 + 매집 단계 + 차트 지표 계산."""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── 수급 점수 (Supply/Demand Score) ──────────────────────

def confluence_score(supply: pd.DataFrame, window: int = 5) -> float:
    """A. 수급 일치도 (외인+기관 동시 매수, 개인 매도) — 30점."""
    if supply is None or supply.empty:
        return 0.0
    recent = supply.tail(window)
    score = 0.0
    if "외국인" in recent.columns and "기관합계" in recent.columns:
        both_buy = ((recent["외국인"] > 0) & (recent["기관합계"] > 0)).sum()
        score += (both_buy / window) * 20
    if "개인" in recent.columns:
        ind_sell = (recent["개인"] < 0).sum()
        score += (ind_sell / window) * 10
    return min(30.0, score)


def intensity_score(supply: pd.DataFrame, marcap: int | None,
                    ohlcv: pd.DataFrame, window: int = 5) -> float:
    """B. 수급 강도 — 시총 대비 / 거래대금 대비 — 25점."""
    if supply is None or supply.empty or not marcap:
        return 0.0
    recent = supply.tail(window)
    foreign = recent.get("외국인", pd.Series(dtype=float)).sum()
    inst = recent.get("기관합계", pd.Series(dtype=float)).sum()
    net = foreign + inst

    # 시총 대비 비율 (%)
    ratio_mcap = (net / marcap) * 100
    # 거래대금 대비 비율
    if ohlcv is not None and "amount" in ohlcv.columns and len(ohlcv) >= window:
        amount_sum = ohlcv["amount"].tail(window).sum()
        ratio_amt = (net / amount_sum) * 100 if amount_sum > 0 else 0
    else:
        ratio_amt = 0

    # 정규화: 시총 대비 0.5%면 만점, 거래대금 대비 10%면 만점
    s1 = max(0, min(15, ratio_mcap / 0.5 * 15))
    s2 = max(0, min(10, ratio_amt / 10 * 10))
    return s1 + s2


def persistence_score(supply: pd.DataFrame, lookback: int = 20) -> float:
    """C. 수급 지속성 — 연속 순매수 일수 + 변곡점 보너스 — 25점."""
    if supply is None or supply.empty:
        return 0.0
    score = 0.0
    for col, weight in [("외국인", 10), ("기관합계", 10)]:
        if col not in supply.columns:
            continue
        s = supply[col].tail(lookback)
        # 최근부터 연속 양수 일수
        streak = 0
        for v in reversed(s.values):
            if v > 0:
                streak += 1
            else:
                break
        score += min(weight, streak / 10 * weight)

    # 변곡점 보너스: 직전 5일 매도 → 최근 3일 매수
    if "외국인" in supply.columns and len(supply) >= 8:
        prior = supply["외국인"].iloc[-8:-3]
        recent = supply["외국인"].iloc[-3:]
        if (prior < 0).sum() >= 3 and (recent > 0).sum() >= 2:
            score += 5
    return min(25.0, score)


def aux_score(supply: pd.DataFrame, lookback: int = 60) -> float:
    """D. 보조 신호 — 신규 기관 유입 등 — 20점.

    공매도/대차 데이터는 별도 fetch 필요. 우선 수급 기반만.
    """
    if supply is None or supply.empty:
        return 0.0
    score = 0.0
    # 신규 기관 유입 추정: 최근 60일 중 직전 30일 합계가 ≤0이고, 최근 30일 합계 >0
    if "기관합계" in supply.columns and len(supply) >= 60:
        prior30 = supply["기관합계"].iloc[-60:-30].sum()
        recent30 = supply["기관합계"].iloc[-30:].sum()
        if prior30 <= 0 < recent30:
            score += 10  # 신규 진입 (O'Neil 'I')
    # 외국인 60일 누적이 양수면 +5
    if "외국인" in supply.columns and len(supply) >= 60:
        if supply["외국인"].iloc[-60:].sum() > 0:
            score += 5
    # 최근 5일 연속 외인+기관 모두 양수면 +5
    if "외국인" in supply.columns and "기관합계" in supply.columns:
        last5 = supply.tail(5)
        if (last5["외국인"] > 0).all() and (last5["기관합계"] > 0).all():
            score += 5
    return min(20.0, score)


def supply_demand_score(supply: pd.DataFrame, ohlcv: pd.DataFrame,
                        marcap: int | None) -> dict:
    """전체 수급 점수 (0~100)."""
    a = confluence_score(supply)
    b = intensity_score(supply, marcap, ohlcv)
    c = persistence_score(supply)
    d = aux_score(supply)
    total = a + b + c + d
    return {
        "total": round(total, 1),
        "confluence": round(a, 1),
        "intensity": round(b, 1),
        "persistence": round(c, 1),
        "aux": round(d, 1),
    }


# ── 매집 단계 분석 (Wyckoff / Weinstein) ─────────────────

def detect_accumulation(ohlcv: pd.DataFrame, lookback: int = 250,
                        max_range_pct: float = 25.0) -> dict:
    """매집 단계 판별.

    매집 = 가격 범위가 좁고(±max_range_pct% 이내), 60일선 평탄.
    매집 기간 = 박스권이 시작된 이후 일수.
    """
    if ohlcv is None or len(ohlcv) < 60:
        return {"in_accumulation": False, "duration": 0, "range_pct": None}

    # 최근 lookback 일 (없으면 전체)
    win = ohlcv.tail(min(lookback, len(ohlcv)))
    high = win["high"].max()
    low = win["low"].min()
    mid = (high + low) / 2
    range_pct = (high - low) / mid * 100 if mid > 0 else 999

    # 60일선 기울기 (최근 30일)
    ma60 = ohlcv["close"].rolling(60).mean()
    if len(ma60.dropna()) >= 30:
        slope = (ma60.iloc[-1] - ma60.iloc[-30]) / ma60.iloc[-30] * 100
    else:
        slope = 0

    # 매집 판정: 박스폭 좁고 + MA 거의 평탄
    in_accum = range_pct <= max_range_pct and abs(slope) < 10

    # 매집 기간 측정: 현재 박스권에 머문 일수
    # 단순화: 종가가 [low*0.95, high*1.05] 범위에 머문 연속 일수
    duration = 0
    for i in range(len(ohlcv) - 1, -1, -1):
        c = ohlcv["close"].iloc[i]
        if low * 0.93 <= c <= high * 1.07:
            duration += 1
        else:
            break

    return {
        "in_accumulation": bool(in_accum),
        "duration": duration,
        "range_pct": round(range_pct, 2),
        "ma60_slope_pct": round(slope, 2),
        "box_high": float(high),
        "box_low": float(low),
    }


# ── 차트 지표 ────────────────────────────────────────────

def chart_metrics(ohlcv: pd.DataFrame) -> dict:
    """차트 보조 지표 (이평선, RVOL, 신고가 등)."""
    if ohlcv is None or len(ohlcv) < 60:
        return {}
    close = ohlcv["close"]
    volume = ohlcv["volume"]
    amount = ohlcv["amount"] if "amount" in ohlcv.columns else None

    ma5 = close.rolling(5).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    ma240 = close.rolling(240).mean().iloc[-1] if len(close) >= 240 else None

    # RVOL (당일 거래량 / 20일 평균)
    avg_vol = volume.rolling(20).mean().iloc[-1]
    rvol = volume.iloc[-1] / avg_vol if avg_vol > 0 else 0

    # 거래대금 배율 (당일 / 20일 평균)
    amt_mult = None
    if amount is not None and len(amount) >= 20:
        avg_amt = amount.rolling(20).mean().iloc[-1]
        amt_mult = float(amount.iloc[-1] / avg_amt) if avg_amt > 0 else 0

    # 신고가 여부
    is_52w_high = bool(close.iloc[-1] >= close.tail(min(252, len(close))).max())

    today = ohlcv.iloc[-1]
    strong_close = today["close"] >= (today["open"] + today["high"]) / 2

    return {
        "close": float(close.iloc[-1]),
        "ma5": float(ma5), "ma20": float(ma20), "ma60": float(ma60),
        "ma240": float(ma240) if ma240 is not None else None,
        "rvol": round(float(rvol), 2),
        "amount_mult": round(amt_mult, 2) if amt_mult is not None else None,
        "is_52w_high": is_52w_high,
        "strong_close": bool(strong_close),
        "ma60_above_ma240": bool(ma240 is not None and ma60 > ma240),
    }
