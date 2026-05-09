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
    """B. 수급 강도 — 시총 대비 / 거래대금 대비 — 25점.

    네이버 수급은 '주(수량)' 단위. 금액으로 변환해야 marcap·amount와 비교 가능.
    금액 환산: 수량 × 해당 일자 종가 (근사).
    """
    if supply is None or supply.empty or ohlcv is None or len(ohlcv) < window:
        return 0.0
    recent = supply.tail(window)
    if recent.empty:
        return 0.0
    # 같은 날짜에 종가 매핑 (없으면 마지막 종가로 근사)
    last_close = float(ohlcv["close"].iloc[-1])
    foreign_qty = recent.get("외국인", pd.Series(dtype=float)).sum()
    inst_qty = recent.get("기관합계", pd.Series(dtype=float)).sum()
    net_qty = foreign_qty + inst_qty
    net_value = net_qty * last_close  # 원 단위

    # 시총 대비 (%)
    ratio_mcap = (net_value / marcap) * 100 if marcap else 0
    # 거래대금 대비 (%)
    amount_sum = float(ohlcv["amount"].tail(window).sum())
    ratio_amt = (net_value / amount_sum) * 100 if amount_sum > 0 else 0

    # 정규화: 시총 대비 0.3% 이상이면 시총 만점, 거래대금 대비 15% 이상이면 만점
    s1 = max(0, min(15, ratio_mcap / 0.3 * 15))
    s2 = max(0, min(10, ratio_amt / 15 * 10))
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
                        max_range_pct: float = 40.0) -> dict:
    """매집 단계 판별.

    가장 긴 "박스 안에서 머문 연속 구간"을 매집 기간으로 본다.
    여러 lookback (60/120/250/500일)을 시도해 가장 합리적인 박스를 선택.
    """
    if ohlcv is None or len(ohlcv) < 60:
        return {"in_accumulation": False, "duration": 0, "range_pct": None,
                "box_high": None, "box_low": None, "ma60_slope_pct": 0}

    close = ohlcv["close"]
    high_s = ohlcv["high"]
    low_s = ohlcv["low"]
    n = len(ohlcv)

    # 60일선 기울기 (최근 30일 변화율)
    ma60 = close.rolling(60).mean()
    if len(ma60.dropna()) >= 30:
        slope = (ma60.iloc[-1] - ma60.iloc[-30]) / ma60.iloc[-30] * 100
    else:
        slope = 0.0

    # 여러 lookback 후보 중 박스폭이 좁은 것 선택
    candidates = [60, 120, 250, 500]
    best = None
    for L in candidates:
        if L > n:
            continue
        win_high = high_s.iloc[-L:].max()
        win_low = low_s.iloc[-L:].min()
        mid = (win_high + win_low) / 2
        rng_pct = (win_high - win_low) / mid * 100 if mid > 0 else 999
        if rng_pct <= max_range_pct:
            best = (L, win_high, win_low, rng_pct)
            # 가장 긴 lookback에서도 박스가 통과하면 그게 진짜 매집
            # 계속 looping (가장 긴 lookback 우선)

    if best is None:
        # 박스가 안 잡혀도 박스 돌파 판단용으로 60일 박스를 사용
        h60 = float(high_s.iloc[-60:].max())
        l60 = float(low_s.iloc[-60:].min())
        rng60 = (h60 - l60) / ((h60 + l60) / 2) * 100 if h60 + l60 > 0 else 999
        # 60일 박스 안에 머문 연속 일수
        upper60, lower60 = h60 * 1.05, l60 * 0.95
        dur60 = 0
        for i in range(n - 1, -1, -1):
            c = close.iloc[i]
            if lower60 <= c <= upper60:
                dur60 += 1
            else:
                break
        return {
            "in_accumulation": False,
            "duration": dur60,
            "range_pct": round(rng60, 2),
            "ma60_slope_pct": round(slope, 2),
            "box_high": h60,
            "box_low": l60,
            "lookback_used": 60,
        }

    L, box_high, box_low, range_pct = best
    in_accum = abs(slope) < 15  # MA가 너무 가파르면 매집 아님

    # 매집 기간: 종가가 박스 ±5% 안에 머문 연속 일수 (역순)
    duration = 0
    upper = box_high * 1.05
    lower = box_low * 0.95
    for i in range(n - 1, -1, -1):
        c = close.iloc[i]
        if lower <= c <= upper:
            duration += 1
        else:
            break

    return {
        "in_accumulation": bool(in_accum),
        "duration": duration,
        "range_pct": round(range_pct, 2),
        "ma60_slope_pct": round(slope, 2),
        "box_high": float(box_high),
        "box_low": float(box_low),
        "lookback_used": L,
    }


# ── 차트 지표 ────────────────────────────────────────────

def chart_metrics(ohlcv: pd.DataFrame,
                  market_close: pd.Series | None = None) -> dict:
    """차트 보조 지표 (이평선, RVOL, 신고가, RS, 거래대금 체급)."""
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

    # 거래대금 체급 상승 (60일 평균 / 240일 평균)
    # 1.3 이상이면 매집기 → 출발기 거래량 체급 변화 (Wyckoff)
    amt_trend = None
    if amount is not None and len(amount) >= 240:
        avg60 = amount.iloc[-60:].mean()
        avg240 = amount.iloc[-240:].mean()
        amt_trend = float(avg60 / avg240) if avg240 > 0 else None

    # 장기 RS (시장 대비 120일 수익률 차이)
    rs_120d = None
    if market_close is not None and len(close) >= 120 and len(market_close) >= 120:
        s_ret = (close.iloc[-1] / close.iloc[-120]) - 1
        m_ret = (market_close.iloc[-1] / market_close.iloc[-120]) - 1
        rs_120d = round((s_ret - m_ret) * 100, 2)

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
        "amount_trend": round(amt_trend, 2) if amt_trend is not None else None,
        "rs_120d_pp": rs_120d,
        "is_52w_high": is_52w_high,
        "strong_close": bool(strong_close),
        "ma60_above_ma240": bool(ma240 is not None and ma60 > ma240),
    }
