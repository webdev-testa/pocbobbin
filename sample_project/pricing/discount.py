"""Discount arithmetic.

`apply_discount` is the helper the demo PR ("clean up the money rounding")
touches. Its caller lives in invoice.py, which is NOT in that diff.
"""

MAX_DISCOUNT_PCT = 30.0


def apply_discount(amount: float, discount_pct: float) -> float:
    """Return `amount` after a percentage discount, in whole cents.

    Rounds once, on the order total, half-up.
    """
    if not 0 <= discount_pct <= MAX_DISCOUNT_PCT:
        raise ValueError("discount_pct out of range")
    discounted = amount * (100.0 - discount_pct) / 100.0
    return round(discounted + 1e-9, 2)