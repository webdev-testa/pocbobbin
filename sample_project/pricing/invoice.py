"""Order invoice totals.

Calls apply_discount() from discount.py. This file is deliberately NOT part of
the demo PR diff: the whole point of the PoC is that a change to discount.py
silently changes what price_total() returns.
"""

from typing import Dict, List, Sequence

from . import discount


def line_gross_amounts(lines: Sequence[Dict]) -> List[float]:
    """Gross amount per order line, rounded to whole cents."""
    return [round(line["unit_price"] * line["qty"], 2) for line in lines]


def subtotal(lines: Sequence[Dict]) -> float:
    """Gross amount for the whole order."""
    return round(sum(line_gross_amounts(lines)), 2)


def price_total(lines: Sequence[Dict], discount_pct: float = 0.0) -> float:
    """Net total payable for an order: subtotal with the discount applied."""
    return discount.apply_discount(subtotal(lines), discount_pct)


def line_breakdown(lines: Sequence[Dict]) -> List[Dict]:
    """Per-line gross amounts, for receipts."""
    return [
        {"description": line.get("description", ""), "gross": gross}
        for line, gross in zip(lines, line_gross_amounts(lines))
    ]
