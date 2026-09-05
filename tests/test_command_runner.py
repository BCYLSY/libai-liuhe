from __future__ import annotations

import asyncio

import pytest

from app.command_runner import (
    ProviderAuthenticationRequired,
    ProviderOutputInvalid,
    run_structured_command,
)


class StubProcess:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._result = (stdout, stderr)

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._result

    def kill(self) -> None:
        pass


def test_runner_unwraps_json_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create(*args: object, **kwargs: object) -> StubProcess:
        return StubProcess(0, b'{"ok":true,"data":[{"id":"123"}]}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    result = asyncio.run(
        run_structured_command(
            "provider",
            ["read"],
            provider="test-provider",
            timeout_seconds=5,
        )
    )
    assert result == [{"id": "123"}]


def test_runner_maps_yaml_login_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create(*args: object, **kwargs: object) -> StubProcess:
        return StubProcess(
            77,
            b"ok: false\nerror:\n  code: AUTH_REQUIRED\n  message: Login required\n",
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    with pytest.raises(ProviderAuthenticationRequired, match="Login required"):
        asyncio.run(
            run_structured_command(
                "provider",
                ["read"],
                provider="test-provider",
                timeout_seconds=5,
            )
        )


def test_runner_rejects_plain_text_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create(*args: object, **kwargs: object) -> StubProcess:
        return StubProcess(0, b"not structured")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    with pytest.raises(ProviderOutputInvalid):
        asyncio.run(
            run_structured_command(
                "provider",
                ["read"],
                provider="test-provider",
                timeout_seconds=5,
            )
        )
