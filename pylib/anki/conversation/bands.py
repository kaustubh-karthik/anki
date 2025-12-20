# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from enum import Enum

FSRS5_DEFAULT_DECAY = 0.5


class RetrievabilityBand(str, Enum):
    COLD = "cold"
    FRAGILE = "fragile"
    STRETCH = "stretch"
    SUPPORT = "support"
    NEW = "new"


def compute_retrievability(
    stability: float, elapsed_days: float, decay: float = FSRS5_DEFAULT_DECAY
) -> float:
    """Compute current recall probability R from FSRS stability/elapsed days.

    FSRS forgetting curve:
      R = ((elapsed/stability) * factor + 1)^(-decay)
      factor = (0.9 ** (1 / -decay)) - 1
    """

    stability = float(stability)
    elapsed_days = float(elapsed_days)
    decay = float(decay)
    if stability <= 0.0 or decay <= 0.0:
        return 0.0
    factor = (0.9 ** (1.0 / -decay)) - 1.0
    r = ((elapsed_days / stability) * factor + 1.0) ** (-decay)
    if r < 0.0:
        return 0.0
    if r > 1.0:
        return 1.0
    return r


def classify_item(
    retrievability: float,
    mastery: dict[str, int],
    thresholds: tuple[float, float, float] = (0.4, 0.6, 0.85),
) -> RetrievabilityBand:
    """Classify with telemetry adjustments.

    Base band is derived from retrievability R. Telemetry then optionally
    downgrades (high dont_know/lookup) or upgrades (high conv success).
    """

    r = float(retrievability)
    cold, fragile, stretch = thresholds
    if r < cold:
        base = RetrievabilityBand.COLD
    elif r < fragile:
        base = RetrievabilityBand.FRAGILE
    elif r < stretch:
        base = RetrievabilityBand.STRETCH
    else:
        base = RetrievabilityBand.SUPPORT

    dont_know = int(mastery.get("dont_know", 0))
    lookup_count = int(mastery.get("lookup_count", 0))
    conv_success = int(mastery.get("conv_success_count", 0))

    band_order = (
        RetrievabilityBand.COLD,
        RetrievabilityBand.FRAGILE,
        RetrievabilityBand.STRETCH,
        RetrievabilityBand.SUPPORT,
    )
    idx = band_order.index(base)

    if (dont_know >= 2 or lookup_count >= 3) and idx > 0:
        return band_order[idx - 1]
    if conv_success >= 3 and idx < (len(band_order) - 1):
        return band_order[idx + 1]
    return base
