"""Per-trade risk math for the breakout swing strategy.

SCOPE
=====
This module contains ONLY per-trade quantities — stop placement, trail
placement, R:R screening, and the target rule the R:R screen uses. It
does NOT contain portfolio-level caps (max positions, max heat, sector
caps): those are the engine-replay harness's job (see backtesting/replay.py)
and the single source of truth is there. Same goes for share sizing —
the harness sizes based on per-trade risk + the portfolio risk budget.

Why this split: keeping per-trade math here lets the strategy reason
about a single setup without entanglement with portfolio state, and
keeps the portfolio caps in one place where they apply uniformly across
strategies (current or future).

THE R:R SCREEN AND THE CIRCULARITY PROBLEM
==========================================
A breakout-with-trailing-stop has open-ended upside — by design we
"let winners run" via the Chandelier exit. So there is no fixed take-
profit target, and computing R:R from a self-referential trailing-stop
position would be circular ("the reward is whatever the trail lets it
become; the trail is whatever the high lets it become").

Resolution — measured-move target (Edwards & Magee / classic TA):
    target = breakout_level + (breakout_level − recent_low)
           = 2 × breakout_level − recent_low
where ``breakout_level`` is the prior N-day high (the level being
broken) and ``recent_low`` is the prior N-day low. This projects the
channel height upward from the breakout point. It is the SCREEN target,
not an actual take-profit order: the strategy never places a target
order; the trailing stop governs every exit. We use the screen target
only to ensure setups with shallow rooms-to-run are skipped.

If a setup's measured-move target gives reward < MIN_RR × initial_risk,
the trade is rejected — there's not enough room above the breakout to
justify the per-trade risk. If a winning trade later trails far past
the measured-move target, that's pure gravy (and exactly what the
trailing stop is designed to capture).

This screen is non-circular: ``breakout_level`` and ``recent_low`` are
both observable BEFORE entry from the prior N-day window; neither
depends on the trailing stop or on the eventual exit price.
"""
from __future__ import annotations

from config import ATR_SL_MULTIPLIER, CHANDELIER_ATR_MULT, MIN_RR


def initial_stop(entry: float, atr: float) -> float:
    """Initial hard stop = entry − ATR_SL_MULTIPLIER × ATR.

    ``entry`` is the strategy's reference entry price (close[T] at the
    moment of the decision); the actual fill happens at the next day's
    open, so the realised risk per share is computed by the harness as
    ``fill_price − stop``. The stop returned here is the absolute price
    level the strategy intends.
    """
    if atr <= 0:
        raise ValueError(f"atr must be positive, got {atr}")
    return entry - ATR_SL_MULTIPLIER * atr


def chandelier_stop(highest_high_since_entry: float, atr: float) -> float:
    """Chandelier trailing exit = (highest high since entry) − 3 × ATR.

    The Chandelier ratchets only upward as new highs print; the strategy
    closes on a CLOSE below this level (per the original Le Beau spec).
    The harness tracks ``highest_high`` per open Position so the strategy
    just asks the harness for it and applies this formula.
    """
    if atr <= 0:
        raise ValueError(f"atr must be positive, got {atr}")
    return highest_high_since_entry - CHANDELIER_ATR_MULT * atr


def measured_move_target(breakout_level: float, recent_low: float) -> float:
    """Non-circular target for the R:R screen.

    Projects the prior N-day channel height upward from the breakout
    level: target = breakout_level + (breakout_level − recent_low). See
    the module docstring for the full rationale on why a trailing-stop
    strategy still needs a finite target for the entry screen.

    Caller's responsibility: ``breakout_level`` and ``recent_low`` must
    come from the SAME prior N-day window so the channel height is
    meaningful, and both must be observable at decision time (i.e.,
    from bars strictly before day T, or including T as long as the
    strategy's causal slice respects the harness's cutoff).
    """
    if not (recent_low < breakout_level):
        raise ValueError(
            f"recent_low ({recent_low}) must be strictly below "
            f"breakout_level ({breakout_level}); a degenerate channel "
            f"has no projectable measured move.")
    return 2.0 * breakout_level - recent_low


def rr_screen(*, entry: float, stop: float, target: float,
               min_rr: float = MIN_RR) -> bool:
    """Return True if (target − entry) / (entry − stop) >= ``min_rr``.

    Two failure modes treated differently:

    * stop AT OR ABOVE entry — raises ``ValueError``. This is a logic
      bug: a long-only initial stop derived from ATR can never sit
      above the entry price. Raising surfaces the bug loudly instead
      of letting it be absorbed as a silent rejection.

    * target AT OR BELOW entry — returns ``False``. This is a legitimate
      rejection: the measured-move projection landed at or below the
      entry price (e.g., the breakout gapped past the projected target).
      No exception; the screen simply fails because there is no room
      to project a meaningful reward.
    """
    if not (stop < entry):
        raise ValueError(
            f"stop ({stop}) must be strictly below entry ({entry}) "
            f"for a long-only breakout setup.")
    if target <= entry:
        return False
    reward = target - entry
    risk = entry - stop
    return (reward / risk) >= min_rr
