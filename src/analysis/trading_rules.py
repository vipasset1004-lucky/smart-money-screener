"""라벨별 매매 룰 + 박스 기반 Fibonacci 가격 가이드.

각 라벨에 따라 시간 horizon과 risk가 다르니 룰도 차등.
- 단타: 빡빡한 손절, 빠른 익절 (3~7일)
- 텐버거: 분할 익절, 추적매도 (6개월~1.4년)
- 매집중: 관찰만 (출발 대기)

Fibonacci levels는 매집 박스(box_low ~ box_high) anchor:
- Retracement: 박스 안으로 눌림 (23.6/38.2/50/61.8%)
- Extension: 박스 돌파 후 목표가 (100/161.8/261.8%)
"""

from __future__ import annotations


# 라벨별 매매 룰
RULES = {
    "⭐황금자리": {
        "size": "1/2 (큰 비중)",
        "stop_pct": -7.0,
        "tp1_pct": 20.0, "tp1_portion": "1/3 (원금 회수)",
        "tp2_pct": 50.0, "tp2_portion": "1/3",
        "trail": "MA50 이탈 또는 Stage 3 진입",
        "horizon": "장기 (수개월~수년)",
        "note": "단타+텐버거 동시 점등, 가장 강한 신호",
    },
    "💎텐버거": {
        "size": "1/4 × 3~5종목 분산",
        "stop_pct": -7.0,
        "tp1_pct": 20.0, "tp1_portion": "1/3 (원금 회수)",
        "tp2_pct": 50.0, "tp2_portion": "1/3",
        "trail": "MA50 이탈",
        "horizon": "6개월~1.4년",
        "note": "주봉 매집 1년+ 종목, 인내 필요",
    },
    "🏛대가합의": {
        "size": "1/3",
        "stop_pct": -7.0,
        "tp1_pct": 20.0, "tp1_portion": "1/3",
        "tp2_pct": 50.0, "tp2_portion": "1/3",
        "trail": "MA50 이탈",
        "horizon": "중기 (1~6개월)",
        "note": "3명+ 대가 65점 이상 일치",
    },
    "🌅폭발임박": {
        "size": "관찰 → 돌파 확인 후 1/4",
        "stop_pct": -5.0,
        "tp1_pct": 15.0, "tp1_portion": "1/2",
        "tp2_pct": 30.0, "tp2_portion": "추가 1/2",
        "trail": None,
        "horizon": "단기~중기",
        "note": "돌파 캔들 close 확인 후 진입, 고점 회피",
    },
    "🎯VCP": {
        "size": "1/4",
        "stop_pct": -8.0,
        "tp1_pct": 20.0, "tp1_portion": "1/3",
        "tp2_pct": 40.0, "tp2_portion": "추가 1/3",
        "trail": "MA50 이탈",
        "horizon": "중기 (백테스트 60d 75%)",
        "note": "VCP 박스 하단 손절, Minervini 패턴",
    },
    "🛡️안정형": {
        "size": "1/3",
        "stop_pct": -7.0,
        "tp1_pct": 15.0, "tp1_portion": "1/3",
        "tp2_pct": 30.0, "tp2_portion": "추가 1/3",
        "trail": "MA50 이탈",
        "horizon": "중기 (백테스트 60d 76%)",
        "note": "매집 + 점수 60+, 변동성 낮음",
    },
    "⚡단타": {
        "size": "1/4~1/3",
        "stop_pct": -5.0,  # 단타라 빡빡
        "tp1_pct": 7.0, "tp1_portion": "전량",
        "tp2_pct": None, "tp2_portion": None,
        "trail": None,
        "horizon": "3~7일 (7일 내 폭발 못 하면 출구)",
        "note": "기계적 손절·익절, 미루지 말 것",
    },
    "🔍매집중": {
        "size": "관찰만",
        "stop_pct": None,
        "tp1_pct": None, "tp1_portion": None,
        "tp2_pct": None, "tp2_portion": None,
        "trail": None,
        "horizon": "출발 신호 대기",
        "note": "아직 진입 아님 — 박스 돌파 또는 출발 신호 기다림",
    },
}


# 라벨 우선순위 (위에서 아래로) — 가장 강한 라벨 룰 적용
PRIORITY = [
    "⭐황금자리", "💎텐버거", "🏛대가합의", "🌅폭발임박",
    "🎯VCP", "🛡️안정형", "⚡단타", "🔍매집중",
]


