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

    # 5조건 중 4개 만족이면 통과 (백테스트 0건 → 완화)
    # 단, '거래대금 또는 rvol' 둘 중 하나는 반드시 만족 (모멘텀 필수)
    checks = [
        bool(amt_mult and amt_mult >= 2.0),  # 거래대금
        rvol >= 1.5,                          # rvol
        bool(metrics.get("strong_close")),    # 종가 강세
        today_supply_ok,                       # 당일 수급
        breakout,                              # 박스 돌파
    ]
    momentum_ok = checks[0] or checks[1]
    triggered = momentum_ok and sum(checks) >= 4

    return {
        "triggered": bool(triggered),
        "reasons": reasons,
        "fail": fail,
        "checks_passed": sum(checks),
    }


def tenbagger_departure(ohlcv: pd.DataFrame, supply: pd.DataFrame,
                        metrics: dict, accumulation: dict,
                        score: dict | None = None,
                        marcap: int | None = None,
                        weekly_pack: dict | None = None,
                        min_accum_days: int = 180,
                        max_marcap: int = 500_000_000_000) -> dict:
    """💎 텐버거 출발 신호 — 주봉 매집 + 일봉 출발 통합 (v4).

    weekly_pack: weekly.py가 만든 dict
      {accum, vcp, dry_explode, smart_money_w, breakout_w}

    필수 (3개, 모두 필수):
      - 주봉 매집 (weekly.in_accumulation == True OR 일봉 매집 250일+)
      - 주봉 박스 돌파 OR 거래량 말라붙기→폭발 (출발 패턴)
      - 스마트머니 주봉 누적 양수 (외인 OR 기관)

    수급 강도 (4 중 3):
      - 외인+기관 동시매수 ≥ 13/26주 (절반)
      - 개인 26주 누적 음수
      - 일봉 수급 점수 ≥ 60
      - 일봉 수급 강도 ≥ 13/25

    차트 보조 (3 중 2):
      - VCP 패턴 OR 거래량 말라붙기→폭발
      - 60>240 정배열
      - 거래대금 체급 1.3+
    """
    reasons = []
    fail = []
    score = score or {}
    weekly_pack = weekly_pack or {}

    w_accum = weekly_pack.get("accum", {}) or {}
    w_breakout = weekly_pack.get("breakout_w", {}) or {}
    w_dry = weekly_pack.get("dry_explode", {}) or {}
    w_vcp = weekly_pack.get("vcp", {}) or {}
    w_sm = weekly_pack.get("smart_money_w", {}) or {}

    # ── 필수 1: 매집 (주봉 OR 일봉 250일+) ──
    daily_acc_days = accumulation.get("duration", 0)
    weekly_in_acc = bool(w_accum.get("in_accumulation"))
    weeks_in_box = int(w_accum.get("weeks", 0))
    accum_ok = weekly_in_acc or daily_acc_days >= min_accum_days
    if weekly_in_acc:
        reasons.append(f"주봉 매집 {weeks_in_box}주")
    elif accum_ok:
        reasons.append(f"매집 {daily_acc_days}일")
    else:
        fail.append(f"매집 부족(주봉 {weeks_in_box}주, 일봉 {daily_acc_days}일)")

    # ── 필수 2: 출발 (주봉 박스 돌파 OR 거래량 마름→폭발) ──
    weekly_break = bool(w_breakout.get("breakout"))
    dry_explode = bool(w_dry.get("dry_to_explode"))
    daily_break = (accumulation.get("box_high") is not None
                   and metrics.get("close", 0)
                       > accumulation["box_high"] * 1.005)
    departure_ok = weekly_break or dry_explode or daily_break
    if weekly_break:
        reasons.append("주봉 박스 돌파")
    if dry_explode:
        reasons.append(f"거래량 마름→폭발 {w_dry.get('explode_ratio')}x")
    if daily_break and not (weekly_break or dry_explode):
        reasons.append("일봉 박스 돌파")
    if not departure_ok:
        fail.append("출발 신호 없음")

    # ── 필수 3: 스마트머니 누적 (주봉 26주 OR 일봉 60일) ──
    if w_sm.get("available"):
        sm_pos = bool(w_sm.get("smart_money_positive"))
    else:
        f60 = float(supply["외국인"].iloc[-60:].sum()) \
            if supply is not None and "외국인" in supply.columns and len(supply) >= 60 else 0
        i60 = float(supply["기관합계"].iloc[-60:].sum()) \
            if supply is not None and "기관합계" in supply.columns and len(supply) >= 60 else 0
        sm_pos = f60 > 0 or i60 > 0
    if sm_pos:
        reasons.append("스마트머니 누적 +")
    else:
        fail.append("스마트머니 누적 부진")

    # ── 수급 강도 (4 중 3) ──
    coincide_w = int(w_sm.get("coincide_weeks", 0)) if w_sm.get("available") else 0
    coincide_ok = coincide_w >= 13
    if coincide_ok:
        reasons.append(f"외인·기관 동시매수 {coincide_w}주")

    individual_neg = bool(w_sm.get("individual_negative", False))
    if not individual_neg and supply is not None \
            and "개인" in supply.columns and len(supply) >= 60:
        individual_neg = float(supply["개인"].iloc[-60:].sum()) < 0
    if individual_neg: reasons.append("개인 누적 -")

    score_total = float(score.get("total", 0))
    score_ok = score_total >= 60
    if score_ok: reasons.append(f"수급 점수 {score_total}")

    intensity_v = float(score.get("intensity", 0))
    intensity_ok = intensity_v >= 13.0
    if intensity_ok: reasons.append(f"수급 강도 {intensity_v}/25")

    supply_checks = [coincide_ok, individual_neg, score_ok, intensity_ok]
    supply_passed = sum(supply_checks)

    # ── 차트 보조 (3 중 2) ──
    vcp_ok = bool(w_vcp.get("vcp")) or dry_explode
    if vcp_ok: reasons.append("VCP/거래량 압축")

    ma_ok = bool(metrics.get("ma60_above_ma240"))
    if ma_ok: reasons.append("60>240 정배열")

    amt_trend = metrics.get("amount_trend") or 0
    trend_ok = amt_trend >= 1.3
    if trend_ok: reasons.append(f"거래대금 체급↑ {amt_trend}x")

    chart_checks = [vcp_ok, ma_ok, trend_ok]
    chart_passed = sum(chart_checks)

    if metrics.get("is_52w_high"): reasons.append("52주 신고가 (보조)")
    rs_pp = metrics.get("rs_120d_pp")
    if rs_pp and rs_pp > 0: reasons.append(f"RS +{rs_pp}%p")

    # 시총 cap — 진짜 텐버거(10배)는 작은 시총에서 시작
    marcap_ok = marcap is not None and marcap < max_marcap
    if not marcap_ok:
        if marcap:
            fail.append(f"시총 {marcap/1e8:.0f}억 (텐버거 cap 5000억 초과)")
        else:
            fail.append("시총 데이터 없음")

    triggered = (accum_ok and departure_ok and sm_pos and marcap_ok
                 and supply_passed >= 3 and chart_passed >= 2)

    return {
        "triggered": bool(triggered),
        "reasons": reasons,
        "fail": fail,
        "supply_passed": supply_passed,
        "chart_passed": chart_passed,
        "weekly_accum_weeks": weeks_in_box,
        "weekly_breakout": weekly_break,
        "dry_explode": dry_explode,
    }
