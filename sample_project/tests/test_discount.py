"""Existing test suite, frozen on the base revision.

Every assertion here stays GREEN after the demo PR. That is the point: the
tests only cover inputs where the rounding change is invisible, and the caller
in invoice.py is not tested at all.
"""

import pytest

from sample_project.pricing import discount, invoice

LINES = [
    {"description": "widget", "unit_price": 10.00, "qty": 2},
    {"description": "gizmo", "unit_price": 5.00, "qty": 4},
]


def test_apply_discount_ten_percent():
    assert discount.apply_discount(100.00, 10.0) == 90.00


def test_apply_discount_half_off():
    assert discount.apply_discount(20.00, 50.0) == 10.00


def test_apply_discount_no_discount():
    assert discount.apply_discount(40.00, 0.0) == 40.00


def test_apply_discount_rejects_out_of_range():
    with pytest.raises(ValueError):
        discount.apply_discount(100.00, 75.0)


def test_subtotal_of_order():
    assert invoice.subtotal(LINES) == 40.00


def test_line_breakdown_gross_amounts():
    assert [row["gross"] for row in invoice.line_breakdown(LINES)] == [20.00, 20.00]
