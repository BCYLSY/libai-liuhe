from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import lru_cache
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from app.cache import FetchResult, TTLCache
from app.command_runner import ProviderInputInvalid
from app.config import Settings, get_settings
from app.opencli_client import OpenCliClient, get_opencli_client


_NOTE_ID_PATTERN = re.compile(r"^[A-Fa-f0-9]{24}$")
_XIAOHONGSHU_HOSTS = frozenset({"xiaohongshu.com", "www.xiaohongshu.com"})
_MINIMUM_INTERVAL_SECONDS = 3.0


class XiaohongshuClient:
    def __init__(self, settings: Settings, opencli: OpenCliClient) -> None:
        self._opencli = opencli
        self._cache: TTLCache[Any] = TTLCache(settings.LIUHE_CACHE_TTL_SECONDS)
        self._lock = asyncio.Lock()

    def command_available(self) -> bool:
        return self._opencli.status().command_available

    async def search(self, query: str, limit: int) -> FetchResult[Any]:
        normalized = self._validate_query(query)
        if limit < 1 or limit > 20:
            raise ProviderInputInvalid("limit 必须在 1～20 之间")
        return await self._run(
            f"search:{normalized.casefold()}:{limit}",
            ["search", normalized, "--limit", str(limit)],
        )

    async def note(self, url: str) -> FetchResult[Any]:
        normalized = self._validate_note_url(url)
        return await self._run(f"note:{normalized}", ["note", normalized])

    async def comments(self, note_id: str) -> FetchResult[Any]:
        normalized = note_id.strip()
        if not _NOTE_ID_PATTERN.fullmatch(normalized):
            raise ProviderInputInvalid("小红书笔记 ID 格式无效")
        return await self._run(f"comments:{normalized}", ["comments", normalized])

    async def _run(self, key: str, args: list[str]) -> FetchResult[Any]:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            data = await self._opencli.run(
                "xiaohongshu",
                args,
                minimum_interval_seconds=_MINIMUM_INTERVAL_SECONDS,
            )
            return self._cache.put(key, data, datetime.now(timezone.utc))

    @staticmethod
    def _validate_query(query: str) -> str:
        normalized = query.strip()
        if not normalized or len(normalized) > 100 or any(ord(char) < 32 for char in normalized):
            raise ProviderInputInvalid("小红书搜索词必须为 1～100 个可见字符")
        return normalized

    @staticmethod
    def _validate_note_url(url: str) -> str:
        value = url.strip()
        try:
            parsed = urlsplit(value)
        except ValueError as error:
            raise ProviderInputInvalid("小红书笔记 URL 无效") from error
        host = (parsed.hostname or "").casefold()
        query = parse_qs(parsed.query)
        path_has_note_id = any(_NOTE_ID_PATTERN.fullmatch(part) for part in parsed.path.split("/"))
        if (
            parsed.scheme != "https"
            or host not in _XIAOHONGSHU_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or not path_has_note_id
            or not query.get("xsec_token")
        ):
            raise ProviderInputInvalid("必须使用搜索结果中带 xsec_token 的完整小红书 HTTPS 链接")
        return urlunsplit(("https", host, parsed.path, parsed.query, ""))


@lru_cache
def get_xiaohongshu_client() -> XiaohongshuClient:
    return XiaohongshuClient(get_settings(), get_opencli_client())
