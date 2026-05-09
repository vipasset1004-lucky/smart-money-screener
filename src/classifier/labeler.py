"""분류 라벨러 — 단타/텐버거/황금자리/매집중."""

from __future__ import annotations


def classify(short_term_signal: dict, tenbagger_signal: dict,
             accumulation: dict, score_total: float,
             marcap: int | None = None,
             ensemble: dict | None = None,
             score_threshold: float = 50.0,
             stable_max_marcap: int = 5_000_000_000_000) -> list[str]:
    """라벨 부여 — v5 앙상블 통합.

    ensemble: src.analysis.ensemble.evaluate_ensemble 결과
      {scores, ensemble, masters_70plus, masters_80plus}
    """
    labels: list[str] = []
    en = ensemble or {}
    en_score = float(en.get("ensemble", 0))
    m70 = int(en.get("masters_70plus", 0))
    m80 = int(en.get("masters_80plus", 0))
    en_scores = en.get("scores", {}) or {}
    # 65점+ 대가 수 (현 분포에 맞춘 보조 임계)
    m65 = sum(1 for v in en_scores.values() if v >= 65)

    if short_term_signal.get("triggered"):
        labels.append("⚡단타")

    if tenbagger_signal.get("triggered"):
        labels.append("💎텐버거")

    # ⭐ 황금자리 — 4명+ 80점 OR 단타+텐버거 동시
    if m80 >= 4 or ("⚡단타" in labels and "💎텐버거" in labels):
        labels.append("⭐황금자리")

    # 🏛 대가합의 — 3명+ 65점 AND 앙상블 50+ (현실 분포 반영)
    if m65 >= 3 and en_score >= 50 and "⭐황금자리" not in labels:
        labels.append("🏛대가합의")

    # 🛡️ 안정형 — 백테스트 검증 매집 ON + 수급 60+
    if accumulation.get("in_accumulation") and score_total >= 60.0:
        labels.append("🛡️안정형")

    # 출발은 안 했지만 매집 진행 중 OR 점수 양호
    if not labels and (
        (accumulation.get("in_accumulation") and score_total >= 40)
        or score_total >= score_threshold
        or en_score >= 50
    ):
        labels.append("🔍매집중")

    return labels
