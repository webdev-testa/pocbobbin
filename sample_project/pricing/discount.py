"""Discount arithmetic.

`apply_discount` is the helper the demo PR ("clean up the money rounding")
touches. Its caller lives in invoice.py, which is NOT in that diff.
"""

MAX_DISCOUNT_PCT = 50.0


def discount_factor(discount_pct: float) -> float:
    """Validate the percentage and return the multiplier to apply."""
    if not 0 <= discount_pct <= MAX_DISCOUNT_PCT:
        raise ValueError("discount_pct out of range")
    return (100.0 - discount_pct) / 100.0


def apply_discount(line_amounts, discount_pct: float) -> float:
    """Apply a percentage discount and return the net amount in whole cents.

    Rounds once, on the order total.
    """
    factor = discount_factor(discount_pct)
    return round(sum(line_amounts) * factor + 1e-9, 2)
