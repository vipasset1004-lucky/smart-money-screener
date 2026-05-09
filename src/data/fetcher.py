"""데이터 수집 — pykrx (OHLCV/유니버스) + Naver 스크래핑 (수급).

pykrx의 투자자별 매매 endpoint는 현재 KRX 응답이 불안정해서,
수급 데이터는 네이버 금융 HTML 스크래핑으로 가져온다.
"""

from __future__ import annotations

import io
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


# ── Universe ─────────────────────────────────────────────

def get_universe(min_mktcap: int = 100_000_000_000,
                 min_amount: int = 3_000_000_000) -> list[dict]:
    """KRX 보통주 중 시총·거래대금 필터 통과 종목.

    시총 1000억 / 거래대금 30억 — 외국인/기관이 의미있게 매수하는 구간.
    이하는 smart money 신호 자체가 약해 노이즈 비율 급증.
    """
    import FinanceDataReader as fdr
    df = fdr.StockListing("KRX")
    df = df[df["MarketId"].isin(["STK", "KSQ"])]
    df = df[df["Code"].str.endswith("0")]
    df = df[(df["Marcap"] >= min_mktcap) &
            (df["Amount"] >= min_amount) &
            (df["Amount"] > 0)]
    out = []
    for _, row in df.iterrows():
        out.append({
            "ticker": str(row["Code"]).zfill(6),
            "name": str(row["Name"]),
            "market": "KOSPI" if row["MarketId"] == "STK" else "KOSDAQ",
            "marcap": int(row["Marcap"]),
        })
    logger.info(f"[universe] {len(out)} 종목")
    return out


# ── OHLCV (pykrx) ────────────────────────────────────────

def fetch_ohlcv(ticker: str, days: int = 400,
                retry: int = 2) -> Optional[pd.DataFrame]:
    """OHLCV 일봉 (pykrx). 거래대금은 volume×close 근사. retry 추가."""
    from pykrx import stock
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 60)).strftime("%Y%m%d")
    rename = {"시가": "open", "고가": "high", "저가": "low",
              "종가": "close", "거래량": "volume"}
    for attempt in range(retry + 1):
        try:
            df = stock.get_market_ohlcv(start, end, ticker)
            if df is not None and not df.empty:
                df = df.rename(columns=rename)
                df["amount"] = (df["volume"].astype("int64")
                                * df["close"].astype("int64"))
                # 메모리 절감: float64→float32 (한국주가 7자리 정밀도면 충분)
                out = df[["open", "high", "low", "close", "volume", "amount"]]
                return out.astype({"open": "float32", "high": "float32",
                                   "low": "float32", "close": "float32",
                                   "volume": "int32"})
        except Exception as e:
            if attempt < retry:
                time.sleep(0.5 + attempt * 0.5)
                continue
            logger.warning(f"[ohlcv] {ticker}: {e}")
        if attempt < retry:
            time.sleep(0.5 + attempt * 0.5)
    return None


# ── 수급 (Naver 스크래핑) ────────────────────────────────

_FRGN_NUM_RE = re.compile(r"-?\d[\d,]*")


