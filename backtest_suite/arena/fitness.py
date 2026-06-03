# backtest_suite/arena/fitness.py
"""Scoring multi-obiettivo dell'arena.

Parte pura (questo blocco): normalizzazione z-score robusta, composite sulla
popolazione della generazione, verdetto di validazione. La valutazione che
richiede un backtest (`evaluate`) è aggiunta nel Task 3.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §6.
"""
from __future__ import annotations

import math
from statistics import median

from backtest_suite.arena.types import CandidateMetrics, Weights


def robust_zscore(values: list[float]) -> list[float]:
    """z-score robusto (mediana / MAD). I valori non-finiti mappano a 0.0.

    scale = 1.4826·MAD; se MAD == 0 si usa la pstdev dei finiti; se anche quella
    è 0 (input costante o un solo finito) si ritorna tutti zero.
    """
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return [0.0] * len(values)
    med = median(finite)
    mad = median([abs(v - med) for v in finite])
    if mad > 0:
        scale = 1.4826 * mad
    else:
        # tutti i finiti uguali alla mediana → nessuna dispersione
        scale = 0.0
        n = len(finite)
        if n >= 2:
            mu = sum(finite) / n
            var = sum((v - mu) ** 2 for v in finite) / n
            scale = math.sqrt(var)
        if scale == 0.0:
            return [0.0] * len(values)
    return [((v - med) / scale) if math.isfinite(v) else 0.0 for v in values]


def composite_scores(cms: list[CandidateMetrics], weights: Weights) -> list[float]:
    """Composite multi-obiettivo z-scorato sulla popolazione (spec §6).

    composite = w_q·z(quality) + w_c·z(consistency) − w_d·z(drawdown).
    I candidati `failed` ricevono -inf (retrocessi in coda, mai eliminati).
    """
    zq = robust_zscore([c.quality for c in cms])
    zc = robust_zscore([c.consistency for c in cms])
    zd = robust_zscore([c.drawdown for c in cms])
    out: list[float] = []
    for i, c in enumerate(cms):
        if c.failed:
            out.append(float("-inf"))
            continue
        out.append(weights.quality * zq[i]
                   + weights.consistency * zc[i]
                   - weights.drawdown * zd[i])
    return out


def validation_verdict(cm: CandidateMetrics) -> str:
    """robust | weak | likely_overfit dal segnale walk-forward OOS.

    - likely_overfit: candidato fallito oppure quality OOS non positiva.
    - robust:        quality > 0 e bassa dispersione tra finestre
                     (stdev ≤ 0.5·|quality|; consistency = -stdev).
    - weak:          tutto il resto.
    """
    if cm.failed or cm.quality <= 0.0:
        return "likely_overfit"
    if cm.consistency >= -0.5 * abs(cm.quality):
        return "robust"
    return "weak"
