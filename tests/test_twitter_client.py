from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

import app.twitter_client as twitter_client_module
from app.config import Settings
from app.models import TwitterPost
from app.twitter_client import TwitterClient, TwitterOutputInvalid, TwitterUserInvalid


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "LIUHE_CLIENT_TOKENS": {"changfeng": "a" * 32},
        "LIUHE_CLIENT_SCOPES": {"changfeng": ["twitter"]},
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_parse_posts_filters_retweets_wrong_authors_and_duplicates() -> None:
    payload = [
        {
            "id": "123",
            "text": " first ",
            "createdAtISO": "2026-09-03T08:00:00Z",
            "author": {"screenName": "btibor91"},
        },
        {
            "id": "124",
            "text": "retweet",
            "isRetweet": True,
            "author": {"screenName": "someone"},
        },
        {"id": "125", "text": "wrong", "author": {"screenName": "someone"}},
        {"id": "123", "text": "duplicate", "author": {"screenName": "btibor91"}},
    ]

    posts = TwitterClient._parse_posts(payload, "btibor91")

    assert posts == [
        TwitterPost(
            id="123",
            text="first",
            created_at="2026-09-03T08:00:00Z",
            url="https://x.com/btibor91/status/123",
            author_username="btibor91",
        )
    ]


def test_parse_posts_accepts_agent_reach_envelope() -> None:
    posts = TwitterClient._parse_posts(
        {
            "ok": True,
            "data": [
                {"id": 123, "text": "hello", "author": {"screenName": "btibor91"}}
            ],
        },
        "btibor91",
    )
    assert posts[0].id == "123"


def test_parse_posts_accepts_opencli_shape() -> None:
    posts = TwitterClient._parse_posts(
        [
            {
                "id": "456",
                "text": "hello from OpenCLI",
                "author": "OpenAI",
                "created_at": "Thu Sep 03 19:32:15 +0000 2026",
                "is_retweet": False,
            }
        ],
        "openai",
    )

    assert posts == [
        TwitterPost(
            id="456",
            text="hello from OpenCLI",
            created_at="Thu Sep 03 19:32:15 +0000 2026",
            url="https://x.com/openai/status/456",
            author_username="OpenAI",
        )
    ]


def test_parse_posts_rejects_unknown_nonempty_shape() -> None:
    try:
        TwitterClient._parse_posts([{"unexpected": True}], "btibor91")
    except TwitterOutputInvalid:
        pass
    else:
        raise AssertionError("invalid provider output should be rejected")


def test_fetch_uses_cache() -> None:
    class StubTwitterClient(TwitterClient):
        def __init__(self, settings: Settings) -> None:
            super().__init__(settings)
            self.calls = 0

        async def _fetch_from_cli(self, username: str, limit: int) -> list[TwitterPost]:
            self.calls += 1
            return [
                TwitterPost(
                    id="123",
                    text="hello",
                    url=f"https://x.com/{username}/status/123",
                    author_username=username,
                )
            ]

    async def scenario() -> None:
        client = StubTwitterClient(build_settings(LIUHE_CACHE_TTL_SECONDS=60))
        first = await client.fetch_user_posts("btibor91", 20)
        second = await client.fetch_user_posts("btibor91", 20)
        assert first.cached is False
        assert second.cached is True
        assert client.calls == 1

    asyncio.run(scenario())


def test_fetch_accepts_any_valid_username() -> None:
    class StubTwitterClient(TwitterClient):
        async def _fetch_from_cli(self, username: str, limit: int) -> list[TwitterPost]:
            return []

    result = asyncio.run(
        StubTwitterClient(build_settings()).fetch_user_posts("OpenAI", 1)
    )

    assert result.username == "openai"


def test_fetch_never_returns_more_than_requested() -> None:
    class StubTwitterClient(TwitterClient):
        async def _fetch_from_cli(self, username: str, limit: int) -> list[TwitterPost]:
            return [
                TwitterPost(
                    id=str(index),
                    text=f"post {index}",
                    url=f"https://x.com/{username}/status/{index}",
                    author_username=username,
                )
                for index in range(1, 4)
            ]

    result = asyncio.run(
        StubTwitterClient(build_settings()).fetch_user_posts("OpenAI", 2)
    )

    assert len(result.posts) == 2


def test_fetch_respects_configured_maximum() -> None:
    client = TwitterClient(
        build_settings(LIUHE_TWITTER_MAX_POSTS_PER_REQUEST=5)
    )

    with pytest.raises(TwitterUserInvalid, match="1～5"):
        asyncio.run(client.fetch_user_posts("OpenAI", 6))


def test_load_credentials_reads_agent_reach_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TWITTER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_CT0", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "twitter_auth_token: saved-auth\ntwitter_ct0: saved-ct0\n",
        encoding="utf-8",
    )
    client = TwitterClient(
        build_settings(LIUHE_AGENT_REACH_CONFIG_PATH=str(config_path))
    )
    assert client._load_credentials() == {
        "TWITTER_AUTH_TOKEN": "saved-auth",
        "TWITTER_CT0": "saved-ct0",
    }


def test_missing_explicit_cookies_falls_back_to_opencli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_structured_command(
        command: str,
        args: list[str],
        **kwargs: object,
    ) -> object:
        captured.update(command=command, args=args, kwargs=kwargs)
        return [
            {
                "id": "456",
                "text": "hello",
                "author": "OpenAI",
                "is_retweet": False,
            }
        ]

    client = TwitterClient(build_settings())
    monkeypatch.setattr(client, "_resolve_command", lambda: "twitter")
    monkeypatch.setattr(client, "_load_credentials", lambda: None)
    monkeypatch.setattr(client, "_resolve_opencli_command", lambda: "opencli")
    monkeypatch.setattr(
        twitter_client_module,
        "run_structured_command",
        fake_run_structured_command,
    )

    posts = asyncio.run(client._fetch_from_cli("openai", 1))

    assert posts[0].id == "456"
    assert captured["command"] == "opencli"
    assert captured["args"] == [
        "twitter",
        "tweets",
        "openai",
        "--limit",
        "1",
        "-f",
        "yaml",
    ]


def test_cli_process_cannot_fall_back_to_browser_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class StubProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b'{"ok":true,"data":[]}', b""

    async def fake_create_subprocess_exec(*args: object, **kwargs: object) -> StubProcess:
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return StubProcess()

    client = TwitterClient(build_settings())
    monkeypatch.setattr(client, "_resolve_command", lambda: "twitter")
    monkeypatch.setattr(
        client,
        "_load_credentials",
        lambda: {
            "TWITTER_AUTH_TOKEN": "saved-auth",
            "TWITTER_CT0": "saved-ct0",
        },
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    posts = asyncio.run(client._fetch_from_cli("btibor91", 20))

    assert posts == []
    assert captured["args"] == (
        "twitter",
        "user-posts",
        "btibor91",
        "-n",
        "20",
        "--json",
    )
    child_env = captured["env"]
    assert isinstance(child_env, dict)
    isolated_paths = {
        child_env["HOME"],
        child_env["USERPROFILE"],
        child_env["APPDATA"],
        child_env["LOCALAPPDATA"],
    }
    assert len(isolated_paths) == 1
    assert not Path(next(iter(isolated_paths))).exists()
    assert child_env["HOME"] != os.environ.get("HOME")
