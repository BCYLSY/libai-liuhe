from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.command_runner import ProviderInputInvalid
from app.config import Settings
from app.web_client import WebClient


def test_web_reader_uses_jina_and_cache() -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="# Example\n\nBody")

    client = WebClient(
        Settings(_env_file=None, LIUHE_CACHE_TTL_SECONDS=60),
        transport=httpx.MockTransport(handler),
    )

    async def scenario() -> None:
        normalized, first = await client.read("https://example.com/article?q=1#fragment")
        _, second = await client.read("https://example.com/article?q=1")
        assert normalized == "https://example.com/article?q=1"
        assert first.cached is False
        assert second.cached is True
        assert second.data.startswith("# Example")

    asyncio.run(scenario())
    assert calls == ["https://r.jina.ai/https://example.com/article?q=1"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://10.0.0.1/",
        "http://localhost/",
        "http://service.local/",
        "file:///etc/passwd",
        "https://user:password@example.com/",
        "https://example.com:8443/",
    ],
)
def test_web_reader_rejects_unsafe_urls(url: str) -> None:
    client = WebClient(Settings(_env_file=None))
    with pytest.raises(ProviderInputInvalid):
        asyncio.run(client.read(url))


def test_web_reader_enforces_optional_host_allowlist() -> None:
    client = WebClient(
        Settings(_env_file=None, LIUHE_WEB_ALLOWED_HOSTS="example.com")
    )
    with pytest.raises(ProviderInputInvalid, match="允许名单"):
        asyncio.run(client.read("https://openai.com/"))


def test_web_reader_reuses_agent_reach_proxy(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("proxy: http://127.0.0.1:7892\n", encoding="utf-8")
    client = WebClient(
        Settings(
            _env_file=None,
            LIUHE_AGENT_REACH_CONFIG_PATH=str(config_path),
        )
    )

    assert client._load_agent_reach_proxy() == "http://127.0.0.1:7892"
