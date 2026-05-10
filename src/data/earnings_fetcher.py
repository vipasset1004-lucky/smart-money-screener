"""Naver 금융 실적 스크래핑.

종목 메인 페이지(`finance.naver.com/item/main.naver?code=...`)의 주요재무정보
테이블에서 매출액·영업이익·당기순이익 추출 → YoY 카테고리화.

테이블 구조:
  헤더1: '주요재무정보' | '최근 연간 실적' | '최근 분기 실적'
  헤더2: 2023.12 | 2024.12 | 2025.12 | 2026.12(E) | 2024.12 | 2025.03 | 2025.06 | 2025.09 | 2025.12 | 2026.03(E)
  헤더3: IFRS연결 ...
  매출액  | 값들...
  영업이익 | 값들... ← 핵심 지표
  ...

분류 기준 (영업이익 YoY 분기 기준):
  🔥 실적폭발 — +100%↑
  📈 실적급증 — +30%~+100%
  ↗️ 실적증가 — +10%~+30%
  ➡️ 실적유지 — -10%~+10%
  ↘️ 실적감소 — -30%~-10%
  📉 실적부진 — -30%↓
  🔄 흑자전환 — 적자→흑자
  💀 적자전환 — 흑자→적자
  ⚠️ 적자유지 — 둘 다 음수
"""

from __future__ import annotations

import html as html_mod
import logging
import re
import time
import urllib.request as ureq
from typing import Optional

logger = logging.getLogger(__name__)

NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _parse_num(s: str) -> Optional[float]:
    """'65,670' 또는 '-15' 또는 '' → float | None."""
    if not s:
        return None
    s = s.replace(",", "").replace("&nbsp;", "").replace("\xa0", "").strip()
    if s in ("", "-", "—", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _strip_html(html_str: str) -> str:
    """HTML 태그 제거 + 엔티티 디코드 + 공백 정리.
    예: '2026.12&#40;E&#41;' → '2026.12(E)'
    """
    txt = re.sub(r"<[^>]+>", "", html_str)
    txt = html_mod.unescape(txt)  # &#40;→(  &amp;→&  &nbsp;→' ' 등
    txt = txt.replace("\xa0", " ")
    return re.sub(r"\s+", " ", txt).strip()


def fetch_earnings_raw(ticker: str, retry: int = 1) -> Optional[dict]:
    """종목의 주요재무정보 raw 데이터 fetch.

    Returns: {
        "headers": [["2023.12", "2024.12", ...], 연간4 + 분기5~6],
        "annual": {"매출액": [...], "영업이익": [...], "당기순이익": [...]},
        "quarterly": {"매출액": [...], ...},
        "annual_periods": ["2023.12", "2024.12", "2025.12", "2026.12(E)"],
        "quarterly_periods": ["2024.12", "2025.03", ...],
    } 또는 None
    """
    url = f"https://finance.naver.com/item/main.naver?code={ticker}"
    for attempt in range(retry + 1):
        try:
            req = ureq.Request(url, headers=NAVER_HEADERS)
            with ureq.urlopen(req, timeout=12) as r:
                html = r.read().decode("utf-8", errors="replace")
            break
        except Exception as e:
            if attempt < retry:
                time.sleep(0.5)
                continue
            logger.debug(f"[earnings fetch] {ticker}: {e}")
            return None

    # 매출액+영업이익 포함된 첫 table
    tables = re.findall(r"<table[^>]*>([\s\S]*?)</table>", html)
    target = None
    for t in tables:
        if "매출액" in t and "영업이익" in t and "주요재무정보" in t:
            target = t
            break
    if target is None:
        # fallback: 주요재무정보 키워드 없어도 매출액+영업이익+IFRS 있으면 OK
        for t in tables:
            if "매출액" in t and "영업이익" in t and "IFRS" in t:
                target = t
                break
    if target is None:
        return None

    rows = re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", target)
    parsed_rows = []
    for r in rows:
        cells = re.findall(r"<t[hd][^>]*>([\s\S]*?)</t[hd]>", r)
        # 빈 셀 보존 (컬럼 정렬 유지) — 작은 종목은 분기 데이터 부분 누락
        cleaned = [_strip_html(c) for c in cells]
        if any(c for c in cleaned):  # 행에 적어도 한 셀 비어있지 않을 때만
            parsed_rows.append(cleaned)

    if len(parsed_rows) < 4:
        return None

    # 헤더 분석: 두 번째 행에 기간들 (예: '2023.12', '2024.12', ...)
    # 첫 행 [0]: '주요재무정보' | '최근 연간 실적' | '최근 분기 실적'
    # 두 번째 행 [1]: 기간들 (4개 연간 + 5~6개 분기 보통)
    headers = parsed_rows[1] if parsed_rows[1] else []
    periods = [p for p in headers if re.match(r"\d{4}\.\d{2}", p)]
    if len(periods) < 4:
        return None

    # 연간 vs 분기 구분 — 보통 4개 연간 + 나머지 분기
    # 연간은 .12로 끝나는 첫 4개 (또는 .03/.06/.09/.12 연속이면 분기)
    # 발견 패턴: 처음 4개는 다른 연도의 .12 → 연간
    #            그 후는 같은 연도의 다른 월 → 분기
    annual_count = 4
    # 검증: 첫 4개가 모두 .12인지 (연간)
    first_four = periods[:4]
    if not all(p.endswith(".12") or "(E)" in p for p in first_four):
        # 연간 확정이 .12가 아니면 다른 회계년도 회사 — 일단 4개 가정 유지
        pass

    annual_periods = periods[:annual_count]
    quarterly_periods = periods[annual_count:]

    # 데이터 행 추출 — 각 행 첫 셀이 이름, 나머지가 값들
    data = {}
    for row in parsed_rows[3:]:  # IFRS 행 다음부터
        if not row or len(row) < 2:
            continue
        name = row[0]
        vals = [_parse_num(v) for v in row[1:]]
        # 길이가 periods 길이와 맞아야 의미 있음 (보통 정확히 일치)
        if len(vals) >= len(periods):
            data[name] = vals[: len(periods)]

    # 매출액 / 영업이익 / 당기순이익 추출
    def find_row(*keys):
        """키 중 하나가 정확히 매치되는 첫 번째 row name을 찾음.
        '영업이익률' 같은 부분일치 회피 위해 우선 정확매치 → fallback prefix."""
        # 정확매치
        for k in keys:
            if k in data:
                return data[k]
        # prefix매치 (단, 주의: '영업이익률' 회피)
        for name, vals in data.items():
            for k in keys:
                if name == k:
                    return vals
        return None

    revenue = find_row("매출액")
    op_income = find_row("영업이익")  # 정확매치
    net_income = find_row("당기순이익")

    if revenue is None or op_income is None:
        return None

    return {
        "ticker": ticker,
        "annual_periods": annual_periods,
        "quarterly_periods": quarterly_periods,
        "annual": {
            "매출액": revenue[:annual_count],
            "영업이익": op_income[:annual_count],
            "당기순이익": net_income[:annual_count] if net_income else [None] * annual_count,
        },
        "quarterly": {
            "매출액": revenue[annual_count:],
            "영업이익": op_income[annual_count:],
            "당기순이익": net_income[annual_count:] if net_income else [None] * len(quarterly_periods),
        },
    }


def categorize_yoy(latest: Optional[float], prior: Optional[float]) -> dict:
    """YoY 비교 → 카테고리.

    Returns {"label": "🔥실적폭발", "yoy_pct": 156.3, "status": "growing"}
    """
    if latest is None or prior is None:
        return {"label": None, "yoy_pct": None, "status": "unknown"}

    # 흑자전환: 음수 → 양수 (가장 강한 긍정 시그널)
    if prior <= 0 and latest > 0:
        return {"label": "🔄흑자전환", "yoy_pct": None, "status": "turnaround"}
    # 적자전환: 양수 → 음수 (가장 강한 부정 시그널)
    if prior > 0 and latest <= 0:
        return {"label": "💀적자전환", "yoy_pct": None, "status": "decline"}
    # 적자 케이스 세분화 — 손실 변화율 기준
    if prior <= 0 and latest <= 0:
        # |latest| vs |prior| 비교 — 손실 축소면 긍정, 확대면 부정
        # prior == 0 회피용 fallback
        if prior == 0:
            return {"label": "⚠️적자유지", "yoy_pct": None, "status": "loss"}
        loss_change = (abs(latest) - abs(prior)) / abs(prior) * 100
        if loss_change <= -50:
            return {"label": "🌱적자축소", "yoy_pct": round(-loss_change, 1), "status": "loss_shrinking"}
        if loss_change >= 50:
            return {"label": "💸적자확대", "yoy_pct": round(loss_change, 1), "status": "loss_growing"}
        return {"label": "⚠️적자유지", "yoy_pct": round(loss_change, 1), "status": "loss"}

    # 양수 vs 양수 — % 계산
    yoy = (latest - prior) / abs(prior) * 100

    if yoy >= 100:
        label = "🔥실적폭발"
    elif yoy >= 30:
        label = "📈실적급증"
    elif yoy >= 10:
        label = "↗️실적증가"
    elif yoy >= -10:
        label = "➡️실적유지"
    elif yoy >= -30:
        label = "↘️실적감소"
    else:
        label = "📉실적부진"

    return {"label": label, "yoy_pct": round(yoy, 1), "status": "growing" if yoy > 0 else "declining"}


def analyze_earnings(raw: dict) -> dict:
    """raw 데이터 → 분류 결과.

    분기 YoY 우선 (가장 반응 빠름):
      가장 최근 확정 분기 / 같은 분기 1년 전
    연간 YoY 보조:
      가장 최근 확정 연도 / 전년
    """
    if not raw:
        return {"available": False}

    q_periods = raw["quarterly_periods"]
    q_op = raw["quarterly"]["영업이익"]
    a_periods = raw["annual_periods"]
    a_op = raw["annual"]["영업이익"]

    # 분기 YoY: 마지막 확정 분기 (E 제외) vs 동일 월 1년 전
    # 분기는 보통 [.12, .03, .06, .09, .12, .03(E)] 또는 [.12, .03, .06, .09]
    def is_estimate(p: str) -> bool:
        return "(E)" in p

    # 확정 분기만 추출 (인덱스 기준)
    confirmed_q = [(i, p) for i, p in enumerate(q_periods) if not is_estimate(p)]
    quarterly_yoy = {"label": None, "yoy_pct": None, "period": None}
    if len(confirmed_q) >= 2:
        # 가장 최근 확정 분기
        latest_i, latest_p = confirmed_q[-1]
        latest_month = latest_p.split(".")[1] if "." in latest_p else None
        # 같은 월 1년 전 찾기
        prior_i = None
        for i, p in confirmed_q[:-1]:
            month = p.split(".")[1] if "." in p else None
            if month == latest_month:
                prior_i = i
        if prior_i is not None and prior_i < latest_i:
            cat = categorize_yoy(q_op[latest_i], q_op[prior_i])
            quarterly_yoy = {
                **cat,
                "period": f"{latest_p} vs {q_periods[prior_i]}",
                "latest": q_op[latest_i],
                "prior": q_op[prior_i],
            }

    # 연간 YoY: 마지막 확정 vs 그 전 (E는 제외)
    confirmed_a = [(i, p) for i, p in enumerate(a_periods) if not is_estimate(p)]
    annual_yoy = {"label": None, "yoy_pct": None, "period": None}
    if len(confirmed_a) >= 2:
        latest_i, latest_p = confirmed_a[-1]
        prior_i, prior_p = confirmed_a[-2]
        cat = categorize_yoy(a_op[latest_i], a_op[prior_i])
        annual_yoy = {
            **cat,
            "period": f"{latest_p} vs {prior_p}",
            "latest": a_op[latest_i],
            "prior": a_op[prior_i],
        }

    # 메인 라벨: 분기 YoY 우선, 없으면 연간
    main = quarterly_yoy if quarterly_yoy["label"] else annual_yoy

    return {
        "available": True,
        "label": main["label"],
        "yoy_pct": main.get("yoy_pct"),
        "period": main.get("period"),
        "quarterly": quarterly_yoy,
        "annual": annual_yoy,
        "raw": {
            "annual_op": list(zip(a_periods, a_op)),
            "quarterly_op": list(zip(q_periods, q_op)),
        },
    }


def fetch_and_analyze(ticker: str) -> Optional[dict]:
    """편의 함수: fetch + analyze 한 번에."""
    raw = fetch_earnings_raw(ticker)
    if not raw:
        return None
    return analyze_earnings(raw)


if __name__ == "__main__":
    # 단일 테스트
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    result = fetch_and_analyze(code)
    if not result:
        print(f"{code}: 데이터 없음")
        sys.exit(1)
    print(f"=== {code} 실적 분석 ===")
    print(f"메인 라벨: {result['label']}")
    print(f"YoY: {result['yoy_pct']}%  ({result['period']})")
    print(f"분기: {result['quarterly']}")
    print(f"연간: {result['annual']}")
    print(f"연간 영업이익: {result['raw']['annual_op']}")
    print(f"분기 영업이익: {result['raw']['quarterly_op']}")
