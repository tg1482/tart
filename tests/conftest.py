"""Isolate every test from the real `~/.tart`.

Without this a test reads the developer's actual index, live processes and
roots — so the suite passes or fails depending on whose machine it runs on
and what happens to be open. It also means a test could delete real state.
Autouse so no test has to remember.

One env var rather than a monkeypatch per constant: every module resolves
its paths through `paths.home()`, so a module added later is isolated by
default instead of quietly writing to the real home until someone notices.
"""

import pytest

from tartifacts import paths


@pytest.fixture(autouse=True)
def isolated_tart_home(tmp_path, monkeypatch):
    home = tmp_path / "tart-home"
    monkeypatch.setenv(paths.ENV_VAR, str(home))
    return home
