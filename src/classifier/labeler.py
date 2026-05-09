"""분류 라벨러 — 단타/텐버거/황금자리/매집중."""

from __future__ import annotations


def classify(short_term_signal: dict, tenbagger_signal: dict,
             accumulation: dict, score_total: float,
             score_threshold: float = 50.0) -> list[str]:
    """라벨 부여."""
    labels: list[str] = []

    if short_term_signal.get("triggered"):
        labels.append("⚡단타")

    if tenbagger_signal.get("triggered"):
        labels.append("💎텐버거")

    if "⚡단타" in labels and "💎텐버거" in labels:
        labels.append("⭐황금자리")

    # 🛡️ 안정형 — 백테스트 검증 (60일 보유 승률 86%, 손실폭 -6.59%)
    # 조건: 매집 단계 ON + 수급 점수 60 이상
    if accumulation.get("in_accumulation") and score_total >= 60.0:
        labels.append("🛡️안정형")

    # 출발은 안 했지만 매집 진행 중 OR 점수 양호
    if not labels and (
        (accumulation.get("in_accumulation") and score_total >= 40)
        or score_total >= score_threshold
    ):
        labels.append("🔍매집중")

    return labels
