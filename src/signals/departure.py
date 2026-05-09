"""출발 신호 (단타 / 텐버거) 게이트.

ALGORITHM.md [4단계]에 정의된 조건을 그대로 구현.
"""

from __future__ import annotations

import pandas as pd


def short_term_departure(ohlcv: pd.DataFrame, supply: pd.DataFrame,
                         metrics: dict, accumulation: dict) -> dict:
    """⚡ 단타 출발 신호 (7일 내 폭발 가능성).

    조건:
      - 거래대금 ≥ 평균 * 3.0
      - 외국인 OR 기관 순매수 전환 (당일 양수)
      - 종가 강세 (종가 ≥ (시가+고가)/2)
      - 박스권 상단 돌파 (또는 매집 끝물 돌파)
      - rvol ≥ 2.0
    """
    reasons = []
    fail = []

    # 거래대금 폭발 (백테스트 후 완화: 3.0 → 2.0)
    amt_mult = metrics.get("amount_mult")
    if amt_mult and amt_mult >= 2.0:
        reasons.append(f"거래대금 {amt_mult:.1f}배")
    else:
        fail.append("거래대금 부족")

    # rvol (백테스트 후 완화: 2.0 → 1.5)
    rvol = metrics.get("rvol", 0)
    if rvol >= 1.5:
        reasons.append(f"rvol {rvol}")
    else:
        fail.append("rvol 부족")

    # 종가 강세
    if metrics.get("strong_close"):
        reasons.append("종가 강세")
    else:
        fail.append("약한 마감")

    # 수급 전환 (당일 외인 OR 기관 양수)
    today_supply_ok = False
    if supply is not None and not supply.empty:
        last = supply.iloc[-1]
        if last.get("외국인", 0) > 0 or last.get("기관합계", 0) > 0:
            today_supply_ok = True
            reasons.append("당일 수급 진입")
    if not today_supply_ok:
        fail.append("당일 수급 없음")

    # 박스 상단 돌파
    box_high = accumulation.get("box_high")
    close = metrics.get("close", 0)
    breakout = box_high is not None and close > box_high * 1.005
    if breakout:
        reasons.append("박스 돌파")
    else:
        fail.append("박스 미돌파")

    triggered = (
        amt_mult and amt_mult >= 2.0
        and rvol >= 1.5
        and metrics.get("strong_close")
        and today_supply_ok
        and breakout
    )

    return {
        "triggered": bool(triggered),
        "reasons": reasons,
        "fail": fail,
    }


def tenbagger_departure(ohlcv: pd.DataFrame, supply: pd.DataFrame,
                        metrics: dict, accumulation: dict,
                        min_accum_days: int = 180) -> dict:
    """💎 텐버거 출발 신호 (Stage 1 → Stage 2 전환).

    조건:
      - 매집 기간 ≥ 250일 (1년 이상)
      - 박스권 상단 돌파
      - 거래량 ≥ 평균 * 2.0
      - 60일선 > 240일선 (장기 정배열)
      - 52주 신고가 갱신
      - 외국인 60일 누적 순매수 양수
    """
    reasons = []
    fail = []

    duration = accumulation.get("duration", 0)
    if duration >= min_accum_days:
        reasons.append(f"매집 {duration}일")
    else:
        fail.append(f"매집 부족({duration}일)")

    # 박스 돌파
    box_high = accumulation.get("box_high")
    close = metrics.get("close", 0)
    breakout = box_high is not None and close > box_high * 1.005
    if breakout:
        reasons.append("장기박스 돌파")
    else:
        fail.append("박스 미돌파")

    # 거래량 폭발 (백테스트 후 완화: 2.0 → 1.5)
    rvol = metrics.get("rvol", 0)
    if rvol >= 1.5:
        reasons.append(f"거래량 폭발 rvol {rvol}")
    else:
        fail.append("거래량 부족")

    # 장기 정배열
    if metrics.get("ma60_above_ma240"):
        reasons.append("60>240 정배열")
    else:
        fail.append("정배열 아님")

    # 52주 신고가
    if metrics.get("is_52w_high"):
        reasons.append("52주 신고가")
    else:
        fail.append("신고가 아님")

    # 외국인 60일 누적 양수
    foreign_ok = False
    if supply is not None and "외국인" in supply.columns and len(supply) >= 60:
        if supply["외국인"].iloc[-60:].sum() > 0:
            foreign_ok = True
            reasons.append("외인 60일 누적 +")
    if not foreign_ok:
        fail.append("외인 누적 부진")

    triggered = (
        duration >= min_accum_days
        and breakout
        and rvol >= 1.5
        and metrics.get("ma60_above_ma240")
        and metrics.get("is_52w_high")
        and foreign_ok
    )

    return {
        "triggered": bool(triggered),
        "reasons": reasons,
        "fail": fail,
    }
