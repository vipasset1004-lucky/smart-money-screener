"""대가별 0~100 점수 + 앙상블 통합.

Wyckoff / Minervini / Weinstein / O'Neil / Livermore / Korean
6명 점수를 각각 0~100으로 산출 후 가중 평균 → 통합 점수.
"""

from __future__ import annotations

import pandas as pd


def wyckoff_score(accumulation: dict, wyckoff_pack: dict | None,
                  weekly_pack: dict | None,
                  metrics: dict, score_dict: dict) -> dict:
    """Wyckoff 점수 0~100.
      - 매집 단계 진행도 (30점)
      - Spring 검출 (15점)
      - SOS 검출 (20점)
      - VSA 정합 (10점)
      - Cause & Effect (매집 기간 길이) (15점)
      - 거래대금 체급 변화 (10점)
    """
    pts = 0
    items = []
    in_acc = bool(accumulation.get("in_accumulation"))
    duration = int(accumulation.get("duration") or 0)
    if in_acc:
        pts += 30; items.append("매집 단계 ON")
    elif duration >= 60:
        pts += 15; items.append(f"매집 진행 {duration}일")

    wp = wyckoff_pack or {}
    if wp.get("spring", {}).get("spring"):
        pts += 15; items.append("Spring 검출")
    if wp.get("sos", {}).get("sos"):
        pts += 20; items.append("SOS 검출")
    vsa = wp.get("vsa", {})
    if vsa.get("vsa_score", 0) >= 3:
        pts += 10; items.append(f"VSA +{vsa['vsa_score']}")
    elif vsa.get("harmful"):
        pts -= 10; items.append("VSA 분산 의심")

    # Cause & Effect — 긴 매집일수록 큰 마크업
    if duration >= 250:
        pts += 15; items.append(f"장기 매집 {duration}일")
    elif duration >= 150:
        pts += 10; items.append(f"중기 매집 {duration}일")
    elif duration >= 60:
        pts += 5; items.append("단기 매집")

    amt_trend = metrics.get("amount_trend") or 0
    if amt_trend >= 1.5:
        pts += 10; items.append(f"거래대금 체급↑ {amt_trend}x")
    elif amt_trend >= 1.2:
        pts += 5; items.append(f"거래대금 약↑ {amt_trend}x")

    pts = max(0, min(100, pts))
    return {"score": pts, "items": items}


def minervini_score(metrics: dict, vcp_pack: dict | None,
                    accumulation: dict) -> dict:
    """Minervini 점수 0~100.
      - Trend Template 충족도 (50점)
      - VCP 점수 (50점)
    """
    pts = 0
    items = []
    close = metrics.get("close", 0)
    ma60 = metrics.get("ma60") or 0
    ma240 = metrics.get("ma240") or 0
    is_52w_high = bool(metrics.get("is_52w_high"))

    # Trend Template (간소화 5개)
    tt_checks = []
    if ma240 and ma60 and close > ma60: tt_checks.append("close>MA60")
    if ma240 and ma60 > ma240: tt_checks.append("MA60>MA240")
    if is_52w_high: tt_checks.append("52주 신고가")
    if ma240 and close >= ma240 * 0.85: tt_checks.append("MA240 -15% 이내")
    if accumulation.get("in_accumulation"): tt_checks.append("매집 ON")
    pts += len(tt_checks) * 10  # 5개 * 10 = 50점
    items.extend(tt_checks)

    # VCP score
    vcp = vcp_pack or {}
    vcp_s = float(vcp.get("vcp_score", 0))
    pts += vcp_s * 0.5  # 100 * 0.5 = 50점
    if vcp_s >= 60:
        items.append(f"VCP {vcp_s}")

    pts = max(0, min(100, round(pts, 1)))
    return {"score": pts, "items": items}


def weinstein_score(metrics: dict, accumulation: dict,
                    mansfield: dict | None) -> dict:
    """Weinstein 점수 0~100.
      - Stage 분류 (40점)
      - Mansfield RS 양수 (30점)
      - 양수 전환 보너스 (30점)
    """
    pts = 0
    items = []
    close = metrics.get("close", 0)
    ma60 = metrics.get("ma60") or 0
    ma240 = metrics.get("ma240") or 0

    # Stage 분류 (60>240 + 가격 위치)
    if ma240 and ma60 > ma240 and close > ma60:
        pts += 40; items.append("Stage 2 (상승)")
    elif accumulation.get("in_accumulation"):
        pts += 25; items.append("Stage 1 (매집)")
    elif ma240 and close < ma240 and ma60 < ma240:
        pts -= 20; items.append("Stage 4 (하락) 회피")

    m = mansfield or {}
    if m.get("available"):
        if m.get("positive"):
            pts += 20
            items.append(f"Mansfield RS +{m['rs_norm_pct']}%")
        if m.get("mansfield_buy"):
            pts += 30
            items.append(f"Mansfield Buy ({m.get('days_since_cross')}일전)")
        elif m.get("trend_up"):
            pts += 10
            items.append("RS 상승중")

    pts = max(0, min(100, pts))
    return {"score": pts, "items": items}


