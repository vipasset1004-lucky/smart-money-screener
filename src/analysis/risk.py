"""리스크 신호 엔진 — 무효화/회피 조건.

GPT 설계서의 12번 항목 반영. 매수보다 회피가 더 중요.
"""

from __future__ import annotations

import pandas as pd


def detect_risk_signals(ohlcv: pd.DataFrame, supply: pd.DataFrame | None,
                        accumulation: dict, metrics: dict) -> dict:
    """리스크 신호들. signals: 적발된 리스크 리스트.

    severity:
      - "safe":     리스크 없음
      - "watch":    1~2개 약한 신호
      - "warning":  3개 이상 또는 강한 신호 1개
      - "danger":   다수 또는 치명적 신호
    """
    signals: list[str] = []
    if ohlcv is None or len(ohlcv) < 20:
        return {"signals": [], "severity": "unknown"}

    close = ohlcv["close"]
    open_ = ohlcv["open"]
    high = ohlcv["high"]
    low = ohlcv["low"]
    volume = ohlcv["volume"]

    today = ohlcv.iloc[-1]
    avg_vol_20 = volume.tail(20).mean()
    rvol = volume.iloc[-1] / avg_vol_20 if avg_vol_20 > 0 else 0

    # 1. 거래량 동반 장대음봉 (분산 의심)
    body = abs(today["close"] - today["open"])
    is_bear = today["close"] < today["open"]
    rng = today["high"] - today["low"]
    big_body = rng > 0 and body / rng >= 0.6
    if is_bear and big_body and rvol >= 1.8:
        signals.append("거래량 동반 장대음봉")

    # 2. 신고가 돌파 실패 (최근 5일 내 신고가 만들었는데 다시 박스 안)
    if accumulation.get("box_high"):
        bh = accumulation["box_high"]
        recent5 = close.tail(5)
        was_above = (recent5 > bh * 1.005).any()
        now_below = close.iloc[-1] < bh * 0.99
        if was_above and now_below:
            signals.append("돌파 실패 후 재이탈")

    # 3. 외국인+기관 동시 이탈 (최근 3일)
    if supply is not None and len(supply) >= 3:
        last3 = supply.tail(3)
        f_neg = (last3.get("외국인", pd.Series([0])) < 0).sum()
        i_neg = (last3.get("기관합계", pd.Series([0])) < 0).sum()
        if f_neg >= 3 and i_neg >= 3:
            signals.append("외인·기관 3일 연속 동반 매도")
        elif f_neg >= 2 and i_neg >= 2:
            signals.append("외인·기관 동반 이탈 중")

    # 4. 거래대금 급감 (수급 이탈 신호)
    amount = ohlcv["amount"]
    avg_amt = amount.tail(20).mean()
    recent_amt = amount.tail(3).mean()
    if avg_amt > 0 and recent_amt / avg_amt < 0.5:
        signals.append("거래대금 급감 (-50% 이상)")

    # 5. 20일선 이탈 후 회복 실패
    if len(close) >= 25:
        ma20 = close.rolling(20).mean()
        below_5 = (close.tail(5) < ma20.tail(5)).sum()
        if below_5 >= 4 and close.iloc[-1] < ma20.iloc[-1] * 0.97:
            signals.append("20일선 이탈 회복 실패")

    # 6. 가격은 오르는데 거래량은 감소 (Effort vs Result 불일치 — Wyckoff)
    if len(close) >= 10:
        close5_chg = (close.iloc[-1] / close.iloc[-5] - 1) * 100
        vol5_avg = volume.tail(5).mean()
        vol_prev_avg = volume.iloc[-10:-5].mean()
        if close5_chg > 5 and vol5_avg < vol_prev_avg * 0.7:
            signals.append("가격↑ 거래량↓ 다이버전스")

    # 7. RSI 과열 (단순 14일 RSI)
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - 100 / (1 + rs)
        last_rsi = rsi.iloc[-1]
        if pd.notna(last_rsi):
            if last_rsi >= 80:
                signals.append(f"RSI 과열 ({last_rsi:.0f})")
            elif last_rsi >= 75:
                signals.append(f"RSI 부담권 ({last_rsi:.0f})")

    # 심각도 분류
    if not signals:
        sev = "safe"
    else:
        critical = [s for s in signals
                    if "동반 매도" in s or "장대음봉" in s
                    or "회복 실패" in s]
        if len(signals) >= 3 or len(critical) >= 2:
            sev = "danger"
        elif len(signals) >= 2 or critical:
            sev = "warning"
        else:
            sev = "watch"

    return {"signals": signals, "severity": sev}
