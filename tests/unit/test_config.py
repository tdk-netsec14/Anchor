"""Configuration contract.

These guard the promises ``.env.example`` makes to someone cloning the repo: it
is a complete, loadable description of every setting.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.config import Settings, get_settings

ROOT = Path(__file__).resolve().parent.parent.parent
ENV_EXAMPLE = ROOT / ".env.example"


def _documented_keys() -> set[str]:
    return set(
        re.findall(
            r"^([A-Z][A-Z0-9_]*)=",
            ENV_EXAMPLE.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )


class TestEnvExample:
    def test_file_exists(self) -> None:
        assert ENV_EXAMPLE.is_file(), (
            ".env.example is how a new user configures Anchor; it must be tracked"
        )

    def test_every_setting_is_documented(self) -> None:
        """A setting missing here is one a new user cannot discover."""
        missing = sorted(set(Settings.model_fields) - _documented_keys())
        assert missing == [], f"undocumented settings: {missing}"

    def test_no_setting_is_documented_twice(self) -> None:
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        keys = re.findall(r"^([A-Z][A-Z0-9_]*)=", text, re.MULTILINE)
        duplicates = {k for k in keys if keys.count(k) > 1}
        assert duplicates == set(), f"duplicated keys: {sorted(duplicates)}"

    def test_it_actually_loads(self) -> None:
        """`cp .env.example .env` must not stop the service from starting.

        A blank value for a structured field (the cost maps) is not valid and
        used to raise at import time - which only shows up for a genuinely new
        clone, never for an existing working .env.
        """
        settings = Settings(_env_file=ENV_EXAMPLE, JWT_SECRET="x" * 32)
        assert settings.APP_NAME == "anchor"
        assert settings.COST_INPUT_PER_MTOK == {}

    def test_it_does_not_contain_a_real_secret(self) -> None:
        secret = get_settings().JWT_SECRET
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        assert secret not in text
        # The documented placeholder must still be obviously a placeholder.
        assert "change-me" in text


class TestSettings:
    def test_jwt_secret_is_required(self, monkeypatch) -> None:
        """No default: an unset secret must fail loudly, not sign tokens."""
        monkeypatch.delenv("JWT_SECRET", raising=False)
        with pytest.raises(ValidationError):
            Settings(_env_file=None, JWT_SECRET="")  # noqa: FBT003

    def test_jwt_secret_must_be_long_enough(self) -> None:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, JWT_SECRET="short")  # noqa: FBT003

    def test_fallback_chain_parses_and_drops_blanks(self) -> None:
        settings = Settings(_env_file=None, JWT_SECRET="x" * 32, ROUTER_FALLBACK_CHAIN="a, b ,,c")
        assert settings.fallback_chain == ["a", "b", "c"]

    def test_log_level_is_normalised(self) -> None:
        assert Settings(_env_file=None, JWT_SECRET="x" * 32, LOG_LEVEL="debug").LOG_LEVEL == "DEBUG"
