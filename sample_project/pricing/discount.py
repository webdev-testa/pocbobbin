"""Discount arithmetic.

`apply_discount` is the helper the demo PR ("clean up the money rounding")
touches. Its caller lives in invoice.py, which is NOT in that diff.

This revision is the "broken setup" case: it imports a helper that the author
forgot to commit, so the module cannot be imported at all.
"""

from pricing.helpers import round_money

MAX_DISCOUNT_PCT = 50.0


def apply_discount(amount: float, discount_pct: float) -> float:
    """Return `amount` after a percentage discount, in whole cents."""
    if not 0 <= discount_pct <= MAX_DISCOUNT_PCT:
        raise ValueError("discount_pct out of range")
    discounted = amount * (100.0 - discount_pct) / 100.0
    return round_money(discounted)