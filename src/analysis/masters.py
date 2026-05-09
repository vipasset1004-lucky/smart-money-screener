"""대가별 조건 충족률 — 상세 카드용.

각 대가의 핵심 조건을 단순화해서 충족 여부 체크.
research/theory/*.md 의 정의에 매칭.
"""

from __future__ import annotations

import pandas as pd


def _ma(close: pd.Series, n: int) -> float | None:
    if len(close) < n:
        return None
    return float(close.tail(n).mean())


def _slope(series: pd.Series, n: int = 30) -> float:
    if len(series.dropna()) < n:
        return 0.0
    s = series.dropna()
    if s.iloc[-n] == 0:
        return 0.0
    return float((s.iloc[-1] - s.iloc[-n]) / abs(s.iloc[-n]) * 100)


def evaluate_oneil(ohlcv: pd.DataFrame, supply: pd.DataFrame | None,
                   metrics: dict, score: dict) -> dict:
    """William O'Neil CAN SLIM (간소화 — 한국 시장 적용 가능 항목).

    구현 가능한 5개 (실적 데이터 없는 상태):
      I: Institutional Sponsorship — 기관/외국인 누적 양수
      N: New high                  — 52주 신고가
      M: Market direction (단순)    — 60>240 정배열
      Supply intensity             — 시총 대비 비율 양호
      Pivot                        — 박스 상단 돌파 (단타_출발 비슷)
    """
    items = []
    close = ohlcv["close"]

    # I — 기관·외국인 누적 60일 양수
    inst_ok = False
    if supply is not None and len(supply) >= 60:
        inst_60 = supply.get("기관합계", pd.Series([0])).iloc[-60:].sum()
        for_60 = supply.get("외국인", pd.Series([0])).iloc[-60:].sum()
        if inst_60 > 0 and for_60 > 0:
            inst_ok = True
    items.append({"key": "I", "label": "기관 후원 (외인+기관 60일 +)",
                  "ok": inst_ok})

    # N — 52주 신고가
    items.append({"key": "N", "label": "52주 신고가권",
                  "ok": bool(metrics.get("is_52w_high"))})

    # M — 시장 방향 (간이): 60>240 정배열
    items.append({"key": "M", "label": "장기 정배열 (60>240)",
                  "ok": bool(metrics.get("ma60_above_ma240"))})

    # Supply intensity
    items.append({"key": "S", "label": "수급 강도 양호 (intensity≥10)",
                  "ok": score["intensity"] >= 10})

    # Pivot (강한 마감 + 거래량)
    items.append({"key": "L", "label": "단기 모멘텀 (rvol≥1.5 강한 마감)",
                  "ok": (metrics.get("rvol", 0) >= 1.5
                         and bool(metrics.get("strong_close")))})

    fulfilled = sum(1 for x in items if x["ok"])
    return {
        "name": "오닐 CAN SLIM",
        "items": items,
        "fulfilled": fulfilled,
        "total": len(items),
        "percent": round(fulfilled / len(items) * 100),
    }


def evaluate_minervini(ohlcv: pd.DataFrame, metrics: dict) -> dict:
    """Mark Minervini Trend Template (8조건)."""
    close = ohlcv["close"]
    last = float(close.iloc[-1])

    ma50 = _ma(close, 50)
    ma150 = _ma(close, 150)
    ma200 = _ma(close, 200)
    ma200_slope = _slope(close.rolling(200).mean(), 30) \
        if len(close) >= 230 else 0

    high_52w = float(close.tail(min(252, len(close))).max())
    low_52w = float(close.tail(min(252, len(close))).min())

    items = [
        {"label": "현재가 > 150일선 > 200일선",
         "ok": bool(ma150 and ma200 and last > ma150 > ma200)},
        {"label": "200일선 상승 중",
         "ok": ma200_slope > 0},
        {"label": "50일선 > 150일선 > 200일선",
         "ok": bool(ma50 and ma150 and ma200 and ma50 > ma150 > ma200)},
        {"label": "현재가 > 50일선",
         "ok": bool(ma50 and last > ma50)},
        {"label": "52주 저가 대비 +30%↑",
         "ok": low_52w > 0 and last >= low_52w * 1.30},
        {"label": "52주 고가의 -25% 이내",
         "ok": high_52w > 0 and last >= high_52w * 0.75},
        {"label": "52주 고가의 -15% 이내 (강함)",
         "ok": high_52w > 0 and last >= high_52w * 0.85},
        {"label": "장기 정배열 (60>240)",
         "ok": bool(metrics.get("ma60_above_ma240"))},
    ]
    fulfilled = sum(1 for x in items if x["ok"])
    return {
        "name": "미너비니 Trend Template",
        "items": items,
        "fulfilled": fulfilled,
        "total": len(items),
        "percent": round(fulfilled / len(items) * 100),
    }


