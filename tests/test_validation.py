import pytest

from pricebot.validation import ValidationError, parse_price, validate_target


@pytest.mark.parametrize("text,expected", [("85000", 85000), ("$85,000.50", 85000.5), (" 1,234 ", 1234), ("0.01", 0.01)])
def test_parse_ok(text, expected):
    assert float(parse_price(text)) == expected


@pytest.mark.parametrize("text", ["abc", "", "-5", "0", "1e5", "1.234", "85,000.123", "$", "--1", "1.2.3"])
def test_parse_rejects(text):
    with pytest.raises(ValidationError):
        parse_price(text)


def v(text, current=80000, active=0):
    return validate_target(text, current, active, 30, 20)


def test_valid_target():
    assert v("81000") == 81000


def test_equal_to_current_rejected():
    with pytest.raises(ValidationError, match="current price"):
        v("80000")


def test_deviation_band():
    assert v("104000") == 104000  # exactly +30%
    with pytest.raises(ValidationError, match="typo"):
        v("104001")
    with pytest.raises(ValidationError, match="typo"):
        v("55999")


def test_max_active_alerts():
    with pytest.raises(ValidationError, match="20 active"):
        v("81000", active=20)
    assert v("81000", active=19) == 81000
