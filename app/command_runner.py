from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping, Sequence

import yaml


logger = logging.getLogger(__name__)
_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_AUTH_ERROR_CODES = frozenset(
    {"AUTH_REQUIRED", "LOGIN_REQUIRED", "UNAUTHENTICATED", "AUTHENTICATION_REQUIRED"}
)


class ProviderError(RuntimeError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class ProviderAuthenticationRequired(ProviderError):
    pass


class ProviderTimeout(ProviderError):
    pass


class ProviderCommandFailed(ProviderError):
    pass


class ProviderOutputInvalid(ProviderError):
    pass


class ProviderInputInvalid(ProviderError):
    pass


def resolve_command(configured: str, *names: str) -> str | None:
    configured = configured.strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)
        return shutil.which(configured)

    for name in names:
        discovered = shutil.which(name)
        if discovered:
            return discovered
    for name in names:
        for suffix in (".exe", ".cmd", ""):
            candidate = Path.home() / ".local" / "bin" / f"{name}{suffix}"
            if candidate.is_file():
                return str(candidate)
    return None


def _parse_structured_output(value: bytes) -> Any:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProviderOutputInvalid("上游命令未返回 UTF-8 内容") from error
    text = _ANSI_ESCAPE.sub("", text).strip()
    if not text:
        raise ProviderOutputInvalid("上游命令未返回内容")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise ProviderOutputInvalid("上游命令未返回有效 JSON 或 YAML") from error


def _extract_error(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "", ""
    error = payload.get("error")
    if not isinstance(error, dict):
        return "", ""
    code = str(error.get("code") or "").strip()
    message = str(error.get("message") or "").strip()
    return code, message[:500]


async def run_structured_command(
    command: str,
    args: Sequence[str],
    *,
    provider: str,
    timeout_seconds: float,
    env_overrides: Mapping[str, str] | None = None,
) -> Any:
    child_env = os.environ.copy()
    child_env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "NO_COLOR": "1",
            "FORCE_COLOR": "0",
        }
    )
    if env_overrides:
        child_env.update(env_overrides)
    process_options: dict[str, object] = {}
    if os.name == "nt":
        process_options["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        process = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_env,
            **process_options,
        )
    except OSError as error:
        raise ProviderUnavailable(f"宿主机无法启动 {provider}") from error

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError as error:
        process.kill()
        await process.communicate()
        raise ProviderTimeout(f"{provider} 执行超时") from error

    if len(stdout) + len(stderr) > _MAX_OUTPUT_BYTES:
        raise ProviderOutputInvalid(f"{provider} 返回内容超过安全上限")

    payload: Any = None
    for candidate in (stdout, stderr):
        if not candidate.strip():
            continue
        try:
            payload = _parse_structured_output(candidate)
            break
        except ProviderOutputInvalid:
            continue

    code, message = _extract_error(payload)
    if process.returncode or (isinstance(payload, dict) and payload.get("ok") is False):
        logger.warning(
            "上游命令失败：provider=%s returncode=%s stdout_bytes=%s stderr_bytes=%s code=%s",
            provider,
            process.returncode,
            len(stdout),
            len(stderr),
            code or "unknown",
        )
        if process.returncode == 77 or code.upper() in _AUTH_ERROR_CODES:
            raise ProviderAuthenticationRequired(message or f"{provider} 需要登录")
        raise ProviderCommandFailed(message or f"{provider} 调用失败")
    if payload is None:
        raise ProviderOutputInvalid(f"{provider} 未返回有效结构化数据")
    if isinstance(payload, dict) and payload.get("ok") is True:
        return payload.get("data")
    if not isinstance(payload, (dict, list)):
        raise ProviderOutputInvalid(f"{provider} 返回格式无效")
    return payload
