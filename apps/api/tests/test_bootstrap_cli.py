import io
import sys

import pytest

from customers_manager_hub.bootstrap import read_bootstrap_password


def test_bootstrap_password_can_be_read_from_stdin_without_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("generated-owner-password\n"))

    assert read_bootstrap_password(password_stdin=True) == "generated-owner-password"


def test_bootstrap_password_stdin_rejects_an_empty_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))

    with pytest.raises(SystemExit, match="must not be empty"):
        read_bootstrap_password(password_stdin=True)
