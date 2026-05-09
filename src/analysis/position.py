"""수급 위치 분류 — 종목이 지금 어디에 있는가.

6단계: 초기매집 / 수급압축 / 돌파직전 / 돌파진행 / 신고가유지 / 과열·분산
"""

from __future__ import annotations

import pandas as pd


def classify_position(ohlcv: pd.DataFrame, supply: pd.DataFrame | None,
                      accumulation: dict, metrics: dict,
                      score_total: float,
                      short_term_triggered: bool,
                      tenbagger_triggered: bool,
                      risk: dict) -> dict:
    """수급 위치 분류 (1개의 주 라벨)."""
    if ohlcv is None or len(ohlcv) < 20:
        return {"label": "데이터부족", "color": "muted", "desc": ""}

    close = ohlcv["close"]
    volume = ohlcv["volume"]
    high = ohlcv["high"]

    in_accum = accumulation.get("in_accumulation", False)
    duration = accumulation.get("duration", 0)
    box_high = accumulation.get("box_high")
    last = float(close.iloc[-1])

    is_52w_high = metrics.get("is_52w_high", False)

    # 거래량 변화 (최근 5일 vs 직전 15일)
    avg_15 = volume.iloc[-20:-5].mean() if len(volume) >= 20 else volume.mean()
    avg_5 = volume.tail(5).mean()
    vol_dried = avg_15 > 0 and avg_5 < avg_15 * 0.7

    # 박스 내 위치 (0~1)
    box_low = accumulation.get("box_low", last)
    if box_high and box_high > box_low:
        in_box_pos = (last - box_low) / (box_high - box_low)
    else:
        in_box_pos = 0.5
    near_top = in_box_pos >= 0.85

    # 분류 우선순위 (위에서부터)
    severity = risk.get("severity", "safe")

    if severity in ("danger", "warning") and "동반 매도" in " ".join(risk.get("signals", [])):
        return {"label": "분산", "color": "danger",
                "desc": "외인·기관 매도 — 매물 출회 가능"}
    if severity == "danger":
        return {"label": "위험", "color": "danger",
                "desc": "다수 리스크 신호"}

    if short_term_triggered or tenbagger_triggered:
        return {"label": "돌파진행", "color": "good",
                "desc": "출발 신호 점등 — 진입 검토"}

    # 신고가 유지 (52주 신고가이면서 거래량 정상)
    if is_52w_high and not vol_dried:
        return {"label": "신고가유지", "color": "good",
                "desc": "52주 신고가권에서 거래량 동반"}

    # 돌파 직전
    if box_high and near_top and score_total >= 50:
        return {"label": "돌파직전", "color": "alert",
                "desc": f"박스 상단 근접 ({in_box_pos*100:.0f}%)"}

    # 수급 압축
    if in_accum and vol_dried and score_total >= 40:
        return {"label": "수급압축", "color": "warn",
                "desc": "거래량 마름 + 가격 유지 — 폭발 직전 가능"}

    # 초기 매집
    if in_accum and duration < 90:
        return {"label": "초기매집", "color": "watch",
                "desc": f"매집 시작 단계 ({duration}일)"}

    if in_accum:
        return {"label": "매집중", "color": "watch",
                "desc": f"매집 진행 중 ({duration}일)"}

    return {"label": "관망", "color": "muted",
            "desc": "특이 신호 없음"}
