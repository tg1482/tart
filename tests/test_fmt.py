from tartifacts import fmt


def test_usd_always_shows_two_decimals():
    assert fmt.usd(1234567.891) == "$1,234,567.89"
    assert fmt.usd(-42.5) == "-$42.50"
    assert fmt.usd(0.0) == "$0.00"
    assert fmt.usd(5911.0) == "$5,911.00"


def test_grouped_drops_decimals_for_whole_numbers():
    assert fmt.grouped(933.7) == "933.70"
    assert fmt.grouped(5911.0) == "5,911"
