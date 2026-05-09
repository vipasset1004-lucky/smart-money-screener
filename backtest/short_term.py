"""단타 신호 백테스트 — 5일 보유 기준.

원리:
  1. 종목별로 OHLCV(400일) + 네이버 수급(약 120일치) 한 번에 fetch
  2. "오늘로부터 N일 전" 시점에 알고리즘을 실행 (그 시점까지의 데이터만 보고)
  3. 신호 발생 시점이면, 다음 거래일 시가 매수 → 5일 후 종가 매도 가정
  4. 5일 수익률 기록
  5. 신호 종목 평균 수익률 vs 무신호 종목 평균 수익률 비교

데이터 한계:
  - 네이버 수급이 ~90~120일만 제공 → 단타(5일 forward)는 OK, 텐버거 불가
  - 거래대금은 volume × close 근사
"""

from __future__ import annotations

import argparse
import csv
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from src.data.fetcher import (
    fetch_ohlcv, fetch_supply_demand, get_universe,
)
from src.data.bulk_fetcher import fetch_kospi_history
from src.analysis.score import (
    supply_demand_score, detect_accumulation, chart_metrics,
)
from src.analysis.weekly import (
    to_weekly, to_weekly_supply, detect_weekly_accumulation,
    detect_vcp, detect_volume_dry_explode, smart_money_weekly,
    weekly_breakout,
)
from src.signals.departure import short_term_departure, tenbagger_departure

logger = logging.getLogger(__name__)


def backtest_one_stock(stock: dict, history_days: int = 60,
                       forward_days: int = 5,
                       market_close=None) -> Optional[list[dict]]:
    """한 종목에 대해 history_days 만큼 시점을 walk-forward 백테스트."""
    ticker = stock["ticker"]
    name = stock.get("name", ticker)
    mcap = stock.get("marcap")

    # 한 번에 데이터 fetch
    ohlcv = fetch_ohlcv(ticker, days=400)
    if ohlcv is None or len(ohlcv) < history_days + forward_days + 60:
        return None
    supply = fetch_supply_demand(ticker, max_pages=8)  # 약 120일

    rows = []
    n = len(ohlcv)
    # 가장 최근 forward_days를 outcome용으로, 그 앞부터 history_days만큼 시점 평가
    for i in range(forward_days, forward_days + history_days):
        if n - i - 1 < 60:  # 최소 60일 lookback 필요
            break
        as_of_idx = n - 1 - i
        as_of_date = ohlcv.index[as_of_idx]

        # 시점 슬라이싱 (as_of 까지만)
        ohlcv_slice = ohlcv.iloc[:as_of_idx + 1]
        if supply is not None:
            supply_slice = supply.loc[:as_of_date]
            if len(supply_slice) < 30:
                supply_slice = None  # 데이터 부족
        else:
            supply_slice = None

        # 알고리즘 실행
        try:
            mkt_slice = None
            if market_close is not None:
                mkt_slice = market_close.loc[:as_of_date]
                if len(mkt_slice) < 5:
                    mkt_slice = None
            score = supply_demand_score(supply_slice, ohlcv_slice, mcap)
            accum = detect_accumulation(ohlcv_slice)
            metrics = chart_metrics(ohlcv_slice, market_close=mkt_slice)
            # 주봉 통합
            weekly = to_weekly(ohlcv_slice)
            weekly_supply = to_weekly_supply(supply_slice)
            weekly_pack = None
            if weekly is not None and len(weekly) >= 26:
                w_accum = detect_weekly_accumulation(weekly)
                weekly_pack = {
                    "accum": w_accum,
                    "vcp": detect_vcp(weekly),
                    "dry_explode": detect_volume_dry_explode(weekly),
                    "smart_money_w": smart_money_weekly(weekly_supply),
                    "breakout_w": weekly_breakout(weekly, w_accum),
                }
            st_sig = short_term_departure(ohlcv_slice, supply_slice, metrics, accum)
            tb_sig = tenbagger_departure(ohlcv_slice, supply_slice, metrics,
                                          accum, score=score, marcap=mcap,
                                          weekly_pack=weekly_pack)
        except Exception as e:
            logger.debug(f"[bt] {ticker} {as_of_date}: {e}")
            continue

        # Outcome: 진입 = 다음 거래일 시가, 청산 = forward_days 후 종가
        # 단순화: 신호 일자 = as_of_idx, 진입가 = as_of_idx+1 시가
        if as_of_idx + 1 + forward_days >= n:
            continue
        entry_open = float(ohlcv["open"].iloc[as_of_idx + 1])
        exit_close = float(ohlcv["close"].iloc[as_of_idx + 1 + forward_days])
        if entry_open <= 0 or exit_close <= 0:
            continue  # 거래정지/데이터 결함 스킵
        ret_pct = (exit_close - entry_open) / entry_open * 100

        # 최고가·최저가 (보유 기간)
        hold = ohlcv.iloc[as_of_idx + 1:as_of_idx + 1 + forward_days + 1]
        max_high = float(hold["high"].max())
        min_low = float(hold["low"].min())
        if max_high <= 0 or min_low <= 0:
            continue
        max_gain = (max_high - entry_open) / entry_open * 100
        max_drawdown = (min_low - entry_open) / entry_open * 100

        rows.append({
            "ticker": ticker,
            "name": name,
            "as_of": str(as_of_date.date() if hasattr(as_of_date, "date") else as_of_date),
            "score": score["total"],
            "short_signal": st_sig["triggered"],
            "tenbagger_signal": tb_sig["triggered"],
            "in_accumulation": accum.get("in_accumulation"),
            "rvol": metrics.get("rvol"),
            "amount_mult": metrics.get("amount_mult"),
            "is_52w_high": metrics.get("is_52w_high"),
            "entry_open": entry_open,
            "exit_close": exit_close,
            "ret_pct": round(ret_pct, 2),
            "max_gain_pct": round(max_gain, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
        })

    return rows


def run_backtest(tickers: list[dict], history_days: int = 60,
                 forward_days: int = 5, max_workers: int = 6) -> pd.DataFrame:
    started = time.time()
    market_close = fetch_kospi_history(days=400)
    if market_close is not None:
        logger.info(f"[bt] kospi {len(market_close)} pts loaded")
    all_rows = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(backtest_one_stock, s, history_days, forward_days,
                           market_close): s
                for s in tickers}
        for f in as_completed(futs):
            r = f.result()
            if r:
                all_rows.extend(r)
                logger.info(f"[bt] {r[0]['ticker']} {len(r)} rows")
    df = pd.DataFrame(all_rows)
    logger.info(f"[bt] 완료 {len(df)} rows ({time.time()-started:.0f}s)")
    return df


