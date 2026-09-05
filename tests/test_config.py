from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


EXAMPLE_PATH = Path(__file__).parents[1] / ".env.example"


def test_defaults_use_loopback_and_port_9204() -> None:
    settings = Settings(_env_file=None)
    assert settings.LIUHE_BIND_ADDRESS == "127.0.0.1"
    assert settings.LIUHE_PORT == 9204
    assert settings.LIUHE_TWITTER_MAX_POSTS_PER_REQUEST == 50


def test_public_bind_address_is_rejected() -> None:
    with pytest.raises(ValidationError, match="回环"):
        Settings(_env_file=None, LIUHE_BIND_ADDRESS="0.0.0.0")


def test_placeholder_client_token_is_rejected() -> None:
    with pytest.raises(ValidationError, match="必须替换"):
        Settings(
            _env_file=None,
            LIUHE_CLIENT_TOKENS={
                "changfeng": "replace-with-a-random-token-at-least-32-characters"
            },
        )


def test_tokens_and_scopes_must_have_the_same_clients() -> None:
    with pytest.raises(ValidationError, match="缺少 scope"):
        Settings(
            _env_file=None,
            LIUHE_CLIENT_TOKENS={"changfeng": "a" * 32},
        )


def test_unknown_scope_is_rejected() -> None:
    with pytest.raises(ValidationError, match="未知能力"):
        Settings(
            _env_file=None,
            LIUHE_CLIENT_TOKENS={"changfeng": "a" * 32},
            LIUHE_CLIENT_SCOPES={"changfeng": ["shell"]},
        )


def test_env_example_matches_all_runtime_settings() -> None:
    example_keys = {
        line.split("=", 1)[0]
        for line in EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    assert example_keys == {
        field_name
        for field_name in Settings.model_fields
        if field_name.startswith("LIUHE_")
    }

    settings = Settings(
        _env_file=EXAMPLE_PATH,
        LIUHE_CLIENT_TOKENS={"changfeng": "a" * 32},
    )
    assert settings.LIUHE_PORT == 9204


def test_agent_reach_config_path_follows_explicit_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = Settings(_env_file=None)
    assert settings.agent_reach_config_path == tmp_path / ".agent-reach" / "config.yaml"