def oneil_score(supply: pd.DataFrame | None, metrics: dict,
                 score_dict: dict) -> dict:
    """O'Neil 점수 0~100 (CAN SLIM의 'I' + 'N' + 'M' + 'L' + 'S').

    실적 데이터(C, A) 부재로 35점 만점에서 0점 처리.
    """
    pts = 0
    items = []
    # I — Institutional Sponsorship
    if supply is not None and len(supply) >= 60:
        if "외국인" in supply.columns:
            f60 = float(supply["외국인"].iloc[-60:].sum())
            if f60 > 0: pts += 15; items.append("외인 60일 +")
        if "기관합계" in supply.columns:
            i60 = float(supply["기관합계"].iloc[-60:].sum())
            if i60 > 0: pts += 15; items.append("기관 60일 +")
        if "외국인" in supply.columns and "기관합계" in supply.columns:
            last5 = supply.tail(5)
            both = ((last5["외국인"] > 0) & (last5["기관합계"] > 0)).sum()
            if both >= 3: pts += 10; items.append("최근 5일 동시매수")

    # N — New high
    if metrics.get("is_52w_high"):
        pts += 15; items.append("52주 신고가")

    # M — Market direction (간이: 60>240)
    if metrics.get("ma60_above_ma240"):
        pts += 10; items.append("M: 정배열")

    # L — Leadership (장기 RS 양수)
    rs = metrics.get("rs_120d_pp")
    if rs is not None and rs > 0:
        pts += 15; items.append(f"L: RS +{rs}%p")

    # S — Supply 강도
    if score_dict.get("intensity", 0) >= 13:
        pts += 20
        items.append(f"S: 강도 {score_dict.get('intensity')}")

    pts = max(0, min(100, pts))
    return {"score": pts, "items": items}


def livermore_score(accumulation: dict, metrics: dict,
                     wyckoff_pack: dict | None) -> dict:
    """Livermore 점수 0~100.
      - Pivotal Point (50점)
      - 추세 정배열 (25점)
      - 거래량 동반 (25점)
    """
    pts = 0
    items = []
    close = metrics.get("close", 0)
    box_high = accumulation.get("box_high")
    rvol = metrics.get("rvol", 0)
    strong = bool(metrics.get("strong_close"))
    ma_ok = bool(metrics.get("ma60_above_ma240"))
    sos = bool((wyckoff_pack or {}).get("sos", {}).get("sos"))

    breakout = box_high is not None and close > box_high * 1.005
    if breakout and strong: pts += 50; items.append("Pivot + 강한 마감")
    elif breakout: pts += 30; items.append("Pivot 돌파")
    elif sos: pts += 25; items.append("SOS 패턴")

    if ma_ok: pts += 25; items.append("정배열")

    if rvol >= 2.0: pts += 25; items.append(f"거래량 폭발 {rvol}")
    elif rvol >= 1.5: pts += 15; items.append(f"거래량 양호 {rvol}")

    pts = max(0, min(100, pts))
    return {"score": pts, "items": items}


def korean_score(supply: pd.DataFrame | None, score_dict: dict,
                 weekly_pack: dict | None) -> dict:
    """국내 실전 점수 0~100 (박병창/김정환 정신).
      - 외인+기관 동시매수 지속성 (35점)
      - 시총·거래대금 대비 비율 (25점)
      - 매도→매수 전환 (20점)
      - 주봉 동시매수 주(週) 수 (20점)
    """
    pts = 0
    items = []

    if supply is not None and len(supply) >= 20:
        last20 = supply.tail(20)
        if "외국인" in supply.columns and "기관합계" in supply.columns:
            both = ((last20["외국인"] > 0) & (last20["기관합계"] > 0)).sum()
            pts += min(35, int(both / 20 * 35))
            if both >= 8: items.append(f"동시매수 {both}/20일")

    intensity = score_dict.get("intensity", 0)
    pts += min(25, int(intensity / 25 * 25))

    # 변곡점: 직전 5일 매도, 최근 3일 매수
    if supply is not None and len(supply) >= 8 and "외국인" in supply.columns:
        prior = supply["외국인"].iloc[-8:-3]
        recent = supply["외국인"].iloc[-3:]
        if (prior < 0).sum() >= 3 and (recent > 0).sum() >= 2:
            pts += 20; items.append("외인 매도→매수 전환")

    sm = (weekly_pack or {}).get("smart_money_w", {})
    if sm.get("available"):
        coincide_w = int(sm.get("coincide_weeks", 0))
        if coincide_w >= 13:
            pts += 20; items.append(f"주봉 동시매수 {coincide_w}주")
        elif coincide_w >= 8:
            pts += 10; items.append(f"주봉 동시매수 {coincide_w}주")

    pts = max(0, min(100, pts))
    return {"score": pts, "items": items}


def evaluate_ensemble(daily, supply, score_dict, accumulation, metrics,
                      weekly_pack, wyckoff_pack, vcp_pack, mansfield) -> dict:
    """6명 대가 종합 + 앙상블 점수."""
    masters = {
        "wyckoff": wyckoff_score(accumulation, wyckoff_pack, weekly_pack,
                                   metrics, score_dict),
        "minervini": minervini_score(metrics, vcp_pack, accumulation),
        "weinstein": weinstein_score(metrics, accumulation, mansfield),
        "oneil": oneil_score(supply, metrics, score_dict),
        "livermore": livermore_score(accumulation, metrics, wyckoff_pack),
        "korean": korean_score(supply, score_dict, weekly_pack),
    }
    # 통합 점수: 텐버거에 더 중요한 대가 가중치
    weights = {
        "wyckoff": 0.25,
        "minervini": 0.20,
        "weinstein": 0.15,
        "oneil": 0.10,
        "livermore": 0.15,
        "korean": 0.15,
    }
    ensemble = sum(masters[k]["score"] * weights[k] for k in masters)
    masters_70plus = sum(1 for k in masters if masters[k]["score"] >= 70)
    masters_80plus = sum(1 for k in masters if masters[k]["score"] >= 80)
    return {
        "scores": {k: v["score"] for k, v in masters.items()},
        "items": {k: v["items"] for k, v in masters.items()},
        "ensemble": round(ensemble, 1),
        "masters_70plus": masters_70plus,
        "masters_80plus": masters_80plus,
    }