def summarize(df: pd.DataFrame, forward_days: int = 5) -> str:
    """결과 요약 출력."""
    if df.empty:
        return "데이터 없음"

    out = [f"\n=== 백테스트 요약 ({forward_days}일 보유) ===\n"]
    out.append(f"총 평가 시점: {len(df):,}")
    out.append(f"평가 종목 수: {df['ticker'].nunique()}")
    out.append(f"기간: {df['as_of'].min()} ~ {df['as_of'].max()}\n")

    def stats(label: str, sub: pd.DataFrame):
        if sub.empty:
            return f"  {label}: 0건"
        win = (sub["ret_pct"] > 0).sum()
        avg = sub["ret_pct"].mean()
        med = sub["ret_pct"].median()
        win_avg = sub.loc[sub["ret_pct"] > 0, "ret_pct"].mean() if win else 0
        loss_avg = sub.loc[sub["ret_pct"] <= 0, "ret_pct"].mean() if (len(sub) - win) else 0
        max_g = sub["max_gain_pct"].mean()
        max_d = sub["max_drawdown_pct"].mean()
        return (f"  {label}: {len(sub):4d}건  승률 {win/len(sub)*100:5.1f}%  "
                f"평균 {avg:+5.2f}%  중앙 {med:+5.2f}%  "
                f"승평균 +{win_avg:5.2f}% 패평균 {loss_avg:5.2f}%  "
                f"평균최고 +{max_g:.2f}% 평균최저 {max_d:+.2f}%")

    # 전체 (기준선)
    out.append(stats("전체 (기준선)", df))
    out.append("")
    # 단타 신호
    out.append(stats("⚡ 단타 신호 ON  ", df[df["short_signal"]]))
    out.append(stats("⚡ 단타 신호 OFF ", df[~df["short_signal"]]))
    out.append("")
    # 텐버거 신호
    out.append(stats("💎 텐버거 신호 ON ", df[df["tenbagger_signal"]]))
    out.append(stats("💎 텐버거 신호 OFF", df[~df["tenbagger_signal"]]))
    out.append("")
    # 점수 구간별
    out.append("점수 구간별:")
    for lo, hi in [(0, 30), (30, 50), (50, 60), (60, 70), (70, 100)]:
        sub = df[(df["score"] >= lo) & (df["score"] < hi)]
        out.append(stats(f"  점수 {lo:>2}~{hi:>2}", sub))
    out.append("")
    # 매집 단계
    out.append(stats("📦 매집 단계 ON  ", df[df["in_accumulation"] == True]))
    out.append(stats("📦 매집 단계 OFF ", df[df["in_accumulation"] == False]))

    return "\n".join(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", help="콤마 구분 ticker 또는 'top'(results.json 톱)",
                        default="top")
    parser.add_argument("--n", type=int, default=20, help="top 사용 시 종목 수")
    parser.add_argument("--history", type=int, default=60, help="백테스트 시점 수")
    parser.add_argument("--forward", type=int, default=5, help="보유 일수")
    parser.add_argument("--out", default="backtest/short_term_result.csv")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    import sys, io, json
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace")

    # 종목 리스트
    if args.tickers == "top":
        path = Path("results.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        # 라벨 부여 종목 우선, 부족하면 점수순
        labeled = [r for r in data["results"] if r.get("labels")]
        pool = labeled if len(labeled) >= args.n else data["results"]
        tickers = [{"ticker": r["ticker"], "name": r["name"],
                    "marcap": r.get("marcap")} for r in pool[:args.n]]
    else:
        tickers = [{"ticker": t.strip(), "name": t.strip(), "marcap": None}
                   for t in args.tickers.split(",")]

    print(f"백테스트 종목 {len(tickers)}개:")
    for t in tickers:
        print(f"  {t['ticker']} {t['name']}")

    df = run_backtest(tickers, history_days=args.history,
                      forward_days=args.forward)
    if not df.empty:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\n결과 저장: {args.out}")

    print(summarize(df, args.forward))
