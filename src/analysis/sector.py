"""섹터 동시포착 (대용) — 가격 상관도 기반 클러스터링.

KRX 공식 섹터 데이터는 외부 API 불안정해서, 30일 수익률 상관계수
0.7 이상이면 같은 동조 그룹으로 분류. 의미적 섹터는 아니지만
"같이 움직이는 종목들" 자동 발견.

GPT 설계서: "동일 섹터 동시 상승 여부" 신호 강화.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def build_correlation_clusters(ohlcv_map: dict, days: int = 30,
                               threshold: float = 0.65) -> dict[str, list[str]]:
    """{ticker: [동조 ticker 리스트]} 반환.

    각 종목에 대해 30일 수익률 상관계수가 threshold 이상인 종목들을 모음.
    """
    if not ohlcv_map:
        return {}

    # 각 종목 N일 수익률 시리즈 (보통 days=30, fetch가 정확히 30일 반환)
    returns = {}
    min_len = max(15, days - 10)  # 최소 15일은 있어야
    for tk, df in ohlcv_map.items():
        if df is None or len(df) < min_len:
            continue
        close = df["close"]
        ret = close.pct_change().dropna()
        if len(ret) >= min_len - 1:
            returns[tk] = ret

    if len(returns) < 5:
        return {}

    # DataFrame으로 정렬 (날짜 인덱스 정렬, 결측치 NaN)
    rdf = pd.DataFrame(returns).dropna(how="all")
    if rdf.empty or len(rdf) < 10:
        return {}

    # 전체 상관계수 행렬 (수십 ms)
    corr = rdf.corr()

    clusters: dict[str, list[str]] = {}
    for tk in corr.index:
        peers = corr[tk][corr[tk] >= threshold].index.tolist()
        peers = [p for p in peers if p != tk]
        clusters[tk] = peers
    logger.info(f"[sector] {len(clusters)} 종목 클러스터 빌드, 평균 {sum(len(v) for v in clusters.values())/len(clusters):.1f} peers")
    return clusters


def confluence_count(ticker: str, labels_map: dict[str, list[str]],
                     clusters: dict[str, list[str]]) -> dict:
    """동조 그룹 내에서 라벨 부여된 종목이 몇 개인가.

    labels_map: {ticker: [labels]}, ticker가 라벨 1개 이상 가지면 카운트.
    """
    peers = clusters.get(ticker, [])
    if not peers:
        return {"peer_count": 0, "labeled_peers": 0, "ratio": 0.0}
    labeled = sum(1 for p in peers if labels_map.get(p))
    return {
        "peer_count": len(peers),
        "labeled_peers": labeled,
        "ratio": round(labeled / len(peers), 2) if peers else 0,
    }
