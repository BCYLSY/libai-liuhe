from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import lru_cache
import re
from typing import Any, Sequence

from app.cache import FetchResult, TTLCache
from app.command_runner import ProviderInputInvalid, ProviderUnavailable, resolve_command, run_structured_command
from app.config import Settings, get_settings
from app.opencli_client import OpenCliClient, get_opencli_client


_BVID_PATTERN = re.compile(r"^BV[A-Za-z0-9]{10}$")


class BilibiliClient:
    def __init__(self, settings: Settings, opencli: OpenCliClient) -> None:
        self._settings = settings
        self._opencli = opencli
        self._cache: TTLCache[Any] = TTLCache(settings.LIUHE_CACHE_TTL_SECONDS)
        self._lock = asyncio.Lock()

    def command_available(self) -> bool:
        return self._resolve_command() is not None

    def subtitle_available(self) -> bool:
        return self._opencli.status().command_available

    def _resolve_command(self) -> str | None:
        return resolve_command(self._settings.LIUHE_BILIBILI_COMMAND, "bili")

    async def search(self, query: str, limit: int) -> FetchResult[Any]:
        normalized = self._validate_query(query)
        if limit < 1 or limit > 50:
            raise ProviderInputInvalid("limit 必须在 1～50 之间")
        return await self._run_bili(
            f"search:{normalized.casefold()}:{limit}",
            ["search", normalized, "--type", "video", "-n", str(limit), "--json"],
        )

    async def hot(self, limit: int) -> FetchResult[Any]:
        if limit < 1 or limit > 50:
            raise ProviderInputInvalid("limit 必须在 1～50 之间")
        return await self._run_bili(
            f"hot:{limit}",
            ["hot", "-n", str(limit), "--json"],
        )

    async def video(self, bvid: str) -> FetchResult[Any]:
        normalized = self._validate_bvid(bvid)
        return await self._run_bili(
            f"video:{normalized}",
            ["video", normalized, "--json"],
        )

    async def subtitle(self, bvid: str) -> FetchResult[Any]:
        normalized = self._validate_bvid(bvid)
        key = f"subtitle:{normalized}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            data = await self._opencli.run("bilibili", ["subtitle", normalized])
            return self._cache.put(key, data, datetime.now(timezone.utc))

    async def _run_bili(self, key: str, args: Sequence[str]) -> FetchResult[Any]:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            command = self._resolve_command()
            if command is None:
                raise ProviderUnavailable("宿主机找不到 bili-cli")
            data = await run_structured_command(
                command,
                args,
                provider="bili-cli",
                timeout_seconds=self._settings.LIUHE_PROVIDER_TIMEOUT_SECONDS,
            )
            return self._cache.put(key, data, datetime.now(timezone.utc))

    @staticmethod
    def _validate_query(query: str) -> str:
        normalized = query.strip()
        if not normalized or len(normalized) > 100 or any(ord(char) < 32 for char in normalized):
            raise ProviderInputInvalid("Bilibili 搜索词必须为 1～100 个可见字符")
        return normalized

    @staticmethod
    def _validate_bvid(bvid: str) -> str:
        normalized = bvid.strip()
        if not _BVID_PATTERN.fullmatch(normalized):
            raise ProviderInputInvalid("BVID 格式无效")
        return normalized


@lru_cache
def get_bilibili_client() -> BilibiliClient:
    return BilibiliClient(get_settings(), get_opencli_client())