def evaluate_weinstein(ohlcv: pd.DataFrame, metrics: dict,
                       accumulation: dict) -> dict:
    """Stan Weinstein Stage Analysis."""
    close = ohlcv["close"]
    last = float(close.iloc[-1])

    ma150 = _ma(close, 150)
    ma150_slope = _slope(close.rolling(150).mean(), 30) \
        if len(close) >= 180 else 0

    is_52w_high = metrics.get("is_52w_high", False)
    in_accum = accumulation.get("in_accumulation", False)

    if ma150 and last > ma150 and ma150_slope > 0.5 and is_52w_high:
        stage = "Stage 2 (상승 추세)"
        ok = True
    elif ma150 and last > ma150 and ma150_slope > -0.5:
        stage = "Stage 1→2 전환 가능"
        ok = True
    elif in_accum and ma150_slope > -1:
        stage = "Stage 1 (매집 베이스)"
        ok = False  # 아직 매수 신호 아님
    elif ma150 and last < ma150 and ma150_slope < -0.5:
        stage = "Stage 4 (하락 추세)"
        ok = False
    else:
        stage = "Stage 3 (분산 의심) 또는 전환 중"
        ok = False

    return {
        "name": "와인스타인 Stage",
        "stage": stage,
        "ok": ok,
        "ma150_slope_pct": round(ma150_slope, 2),
        "items": [
            {"label": "현재가 > 150일선", "ok": bool(ma150 and last > ma150)},
            {"label": "150일선 상승 중", "ok": ma150_slope > 0.5},
            {"label": "52주 신고가권", "ok": is_52w_high},
        ],
    }


def evaluate_wyckoff(ohlcv: pd.DataFrame, accumulation: dict,
                     metrics: dict, supply: pd.DataFrame | None) -> dict:
    """Wyckoff 매집/분산 단계."""
    close = ohlcv["close"]
    duration = accumulation.get("duration", 0)
    in_accum = accumulation.get("in_accumulation", False)
    range_pct = accumulation.get("range_pct", 0)
    rvol = metrics.get("rvol", 0)
    is_52w_high = metrics.get("is_52w_high", False)

    if is_52w_high and rvol >= 1.5:
        phase = "Markup 진행 (마크업)"
    elif in_accum and duration >= 60 and rvol >= 1.8:
        phase = "SOS 의심 (Sign of Strength)"
    elif in_accum:
        phase = f"Accumulation (매집, {duration}일)"
    elif range_pct and range_pct > 50:
        phase = "Trend 진행 중 (박스 아님)"
    else:
        phase = "판별 곤란"

    items = [
        {"label": "박스권 횡보 (range≤40%)",
         "ok": bool(range_pct and range_pct <= 40)},
        {"label": "매집 60일+", "ok": duration >= 60},
        {"label": "거래량 폭발 (rvol≥1.8)", "ok": rvol >= 1.8},
        {"label": "외인 60일 누적 +",
         "ok": (supply is not None
                and "외국인" in supply.columns
                and len(supply) >= 60
                and supply["외국인"].iloc[-60:].sum() > 0)},
    ]
    fulfilled = sum(1 for x in items if x["ok"])
    return {
        "name": "와이코프 단계",
        "phase": phase,
        "duration": duration,
        "items": items,
        "fulfilled": fulfilled,
        "total": len(items),
        "percent": round(fulfilled / len(items) * 100),
    }


def evaluate_livermore(metrics: dict, accumulation: dict) -> dict:
    """Jesse Livermore Pivotal Point."""
    box_high = accumulation.get("box_high")
    close = metrics.get("close", 0)
    rvol = metrics.get("rvol", 0)
    strong = metrics.get("strong_close", False)
    is_52w = metrics.get("is_52w_high", False)

    breakout = box_high and close > box_high * 1.005
    items = [
        {"label": "박스 상단 돌파", "ok": bool(breakout)},
        {"label": "거래량 동반 (rvol≥1.5)", "ok": rvol >= 1.5},
        {"label": "강한 마감 (윗꼬리 짧음)", "ok": bool(strong)},
        {"label": "신고가 (52주)", "ok": bool(is_52w)},
    ]
    fulfilled = sum(1 for x in items if x["ok"])
    if fulfilled == len(items):
        pivot_type = "Reversal Pivot (텐버거 출발 가능)"
    elif fulfilled >= 2:
        pivot_type = "Continuation Pivot (단타 가능)"
    else:
        pivot_type = "피벗 미형성"
    return {
        "name": "리버모어 Pivot",
        "type": pivot_type,
        "items": items,
        "fulfilled": fulfilled,
        "total": len(items),
        "percent": round(fulfilled / len(items) * 100),
    }


def evaluate_all(ohlcv: pd.DataFrame, supply: pd.DataFrame | None,
                 score: dict, accumulation: dict, metrics: dict) -> dict:
    """모든 대가 평가 한꺼번에 — 상세 카드용 dict."""
    return {
        "oneil": evaluate_oneil(ohlcv, supply, metrics, score),
        "minervini": evaluate_minervini(ohlcv, metrics),
        "weinstein": evaluate_weinstein(ohlcv, metrics, accumulation),
        "wyckoff": evaluate_wyckoff(ohlcv, accumulation, metrics, supply),
        "livermore": evaluate_livermore(metrics, accumulation),
    }
