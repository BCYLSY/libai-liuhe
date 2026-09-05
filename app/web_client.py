from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import lru_cache
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

import httpx
import yaml

from app.cache import FetchResult, TTLCache
from app.command_runner import ProviderCommandFailed, ProviderInputInvalid, ProviderOutputInvalid, ProviderTimeout
from app.config import Settings, get_settings


_JINA_READER_BASE_URL = "https://r.jina.ai"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_CONTENT_CHARS = 200_000


class WebClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport
        self._cache: TTLCache[str] = TTLCache(settings.LIUHE_CACHE_TTL_SECONDS)
        self._lock = asyncio.Lock()

    async def read(self, url: str) -> tuple[str, FetchResult[str]]:
        normalized = self._validate_url(url)
        cached = self._cache.get(normalized)
        if cached is not None:
            return normalized, cached
        async with self._lock:
            cached = self._cache.get(normalized)
            if cached is not None:
                return normalized, cached
            content = await self._fetch(normalized)
            return normalized, self._cache.put(normalized, content, datetime.now(timezone.utc))

    def _load_agent_reach_proxy(self) -> str | None:
        try:
            payload = yaml.safe_load(
                self._settings.agent_reach_config_path.read_text(encoding="utf-8")
            )
        except (OSError, yaml.YAMLError):
            return None
        if not isinstance(payload, dict):
            return None
        value = payload.get("proxy")
        if not isinstance(value, str) or not value.strip():
            return None
        candidate = value.strip()
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        return candidate

    async def _fetch(self, url: str) -> str:
        reader_url = f"{_JINA_READER_BASE_URL}/{url}"
        timeout = httpx.Timeout(self._settings.LIUHE_PROVIDER_TIMEOUT_SECONDS)
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout,
                transport=self._transport,
                trust_env=True,
                proxy=self._load_agent_reach_proxy(),
            ) as client:
                async with client.stream(
                    "GET",
                    reader_url,
                    headers={"Accept": "text/plain", "User-Agent": "libai-liuhe/0.2"},
                ) as response:
                    response.raise_for_status()
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > _MAX_RESPONSE_BYTES:
                            raise ProviderOutputInvalid("Jina Reader 返回内容超过安全上限")
        except httpx.TimeoutException as error:
            raise ProviderTimeout("Jina Reader 请求超时") from error
        except httpx.HTTPStatusError as error:
            raise ProviderCommandFailed(f"Jina Reader 返回 HTTP {error.response.status_code}") from error
        except httpx.RequestError as error:
            raise ProviderCommandFailed("Jina Reader 暂时无法访问") from error

        content = bytes(payload).decode("utf-8", errors="replace").strip()
        if not content:
            raise ProviderOutputInvalid("Jina Reader 未返回网页正文")
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "\n\n[内容已由六合截断]"
        return content

    def _validate_url(self, url: str) -> str:
        value = url.strip()
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise ProviderInputInvalid("网页 URL 无效") from error
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ProviderInputInvalid("网页 URL 只允许使用 http 或 https")
        if parsed.username is not None or parsed.password is not None:
            raise ProviderInputInvalid("网页 URL 不允许包含用户名或密码")
        if port is not None and port not in {80, 443}:
            raise ProviderInputInvalid("网页 URL 只允许使用 80 或 443 端口")

        try:
            host = parsed.hostname.encode("idna").decode("ascii").casefold().rstrip(".")
        except UnicodeError as error:
            raise ProviderInputInvalid("网页域名无效") from error
        try:
            address = ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if not address.is_global:
                raise ProviderInputInvalid("网页 URL 不允许访问本机或私有地址")
        elif "." not in host or host == "localhost" or host.endswith((".localhost", ".local")):
            raise ProviderInputInvalid("网页 URL 不允许访问本机或内部域名")

        allowed_hosts = self._settings.allowed_web_hosts
        if allowed_hosts and not any(host == item or host.endswith(f".{item}") for item in allowed_hosts):
            raise ProviderInputInvalid("网页域名不在六合允许名单中")

        host_for_netloc = f"[{host}]" if address is not None and address.version == 6 else host
        netloc = host_for_netloc if port is None else f"{host_for_netloc}:{port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path or "/", parsed.query, ""))


@lru_cache
def get_web_client() -> WebClient:
    return WebClient(get_settings())
