"""Wyckoff Spring → Test → SOS 패턴 검출.

Stage 1 매집의 마지막 단계 패턴 — 텐버거 출발의 표준 시그널.

원리:
1. Spring  : 박스 하단을 잠시 이탈 (개미 떨궈내기) 후 5일 내 박스 복귀
2. Test    : Spring 후 저거래량 재테스트 (신저가 무산)
3. SOS     : 강한 반등 + 거래량 폭발 (Sign of Strength)
4. LPS     : 마지막 눌림 (Last Point of Support, 진입 가능)
"""

from __future__ import annotations

import pandas as pd


def detect_spring(daily: pd.DataFrame, accumulation: dict,
                  lookback: int = 30) -> dict:
    """Spring 검출 — 박스 하단 잠시 이탈 후 회복.

    조건:
      - 박스 하단(box_low)을 한 번 이상 하향 이탈
      - 이탈 후 5일 이내 종가가 박스 안으로 복귀
      - 이탈 시점 거래량이 평소보다 높음 (개미 손절 흡수)
    """
    if daily is None or len(daily) < lookback:
        return {"spring": False}
    box_low = accumulation.get("box_low")
    if not box_low or box_low <= 0:
        return {"spring": False}

    win = daily.tail(lookback)
    low = win["low"]
    close = win["close"]
    volume = win["volume"]
    avg_vol = volume.mean()

    # 박스 하단 이탈 후보 일자 (저가 < box_low * 0.97)
    breaches = win[low < box_low * 0.97]
    if breaches.empty:
        return {"spring": False}

    # 가장 최근 이탈 + 5일 내 복귀 확인
    last_breach_idx = breaches.index[-1]
    breach_pos = win.index.get_loc(last_breach_idx)
    if breach_pos + 5 >= len(win):
        # 너무 최근 — 회복 검증 불가
        return {"spring": False, "pending": True}
    after = win.iloc[breach_pos + 1:breach_pos + 6]
    recovered = (after["close"] >= box_low).any()
    breach_vol = float(volume.iloc[breach_pos])
    high_vol = avg_vol > 0 and breach_vol >= avg_vol * 1.3

    return {
        "spring": bool(recovered),
        "high_vol": bool(high_vol),
        "breach_date": str(last_breach_idx.date() if hasattr(
            last_breach_idx, "date") else last_breach_idx),
        "breach_pos_from_end": int(len(win) - breach_pos),
    }


def detect_sos(daily: pd.DataFrame, accumulation: dict,
               lookback: int = 30) -> dict:
    """SOS (Sign of Strength) 검출 — Spring 후 강한 반등.

    조건:
      - 박스 상단(box_high)을 +0.5% 이상 돌파한 일자
      - 돌파 시 거래량 ≥ 20일 평균 * 1.8 (Wyckoff 보수 기준)
      - 종가가 고가 부근에서 마감 (윗꼬리 짧음)
    """
    if daily is None or len(daily) < lookback:
        return {"sos": False}
    box_high = accumulation.get("box_high")
    if not box_high or box_high <= 0:
        return {"sos": False}

    win = daily.tail(lookback)
    close = win["close"]
    high = win["high"]
    open_ = win["open"]
    low = win["low"]
    volume = win["volume"]
    avg_vol_20 = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else float(volume.mean())

    breakout_days = win[close > box_high * 1.005]
    if breakout_days.empty:
        return {"sos": False}

    last_idx = breakout_days.index[-1]
    pos = win.index.get_loc(last_idx)
    bar = win.iloc[pos]
    rng = bar["high"] - bar["low"]
    if rng <= 0: return {"sos": False}
    body_pos = (bar["close"] - bar["low"]) / rng  # 0~1, 1이면 종가 = 고가
    vol_ratio = float(bar["volume"] / avg_vol_20) if avg_vol_20 > 0 else 0

    sos_ok = vol_ratio >= 1.8 and body_pos >= 0.6

    return {
        "sos": bool(sos_ok),
        "breakout_date": str(last_idx.date() if hasattr(last_idx, "date") else last_idx),
        "vol_ratio": round(vol_ratio, 2),
        "body_position": round(body_pos, 2),
        "days_from_end": int(len(win) - pos),
    }


def vsa_effort_result(daily: pd.DataFrame, lookback: int = 5) -> dict:
    """VSA — Effort vs Result 일치도.

    최근 lookback일 거래량(노력)과 가격 변화(결과) 정합성.
    - 큰 거래량 + 약한 가격 = 분산 의심 (-)
    - 큰 거래량 + 강한 가격 = 진짜 매수 (+)
    - 작은 거래량 + 강한 가격 = 매물 소화 끝 (+)
    - 작은 거래량 + 약한 가격 = 매도세 약함 (+)
    """
    if daily is None or len(daily) < lookback + 10:
        return {"vsa_score": 0, "harmful": False}
    win = daily.tail(lookback)
    base_vol = float(daily["volume"].iloc[-(lookback+15):-lookback].mean())
    if base_vol <= 0:
        return {"vsa_score": 0, "harmful": False}

    score = 0
    harmful_days = 0
    for _, row in win.iterrows():
        v_ratio = row["volume"] / base_vol
        price_change = (row["close"] - row["open"]) / row["open"] \
            if row["open"] > 0 else 0
        # 큰 거래량 + 음봉 = 분산 의심
        if v_ratio >= 1.5 and price_change < -0.005:
            harmful_days += 1
            score -= 2
        # 큰 거래량 + 양봉 = 진짜 매수
        elif v_ratio >= 1.5 and price_change > 0.005:
            score += 2
        # 작은 거래량 + 양봉 = 매물 소화 끝
        elif v_ratio <= 0.7 and price_change > 0:
            score += 1
        # 작은 거래량 + 음봉 = 매도세 약함
        elif v_ratio <= 0.7 and price_change < 0:
            score += 1
    return {
        "vsa_score": int(score),
        "harmful_days": int(harmful_days),
        "harmful": harmful_days >= 2,
    }


def wyckoff_pack(daily: pd.DataFrame, accumulation: dict) -> dict:
    """전체 Wyckoff 진단 한 번에."""
    spring = detect_spring(daily, accumulation)
    sos = detect_sos(daily, accumulation)
    vsa = vsa_effort_result(daily)
    return {
        "spring": spring,
        "sos": sos,
        "vsa": vsa,
        "stage_1_to_2_pattern": bool(spring.get("spring") and sos.get("sos")),
    }
