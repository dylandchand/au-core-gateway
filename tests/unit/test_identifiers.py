import random

import pytest

from gateway.identifiers.au import (ihi_problems, luhn_check_digit, luhn_valid, make_ihi,
                                    make_medicare, medicare_problems)

# IHIs and Medicare numbers that appear in the official AU Core 2.0.0 / AU Base 6.0.0 examples.
OFFICIAL_IHIS = ["8003608333647261", "8003608833357361"]
OFFICIAL_MEDICARE = ["22968184811", "32788511952"]


@pytest.mark.parametrize("ihi", OFFICIAL_IHIS)
def test_official_example_ihis_are_valid(ihi):
    assert ihi_problems(ihi) == []


def test_luhn_digit_matches_known_value():
    assert luhn_check_digit("800360833364726") == "1"
    assert luhn_valid("8003608333647261")
    assert not luhn_valid("8003608333647262")


def test_each_ihi_rule_is_reported_separately():
    assert any("inv-ihi-value-2" in p for p in ihi_problems("8003608333647262"))
    assert any("inv-ihi-value-0" in p for p in ihi_problems("80036083336472"))
    wrong_prefix = "800361833364726"
    wrong_prefix += luhn_check_digit(wrong_prefix)
    assert ihi_problems(wrong_prefix) == ["inv-ihi-value-1: must start with 800360"]


def test_generated_ihis_are_always_valid():
    rng = random.Random(1)
    assert all(ihi_problems(make_ihi(rng)) == [] for _ in range(500))


@pytest.mark.parametrize("number", OFFICIAL_MEDICARE)
def test_official_example_medicare_numbers_are_valid(number):
    assert medicare_problems(number) == []


def test_medicare_problems():
    assert "check digit" in " ".join(medicare_problems("22968184711"))
    assert "first digit" in " ".join(medicare_problems("12968184811"))
    assert "10 digits" in " ".join(medicare_problems("2296818481"[:9]))


def test_generated_medicare_numbers_are_always_valid():
    rng = random.Random(2)
    assert all(medicare_problems(make_medicare(rng)) == [] for _ in range(500))
