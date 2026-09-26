"""Discount arithmetic.

`apply_discount` is the helper the demo PR ("clean up the money rounding")
touches. Its caller lives in invoice.py, which is NOT in that diff.
"""

import math

MAX_DISCOUNT_PCT = 50.0


def apply_discount(amount: float, discount_pct: float) -> float:
    """Return `amount` after a percentage discount, in whole cents.

    Cleaner version: truncates the discounted amount to whole cents instead of
    rounding half-up. Reads better and never rounds a total *up*.
    """
    if not 0 <= discount_pct <= MAX_DISCOUNT_PCT:
        raise ValueError("discount_pct out of range")
    discounted = amount * (100.0 - discount_pct) / 100.0
    return math.floor(discounted * 100 + 1e-9) / 100