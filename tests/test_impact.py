from app.schemas import ChangeTag

DISCOUNT_V1 = '''\
MAX_DISCOUNT = 0.5


def apply_discount(prices, pct):
    """Discounted line prices, rounded once on the total."""
    pct = min(pct, MAX_DISCOUNT)
    return [p * (1 - pct) for p in prices]
'''

DISCOUNT_V2 = '''\
MAX_DISCOUNT = 0.5


def apply_discount(prices, pct):
    """Cleaned up: round each line."""
    pct = min(pct, MAX_DISCOUNT)
    return [round(p * (1 - pct), 2) for p in prices]
'''

INVOICE = '''\
from pricing.discount import apply_discount


def price_total(prices, pct):
    return round(sum(apply_discount(prices, pct)), 2)
'''

CHECKOUT = '''\
from pricing import invoice


def checkout(cart):
    return invoice.price_total(cart["prices"], cart["pct"])
'''

TEST_DISCOUNT = '''\
from pricing.discount import apply_discount


def test_apply_discount():
    assert apply_discount([10.0], 0.1) == [9.0]
'''

SAMPLE = {
    "sample_project/pricing/__init__.py": "",
    "sample_project/pricing/discount.py": DISCOUNT_V1,
    "sample_project/pricing/invoice.py": INVOICE,
    "sample_project/pricing/checkout.py": CHECKOUT,
    "sample_project/tests/test_discount.py": TEST_DISCOUNT,
}
DISCOUNT = "sample_project/pricing/discount.py"


def rendered(impact):
    return {p.render(): p for p in impact.paths}


def test_scenario1_finds_caller_outside_diff(impact_of):
    impact = impact_of(SAMPLE, {DISCOUNT: DISCOUNT_V2})

    [changed] = impact.changed_symbols
    assert (changed.path, changed.symbol, changed.tags) == (DISCOUNT, "apply_discount", [ChangeTag.BODY_CHANGED])

    paths = rendered(impact)
    direct = paths["price_total → apply_discount"]
    assert direct.outside_diff and not direct.is_test
    assert direct.hops[0].path == "sample_project/pricing/invoice.py"
    assert direct.hops[0].line == 5
    assert paths["test_apply_discount → apply_discount"].is_test
    assert impact.paths[0] is direct  # non-test callers outside the diff sort first
    assert impact.unknowns == []


def test_two_hops_through_module_attribute(impact_of):
    impact = impact_of(SAMPLE, {DISCOUNT: DISCOUNT_V2})
    assert "checkout → price_total → apply_discount" in rendered(impact)


def test_max_hops_bounds_the_search(impact_of):
    impact = impact_of(SAMPLE, {DISCOUNT: DISCOUNT_V2}, max_hops=1)
    assert impact.paths and all(len(p.hops) == 2 for p in impact.paths)


def test_changed_constant_reaches_its_readers(impact_of):
    impact = impact_of(SAMPLE, {DISCOUNT: DISCOUNT_V1.replace("0.5", "0.3")})
    assert [c.symbol for c in impact.changed_symbols] == ["MAX_DISCOUNT"]
    assert "price_total → apply_discount → MAX_DISCOUNT" in rendered(impact)


def test_relative_import_and_callback_reference(impact_of):
    report = "from .discount import apply_discount as ad\n\n\ndef report(rows):\n    return list(map(ad, rows, [0.1] * len(rows)))\n"
    impact = impact_of({**SAMPLE, "sample_project/pricing/report.py": report}, {DISCOUNT: DISCOUNT_V2})
    assert "report → apply_discount" in rendered(impact)


def test_unresolvable_reference_is_unknown_not_safe(impact_of):
    dynamic = (
        "import importlib\n\n\ndef legacy(prices):\n"
        "    mod = importlib.import_module('pricing.discount')\n"
        "    fn = getattr(mod, 'apply_discount')\n"
        "    return mod.apply_discount(prices, 0.1), fn\n"
    )
    impact = impact_of({**SAMPLE, "sample_project/pricing/legacy.py": dynamic}, {DISCOUNT: DISCOUNT_V2})
    reasons = {(u.line, u.reason) for u in impact.unknowns}
    assert reasons == {(6, "dynamic attribute access"), (7, "unresolved reference")}
    assert all(u.may_reach == [f"{DISCOUNT}::apply_discount"] for u in impact.unknowns)


def test_syntax_error_is_unknown_and_not_a_crash(impact_of):
    impact = impact_of(SAMPLE, {"sample_project/pricing/invoice.py": "def price_total(:\n"})
    assert impact.changed_symbols == []
    [unknown] = impact.unknowns
    assert unknown.path == "sample_project/pricing/invoice.py"
    assert unknown.reason.startswith("could not parse on head")


def test_signature_change_added_and_removed(impact_of):
    head = {
        DISCOUNT: DISCOUNT_V1.replace("(prices, pct)", "(prices, pct, cap=None)") + "\n\ndef new_helper():\n    return 1\n",
        "sample_project/pricing/checkout.py": None,
    }
    impact = impact_of(SAMPLE, head)
    tags = {(c.path.rsplit("/", 1)[-1], c.symbol): c.tags for c in impact.changed_symbols}
    assert tags[("discount.py", "apply_discount")] == [ChangeTag.SIGNATURE_CHANGED]
    assert tags[("discount.py", "new_helper")] == [ChangeTag.ADDED]
    assert tags[("checkout.py", "checkout")] == [ChangeTag.REMOVED]


def test_docstring_only_edit_is_not_a_change(impact_of):
    impact = impact_of(SAMPLE, {DISCOUNT: DISCOUNT_V1.replace("rounded once", "rounded a single time")})
    assert impact.changed_symbols == []


def test_assigned_name_is_a_caller(impact_of):
    rates = "from pricing.discount import apply_discount\n\nSTANDARD = apply_discount([100.0], 0.1)\n\n\ndef quote():\n    return STANDARD\n"
    impact = impact_of({**SAMPLE, "sample_project/pricing/rates.py": rates}, {DISCOUNT: DISCOUNT_V2})
    assert "quote → STANDARD → apply_discount" in rendered(impact)


def test_ambiguous_module_name_is_unknown_not_safe(impact_of):
    user = "from util import helper\n\n\ndef run():\n    return helper()\n"
    base = {"a/util.py": "def helper():\n    return 1\n", "b/util.py": "def helper():\n    return 1\n", "c/user.py": user}
    impact = impact_of(base, {"a/util.py": "def helper():\n    return 2\n"})
    assert impact.paths == []
    assert [(u.path, u.reason) for u in impact.unknowns] == [("c/user.py", "unresolved reference")]