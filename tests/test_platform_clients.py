from __future__ import annotations

import asyncio

import pytest

from app.bilibili_client import BilibiliClient
from app.command_runner import ProviderInputInvalid
from app.config import Settings
from app.xiaohongshu_client import XiaohongshuClient


class StubOpenCli:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], float]] = []

    async def run(
        self,
        adapter: str,
        args: list[str],
        *,
        minimum_interval_seconds: float = 0,
    ) -> object:
        self.calls.append((adapter, args, minimum_interval_seconds))
        return {"adapter": adapter, "args": args}


def test_bilibili_subtitle_validates_bvid_and_uses_opencli() -> None:
    opencli = StubOpenCli()
    client = BilibiliClient(Settings(_env_file=None), opencli)  # type: ignore[arg-type]
    first = asyncio.run(client.subtitle("BV1mDtL6hE4x"))
    second = asyncio.run(client.subtitle("BV1mDtL6hE4x"))
    assert first.cached is False
    assert second.cached is True
    assert opencli.calls == [("bilibili", ["subtitle", "BV1mDtL6hE4x"], 0)]

    with pytest.raises(ProviderInputInvalid, match="BVID"):
        asyncio.run(client.video("https://www.bilibili.com/video/BV1mDtL6hE4x"))


def test_xiaohongshu_note_requires_full_search_result_url() -> None:
    opencli = StubOpenCli()
    client = XiaohongshuClient(Settings(_env_file=None), opencli)  # type: ignore[arg-type]
    url = "https://www.xiaohongshu.com/explore/68b77cb1000000001d014abc?xsec_token=token"
    result = asyncio.run(client.note(url))
    assert result.data == {"adapter": "xiaohongshu", "args": ["note", url]}
    assert opencli.calls[0][2] == 3.0

    for invalid in (
        "https://www.xiaohongshu.com/explore/68b77cb1000000001d014abc",
        "https://evil.example/explore/68b77cb1000000001d014abc?xsec_token=token",
        "https://www.xiaohongshu.com/?xsec_token=token",
    ):
        with pytest.raises(ProviderInputInvalid, match="xsec_token"):
            asyncio.run(client.note(invalid))