def _parse_naver_int(s) -> Optional[int]:
    """네이버 표의 셀 값을 int로. float/숫자 문자열/콤마 모두 대응."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        if pd.isna(s):
            return None
        return int(s)
    txt = str(s).replace(",", "").strip()
    if txt in ("", "-", "—", "nan"):
        return None
    try:
        return int(float(txt))
    except (ValueError, TypeError):
        return None


def fetch_supply_demand(ticker: str, max_pages: int = 6) -> Optional[pd.DataFrame]:
    """네이버 금융에서 외인/기관 일별 순매수 수량(주) 가져오기.

    URL: https://finance.naver.com/item/frgn.naver?code={ticker}&page=N
    각 페이지당 ~15일치, max_pages=6이면 약 90일.

    Returns DataFrame indexed by date with columns:
        외국인, 기관합계, 개인 (개인은 추정 — 네이버는 외인/기관만 직접 제공)
    값 단위: 주 (수량). 비어있으면 None.
    """
    rows = []
    for page in range(1, max_pages + 1):
        url = f"https://finance.naver.com/item/frgn.naver?code={ticker}&page={page}"
        # 페이지당 retry 1회 (Naver rate limit 회피)
        tables = None
        for attempt in range(2):
            try:
                resp = requests.get(url, headers=NAVER_HEADERS, timeout=10)
                resp.encoding = "euc-kr"
                if resp.status_code != 200:
                    if attempt == 0:
                        time.sleep(0.5)
                        continue
                    break
                tables = pd.read_html(io.StringIO(resp.text))
                break
            except Exception as e:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                logger.debug(f"[naver supply] {ticker} p{page}: {e}")
                break
        if tables is None:
            break

        # 네이버 frgn 페이지의 데이터 표는 보통 인덱스 2 근처. 가장 행 많은 표 사용.
        candidates = [t for t in tables if t.shape[0] >= 5 and t.shape[1] >= 6]
        if not candidates:
            break
        df = max(candidates, key=lambda t: t.shape[0])

        # MultiIndex라면 두 레벨을 "_"로 결합. 단일이면 그대로.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = ["_".join(str(p) for p in c).strip()
                          for c in df.columns]
        df.columns = [str(c).strip() for c in df.columns]

        # 날짜 컬럼: '날짜_날짜' 또는 '날짜' (반복 접미 제거)
        date_col = next((c for c in df.columns
                         if c.startswith("날짜")), df.columns[0])
        foreign_col = next((c for c in df.columns
                            if "외국인_순매매" in c or
                               (c == "외국인_순매매수")), None)
        inst_col = next((c for c in df.columns
                         if "기관_순매매" in c or
                            (c == "기관_순매매수")), None)
        if foreign_col is None:
            foreign_col = next((c for c in df.columns
                                if "외국인" in c and "순매" in c), None)
        if inst_col is None:
            inst_col = next((c for c in df.columns
                             if "기관" in c and "순매" in c), None)
        if foreign_col is None or inst_col is None:
            logger.debug(f"[naver supply] {ticker} p{page}: cols={df.columns.tolist()}")
            break

        for _, r in df.iterrows():
            d_raw = str(r[date_col]).strip()
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", d_raw):
                continue
            try:
                d = datetime.strptime(d_raw, "%Y.%m.%d").date()
            except ValueError:
                continue
            f_val = _parse_naver_int(r[foreign_col])
            i_val = _parse_naver_int(r[inst_col])
            if f_val is None and i_val is None:
                continue
            rows.append({
                "date": d,
                "외국인": f_val if f_val is not None else 0,
                "기관합계": i_val if i_val is not None else 0,
            })

        time.sleep(0.1)

    if not rows:
        return None

    out = pd.DataFrame(rows).drop_duplicates(subset=["date"]).sort_values("date")
    out["date"] = pd.to_datetime(out["date"])
    out = out.set_index("date")
    # 개인은 -(외인+기관) 로 근사 — 거래대금 보존이 정확치 않지만 부호만 보면 충분
    out["개인"] = -(out["외국인"] + out["기관합계"])
    return out


# ── 시가총액 ────────────────────────────────────────────

def fetch_market_cap(ticker: str) -> Optional[int]:
    """가장 최근 시가총액 (pykrx). 실패 시 None."""
    from pykrx import stock
    today = datetime.now().strftime("%Y%m%d")
    try:
        df = stock.get_market_cap_by_date(
            (datetime.now() - timedelta(days=10)).strftime("%Y%m%d"),
            today, ticker)
        if df is None or df.empty:
            return None
        col = "시가총액" if "시가총액" in df.columns else df.columns[0]
        return int(df[col].iloc[-1])
    except Exception as e:
        logger.debug(f"[mcap] {ticker}: {e}")
        return None


def fetch_all(ticker: str, ohlcv_days: int = 400, supply_pages: int = 6,
              sleep: float = 0.15) -> Optional[dict]:
    ohlcv = fetch_ohlcv(ticker, days=ohlcv_days)
    time.sleep(sleep)
    supply = fetch_supply_demand(ticker, max_pages=supply_pages)
    if ohlcv is None:
        return None
    return {"ticker": ticker, "ohlcv": ohlcv, "supply": supply}
