"""주봉 분석 — 텐버거 매집 검출의 핵심.

일봉 데이터를 주봉으로 변환하고, 주봉 기준으로 매집 단계·VCP·다이버전스
같은 장기 패턴을 검출한다.

참고: vipasset1004-lucky/divergence-screener (주봉 다이버전스 스크리너)의
핵심 패턴 5개를 한국 시장에 맞게 재구현.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame | None:
    """일봉 OHLCV → 주봉 (W-FRI 기준).

    open: 주의 첫 거래일 시가
    high/low: 주 중 최고/최저
    close: 주의 마지막 거래일 종가 (보통 금)
    volume: 주 합산
    amount: 주 합산
    """
    if daily is None or len(daily) < 7:
        return None
    if not isinstance(daily.index, pd.DatetimeIndex):
        try:
            daily = daily.copy()
            daily.index = pd.to_datetime(daily.index)
        except Exception:
            return None
    rule = "W-FRI"
    agg = {
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }
    if "amount" in daily.columns:
        agg["amount"] = "sum"
    weekly = daily.resample(rule).agg(agg)
    weekly = weekly.dropna(subset=["close"])
    return weekly


def to_weekly_supply(daily_supply: pd.DataFrame | None) -> pd.DataFrame | None:
    """수급(외인/기관/개인)을 주봉으로 합산."""
    if daily_supply is None or daily_supply.empty:
        return None
    if not isinstance(daily_supply.index, pd.DatetimeIndex):
        try:
            daily_supply = daily_supply.copy()
            daily_supply.index = pd.to_datetime(daily_supply.index)
        except Exception:
            return None
    weekly = daily_supply.resample("W-FRI").sum()
    return weekly[weekly.abs().sum(axis=1) > 0]


def detect_weekly_accumulation(weekly: pd.DataFrame,
                               min_weeks: int = 26) -> dict:
    """주봉 매집 검출 — Wyckoff Stage 1.

    조건:
      - 가격이 박스권 (range ≤ 35%)
      - 박스권 기간이 최소 min_weeks 주
      - 20주(100일) 이평선 평탄 또는 약상승
    """
    if weekly is None or len(weekly) < min_weeks:
        return {"in_accumulation": False, "weeks": 0, "range_pct": None,
                "box_high": None, "box_low": None}

    candidates = [26, 52, 78, 104]
    best = None
    for L in candidates:
        if L > len(weekly):
            continue
        win = weekly.tail(L)
        h, l = win["high"].max(), win["low"].min()
        mid = (h + l) / 2
        rng = (h - l) / mid * 100 if mid > 0 else 999
        if rng <= 35:
            best = (L, h, l, rng)

    ma20w_slope = 0.0
    if len(weekly) >= 30:
        ma20w = weekly["close"].rolling(20).mean()
        if len(ma20w.dropna()) >= 10:
            ma20w_slope = float((ma20w.iloc[-1] - ma20w.iloc[-10])
                                / abs(ma20w.iloc[-10]) * 100)

    if best is None:
        return {"in_accumulation": False, "weeks": 0, "range_pct": None,
                "box_high": float(weekly["high"].tail(26).max()),
                "box_low": float(weekly["low"].tail(26).min()),
                "ma20w_slope_pct": round(ma20w_slope, 2)}

    L, box_high, box_low, range_pct = best
    # 매집 = 박스 + 20주선 평탄 또는 약상승
    in_accum = abs(ma20w_slope) < 15

    # 매집 주(週) 수
    weeks_in_box = 0
    upper, lower = box_high * 1.05, box_low * 0.95
    for i in range(len(weekly) - 1, -1, -1):
        c = weekly["close"].iloc[i]
        if lower <= c <= upper:
            weeks_in_box += 1
        else:
            break

    return {
        "in_accumulation": bool(in_accum),
        "weeks": weeks_in_box,
        "range_pct": round(range_pct, 2),
        "box_high": float(box_high),
        "box_low": float(box_low),
        "lookback_weeks_used": L,
        "ma20w_slope_pct": round(ma20w_slope, 2),
    }


def detect_vcp(weekly: pd.DataFrame, n_segments: int = 4) -> dict:
    """VCP (Volatility Contraction Pattern, Minervini).

    최근 n_segments 구간(각 ~6주)으로 나누어 변동폭이 점진 감소하는지.
    거래량도 함께 마르면 출발 임박.
    """
    if weekly is None or len(weekly) < n_segments * 4:
        return {"vcp": False}

    seg_len = max(4, len(weekly) // n_segments // 2)
    use = weekly.tail(seg_len * n_segments)
    segments = [use.iloc[i*seg_len:(i+1)*seg_len] for i in range(n_segments)]

    widths = []
    vols = []
    for s in segments:
        if s.empty: continue
        h, l = s["high"].max(), s["low"].min()
        widths.append((h - l) / ((h + l) / 2) * 100 if h + l > 0 else 0)
        vols.append(s["volume"].mean())

    if len(widths) < 3:
        return {"vcp": False}

    contraction = all(widths[i] >= widths[i+1] * 0.85
                      for i in range(len(widths) - 1))
    vol_dried = vols[-1] < np.mean(vols[:-1]) * 0.8
    return {
        "vcp": bool(contraction and vol_dried),
        "widths_pct": [round(w, 2) for w in widths],
        "vol_dried": bool(vol_dried),
    }


def detect_volume_dry_explode(weekly: pd.DataFrame) -> dict:
    """거래량 말라붙기 → 폭발 (매집 완료의 전형).

    최근 4주 거래량이 마지막 1주 폭발 + 직전 평균보다 50% 이상 감소.
    """
    if weekly is None or len(weekly) < 12:
        return {"dry_to_explode": False}
    last_vol = weekly["volume"].iloc[-1]
    prior4 = weekly["volume"].iloc[-5:-1].mean()
    base_avg = weekly["volume"].iloc[-12:-5].mean()
    if base_avg <= 0 or prior4 <= 0:
        return {"dry_to_explode": False}
    dried = prior4 < base_avg * 0.7
    explode = last_vol > prior4 * 2.0
    return {
        "dry_to_explode": bool(dried and explode),
        "dried_ratio": round(prior4 / base_avg, 2),
        "explode_ratio": round(last_vol / prior4, 2),
    }


def smart_money_weekly(weekly_supply: pd.DataFrame | None,
                        weeks: int = 26) -> dict:
    """스마트머니 주봉 누적 분석 — 텐버거 핵심 수급.

    - 외국인 N주 누적
    - 기관 N주 누적
    - 개인 N주 누적 (이전 신호)
    - 외인+기관 동시매수 주(週) 수 / N
    - 누적 매수 주 수 (외인 양수 주 비율)
    """
    if weekly_supply is None or len(weekly_supply) < min(weeks // 2, 10):
        return {"available": False}
    n = min(weeks, len(weekly_supply))
    last = weekly_supply.iloc[-n:]
    f = last.get("외국인", pd.Series([0])).fillna(0)
    i = last.get("기관합계", pd.Series([0])).fillna(0)
    p = last.get("개인", pd.Series([0])).fillna(0)
    return {
        "available": True,
        "weeks_analyzed": n,
        "foreign_sum": float(f.sum()),
        "inst_sum": float(i.sum()),
        "individual_sum": float(p.sum()),
        "foreign_buy_weeks": int((f > 0).sum()),
        "inst_buy_weeks": int((i > 0).sum()),
        "coincide_weeks": int(((f > 0) & (i > 0)).sum()),
        "smart_money_positive": bool(f.sum() > 0 or i.sum() > 0),
        "individual_negative": bool(p.sum() < 0),
    }


def weekly_breakout(weekly: pd.DataFrame, weekly_accum: dict) -> dict:
    """주봉 박스 돌파 — Stage 1 → Stage 2 전환의 핵심.

    조건:
      - 마지막 주봉 종가 > 박스 상단 * 1.005
      - 마지막 주봉 거래량 ≥ 26주 평균 * 2.0
    """
    if weekly is None or len(weekly) < 26:
        return {"breakout": False}
    box_high = weekly_accum.get("box_high")
    if not box_high:
        return {"breakout": False}
    last_close = float(weekly["close"].iloc[-1])
    last_vol = float(weekly["volume"].iloc[-1])
    avg_vol_26w = float(weekly["volume"].tail(26).mean())
    breakout = last_close > box_high * 1.005
    vol_ok = avg_vol_26w > 0 and last_vol >= avg_vol_26w * 2.0
    return {
        "breakout": bool(breakout and vol_ok),
        "price_breakout": bool(breakout),
        "vol_breakout": bool(vol_ok),
        "vol_ratio": round(last_vol / avg_vol_26w, 2) if avg_vol_26w > 0 else 0,
    }
