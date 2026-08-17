from tartifacts import fmt


def test_usd_always_shows_two_decimals():
    assert fmt.usd(1234567.891) == "$1,234,567.89"
    assert fmt.usd(-42.5) == "-$42.50"
    assert fmt.usd(0.0) == "$0.00"
    assert fmt.usd(5911.0) == "$5,911.00"


def test_grouped_drops_decimals_for_whole_numbers():
    assert fmt.grouped(933.7) == "933.70"
    assert fmt.grouped(5911.0) == "5,911"


def test_age_picks_the_largest_fitting_unit():
    assert fmt.age(45) == "45s"
    assert fmt.age(90) == "1m"
    assert fmt.age(3 * 3600 + 40 * 60) == "3h"
    assert fmt.age(93784) == "1d"


def test_age_never_goes_negative():
    # A clock skew or a status file from the future reads as "0s", not "-3m".
    assert fmt.age(-180) == "0s"