def get_primary_rule(labels: list[str]) -> tuple[dict | None, str | None]:
    """라벨 리스트에서 적용할 매매 룰 선택 (우선순위 기준)."""
    if not labels:
        return None, None
    for p in PRIORITY:
        if p in labels:
            return RULES.get(p), p
    return None, None


def fibonacci_levels(box_low: float | None, box_high: float | None,
                     current: float | None) -> dict | None:
    """박스 기반 Fibonacci levels 계산.

    Args:
        box_low: 매집 박스 저점
        box_high: 매집 박스 고점
        current: 현재가

    Returns dict with:
        - retracement: {23.6%, 38.2%, 50%, 61.8%} — 박스 안으로 눌림 가격
        - extension:   {100%, 161.8%, 261.8%, 423.6%} — 돌파 후 목표가
        - distance:    현재가 vs 각 레벨 % 거리
    """
    if box_low is None or box_high is None or box_high <= box_low:
        return None

    box_range = box_high - box_low

    # Retracement (위에서 박스 안으로)
    retr = {
        "0.236": box_high - box_range * 0.236,
        "0.382": box_high - box_range * 0.382,
        "0.500": box_high - box_range * 0.500,
        "0.618": box_high - box_range * 0.618,
        "0.786": box_high - box_range * 0.786,
    }

    # Extension (돌파 후 목표가)
    ext = {
        "1.000": box_high + box_range,                # +100% (박스 측정 이론)
        "1.272": box_high + box_range * 1.272,
        "1.618": box_high + box_range * 1.618,        # 황금비
        "2.618": box_high + box_range * 2.618,        # 텐버거 1차
        "4.236": box_high + box_range * 4.236,        # 본격 텐버거
    }

    out = {
        "box_low": round(box_low, 1),
        "box_high": round(box_high, 1),
        "box_range": round(box_range, 1),
        "current": round(current, 1) if current else None,
        "retracement": {k: round(v, 1) for k, v in retr.items()},
        "extension": {k: round(v, 1) for k, v in ext.items()},
    }

    # 현재가 위치 분석
    if current is not None:
        if current > box_high:
            # 돌파 후 — extension 어느 정도 도달?
            up_pct = (current - box_high) / box_range * 100
            out["breakout_pct"] = round(up_pct, 1)
            out["zone"] = "BREAKOUT"
            # 다음 목표
            for level_str, level_val in ext.items():
                if current < level_val:
                    out["next_target"] = level_val
                    out["next_target_label"] = f"{float(level_str)*100:.1f}% ext"
                    out["next_target_gain_pct"] = round(
                        (level_val - current) / current * 100, 1)
                    break
        elif current >= box_low:
            # 박스 안 — retracement 어느 정도?
            down_pct = (box_high - current) / box_range * 100
            out["retracement_pct"] = round(down_pct, 1)
            out["zone"] = "IN_BOX"
        else:
            # 박스 아래 — 박스 깨짐
            out["zone"] = "BELOW_BOX"

    return out


def compute_trading_guide(labels: list[str],
                          accumulation: dict | None,
                          metrics: dict | None) -> dict | None:
    """라벨 + 박스 + 현재가 → 종합 매매 가이드.

    Returns: {
        "rule": {...},          # 라벨별 매매 룰
        "rule_label": "⭐황금자리",
        "fib": {...},           # Fibonacci levels
        "computed": {           # 현재가 기준 실제 가격
            "stop_price": 977.0,
            "tp1_price": 1260.0,
            "tp2_price": 1575.0,
        }
    } or None
    """
    rule, rule_label = get_primary_rule(labels)
    if not rule:
        return None

    box_low = (accumulation or {}).get("box_low")
    box_high = (accumulation or {}).get("box_high")
    current = (metrics or {}).get("close")
    fib = fibonacci_levels(box_low, box_high, current)

    # 현재가 기준 실제 가격 계산 (% → 원)
    computed = {}
    if current is not None and rule.get("stop_pct") is not None:
        computed["stop_price"] = round(current * (1 + rule["stop_pct"] / 100), 1)
    if current is not None and rule.get("tp1_pct") is not None:
        computed["tp1_price"] = round(current * (1 + rule["tp1_pct"] / 100), 1)
    if current is not None and rule.get("tp2_pct") is not None:
        computed["tp2_price"] = round(current * (1 + rule["tp2_pct"] / 100), 1)

    return {
        "rule": rule,
        "rule_label": rule_label,
        "fib": fib,
        "computed": computed if computed else None,
    }
