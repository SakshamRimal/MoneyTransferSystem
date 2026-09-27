"""
Money conversion helpers.

Internally, all monetary values are integers in "minor units" (cents for
USD, paise for INR, etc.) — see `app/models/account.py` docstring for why.
At the API boundary we accept/return human-friendly Decimal amounts (e.g.
"19.99"). These helpers are the *only* place that conversion should happen.

Only two-decimal-place currencies are supported for simplicity (documented
trade-off in the README); extending to currencies with different exponents
(e.g. JPY with 0, BHD with 3) would mean looking up the exponent per
currency code instead of hardcoding 2.
"""

from decimal import ROUND_HALF_UP, Decimal

_MINOR_UNITS_PER_MAJOR = Decimal("100")


def to_minor_units(amount: Decimal) -> int:
    """Convert a Decimal major-unit amount (e.g. Decimal('19.99')) to an
    integer number of minor units (1999), rounding to the nearest cent using
    banker's-rounding-avoidant HALF_UP semantics."""
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(quantized * _MINOR_UNITS_PER_MAJOR)


def to_major_units(amount_minor_units: int) -> Decimal:
    """Convert an integer number of minor units back to a Decimal major-unit
    amount, e.g. 1999 -> Decimal('19.99')."""
    return (Decimal(amount_minor_units) / _MINOR_UNITS_PER_MAJOR).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
