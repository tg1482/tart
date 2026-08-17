"""env_file parsing — the common ground of .env dialects, nothing more."""

import pytest

from tartifacts import envfile


def test_key_value_comments_and_blanks():
    parsed = envfile.parse(
        "# secrets for the ads artifact\n"
        "\n"
        "API_KEY=abc123\n"
        "REGION = us-east-1\n"
    )
    assert parsed == {"API_KEY": "abc123", "REGION": "us-east-1"}


def test_export_prefix_and_quotes_tolerated():
    """An existing shell-sourced secrets file works unchanged."""
    parsed = envfile.parse(
        'export TOKEN="tok-with-#-and-=signs"\n'
        "export EMPTY=''\n"
        "PLAIN=unquoted value with spaces\n"
    )
    assert parsed["TOKEN"] == "tok-with-#-and-=signs"
    assert parsed["EMPTY"] == ""
    assert parsed["PLAIN"] == "unquoted value with spaces"


def test_non_assignments_are_skipped_not_fatal():
    """A stray shell construct in a sourced file must not take the whole
    load down — the keys that ARE there still load."""
    parsed = envfile.parse(
        "if [ -f other ]; then\n"
        "source other.env\n"
        "GOOD=1\n"
        "fi\n"
    )
    assert parsed == {"GOOD": "1"}


def test_mismatched_quotes_kept_verbatim():
    assert envfile.parse("K='half\n")["K"] == "'half"


def test_missing_file_raises_oserror(tmp_path):
    """Loud by contract: the downstream failure (an API rejecting a blank
    key) points anywhere but at the missing file."""
    with pytest.raises(OSError):
        envfile.load(tmp_path / "absent.env")


def test_load_reads_a_real_file(tmp_path):
    secrets = tmp_path / "s.env"
    secrets.write_text("A=1\nB=2\n")
    assert envfile.load(secrets) == {"A": "1", "B": "2"}
