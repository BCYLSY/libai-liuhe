from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from time import monotonic
from typing import Any, Sequence

from app.command_runner import ProviderUnavailable, resolve_command, run_structured_command
from app.config import Settings, get_settings


@dataclass(frozen=True)
class OpenCliStatus:
    command_available: bool


class OpenCliClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._last_completed_at: dict[str, float] = {}

    def status(self) -> OpenCliStatus:
        return OpenCliStatus(command_available=self._resolve_command() is not None)

    def _resolve_command(self) -> str | None:
        return resolve_command(self._settings.LIUHE_OPENCLI_COMMAND, "opencli")

    async def run(
        self,
        adapter: str,
        args: Sequence[str],
        *,
        minimum_interval_seconds: float = 0,
    ) -> Any:
        command = self._resolve_command()
        if command is None:
            raise ProviderUnavailable("宿主机找不到 OpenCLI")

        async with self._lock:
            last_completed = self._last_completed_at.get(adapter)
            if last_completed is not None and minimum_interval_seconds > 0:
                remaining = minimum_interval_seconds - (monotonic() - last_completed)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            try:
                return await run_structured_command(
                    command,
                    [
                        adapter,
                        *args,
                        "-f",
                        "json",
                        "--window",
                        "background",
                        "--site-session",
                        "ephemeral",
                        "--keep-tab",
                        "false",
                    ],
                    provider=f"OpenCLI {adapter}",
                    timeout_seconds=self._settings.LIUHE_PROVIDER_TIMEOUT_SECONDS,
                )
            finally:
                self._last_completed_at[adapter] = monotonic()


@lru_cache
def get_opencli_client() -> OpenCliClient:
    return OpenCliClient(get_settings())
