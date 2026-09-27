"""Unit tests for the money conversion helpers (`app/utils/money.py`)."""

from decimal import Decimal

import pytest

from app.utils.money import to_major_units, to_minor_units


class TestToMinorUnits:
    @pytest.mark.parametrize(
        ("major", "minor"),
        [
            ("0.00", 0),
            ("0.01", 1),
            ("1.00", 100),
            ("19.99", 1999),
            ("100", 10000),
            ("1234567.89", 123456789),
            ("-5.00", -500),
        ],
    )
    def test_exact_conversions(self, major: str, minor: int) -> None:
        assert to_minor_units(Decimal(major)) == minor

    @pytest.mark.parametrize(
        ("major", "expected_minor"),
        [
            ("0.005", 1),  # half a cent rounds up
            ("0.004", 0),
            ("19.995", 2000),  # always HALF_UP, never banker's rounding
            ("2.675", 268),
        ],
    )
    def test_rounds_half_up_at_sub_cent_precision(self, major: str, expected_minor: int) -> None:
        assert to_minor_units(Decimal(major)) == expected_minor

    def test_is_exact_for_many_cents(self) -> None:
        # No floating point involved anywhere: 0.1 + 0.2 style errors
        # must be impossible.
        total = sum(to_minor_units(Decimal("0.10")) for _ in range(10))
        assert total == 100


class TestToMajorUnits:
    @pytest.mark.parametrize(
        ("minor", "major"),
        [
            (0, "0.00"),
            (1, "0.01"),
            (1999, "19.99"),
            (-500, "-5.00"),
            (123456789, "1234567.89"),
        ],
    )
    def test_exact_conversions(self, minor: int, major: str) -> None:
        assert to_major_units(minor) == Decimal(major)


class TestRoundTrip:
    @pytest.mark.parametrize("value", ["0.00", "0.01", "7.50", "19.99", "1000.00", "999999.99"])
    def test_minor_major_round_trip(self, value: str) -> None:
        assert to_major_units(to_minor_units(Decimal(value))) == Decimal(value)
