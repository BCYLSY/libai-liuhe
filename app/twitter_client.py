from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from time import monotonic

import yaml

from app.command_runner import (
    ProviderAuthenticationRequired,
    ProviderCommandFailed,
    ProviderOutputInvalid,
    ProviderTimeout,
    ProviderUnavailable,
    resolve_command,
    run_structured_command,
)
from app.config import Settings, get_settings
from app.models import TwitterPost


logger = logging.getLogger(__name__)
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_MAX_OUTPUT_BYTES = 2 * 1024 * 1024


class TwitterGatewayError(RuntimeError):
    """六合调用 Twitter 后端失败。"""


class TwitterCommandUnavailable(TwitterGatewayError):
    pass


class TwitterCredentialsUnavailable(TwitterGatewayError):
    pass


class TwitterCommandTimeout(TwitterGatewayError):
    pass


class TwitterCommandFailed(TwitterGatewayError):
    pass


class TwitterOutputInvalid(TwitterGatewayError):
    pass


class TwitterUserInvalid(TwitterGatewayError):
    pass


@dataclass(frozen=True)
class ProviderStatus:
    command_available: bool
    credentials_configured: bool
    opencli_available: bool = False


@dataclass(frozen=True)
class TwitterFetchResult:
    username: str
    posts: list[TwitterPost]
    fetched_at: datetime
    cached: bool
    provider: str = "twitter-cli"


@dataclass(frozen=True)
class _CacheEntry:
    requested_limit: int
    posts: list[TwitterPost]
    fetched_at: datetime
    expires_at: float
    provider: str


class TwitterClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    def provider_status(self) -> ProviderStatus:
        twitter_cli_available = self._resolve_command() is not None
        opencli_available = self._resolve_opencli_command() is not None
        return ProviderStatus(
            command_available=twitter_cli_available or opencli_available,
            credentials_configured=self._load_credentials() is not None,
            opencli_available=opencli_available,
        )

    async def fetch_user_posts(self, username: str, limit: int) -> TwitterFetchResult:
        normalized = username.strip().lstrip("@")
        if not _USERNAME_PATTERN.fullmatch(normalized):
            raise TwitterUserInvalid("X 用户名格式无效")
        normalized = normalized.casefold()
        maximum = self._settings.LIUHE_TWITTER_MAX_POSTS_PER_REQUEST
        if limit < 1 or limit > maximum:
            raise TwitterUserInvalid(f"limit 必须在 1～{maximum} 之间")

        cached = self._cached_result(normalized, limit)
        if cached is not None:
            return cached

        async with self._lock:
            cached = self._cached_result(normalized, limit)
            if cached is not None:
                return cached
            provider = self._provider_name()
            # 上游即使忽略 -n 参数，六合也只返回调用方明确请求的数量。
            posts = (await self._fetch_from_cli(normalized, limit))[:limit]
            fetched_at = datetime.now(timezone.utc)
            entry = _CacheEntry(
                requested_limit=limit,
                posts=posts,
                fetched_at=fetched_at,
                expires_at=monotonic() + self._settings.LIUHE_CACHE_TTL_SECONDS,
                provider=provider,
            )
            if self._settings.LIUHE_CACHE_TTL_SECONDS > 0:
                self._cache[normalized] = entry
            return TwitterFetchResult(normalized, posts, fetched_at, False, provider)

    def _cached_result(self, username: str, limit: int) -> TwitterFetchResult | None:
        entry = self._cache.get(username)
        if entry is None or entry.expires_at <= monotonic() or entry.requested_limit < limit:
            return None
        return TwitterFetchResult(
            username,
            entry.posts[:limit],
            entry.fetched_at,
            True,
            entry.provider,
        )

    def _resolve_command(self) -> str | None:
        configured = self._settings.LIUHE_TWITTER_COMMAND.strip()
        if configured:
            configured_path = Path(configured).expanduser()
            if configured_path.is_file():
                return str(configured_path)
            return shutil.which(configured)

        discovered = shutil.which("twitter") or shutil.which("twitter.exe")
        if discovered:
            return discovered
        for candidate in (
            Path.home() / ".local" / "bin" / "twitter.exe",
            Path.home() / ".local" / "bin" / "twitter",
        ):
            if candidate.is_file():
                return str(candidate)
        return None

    def _resolve_opencli_command(self) -> str | None:
        return resolve_command(self._settings.LIUHE_OPENCLI_COMMAND, "opencli")

    def _provider_name(self) -> str:
        if self._resolve_command() is not None and self._load_credentials() is not None:
            return "twitter-cli"
        if self._resolve_opencli_command() is not None:
            return "OpenCLI twitter"
        return "twitter-cli"

    def _load_credentials(self) -> dict[str, str] | None:
        credentials = {
            "TWITTER_AUTH_TOKEN": os.environ.get("TWITTER_AUTH_TOKEN", "").strip(),
            "TWITTER_CT0": os.environ.get("TWITTER_CT0", "").strip(),
        }
        if credentials["TWITTER_AUTH_TOKEN"] and credentials["TWITTER_CT0"]:
            return credentials

        try:
            payload = yaml.safe_load(
                self._settings.agent_reach_config_path.read_text(encoding="utf-8")
            )
        except (OSError, yaml.YAMLError):
            payload = None
        if isinstance(payload, dict):
            if not credentials["TWITTER_AUTH_TOKEN"]:
                value = payload.get("twitter_auth_token")
                credentials["TWITTER_AUTH_TOKEN"] = value.strip() if isinstance(value, str) else ""
            if not credentials["TWITTER_CT0"]:
                value = payload.get("twitter_ct0")
                credentials["TWITTER_CT0"] = value.strip() if isinstance(value, str) else ""
        if credentials["TWITTER_AUTH_TOKEN"] and credentials["TWITTER_CT0"]:
            return credentials
        return None

    async def _fetch_from_cli(self, username: str, limit: int) -> list[TwitterPost]:
        command = self._resolve_command()
        credentials = self._load_credentials()
        if command is None or credentials is None:
            opencli_command = self._resolve_opencli_command()
            if opencli_command is not None:
                return await self._fetch_from_opencli(opencli_command, username, limit)
            if command is None:
                raise TwitterCommandUnavailable("宿主机找不到 twitter-cli 或 OpenCLI")
            raise TwitterCredentialsUnavailable(
                "Agent Reach 尚未配置完整 X Cookie，且 OpenCLI 不可用"
            )

        child_env = os.environ.copy()
        child_env.update(credentials)
        child_env.update(
            {
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        process_options: dict[str, object] = {}
        if os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NO_WINDOW
        # twitter-cli 在显式 Cookie 失效时会尝试读取本机浏览器 Cookie。六合只
        # 接受 Agent Reach 已保存的 Cookie，因此给子进程一个临时空用户目录，
        # 阻止它越过这条凭据边界；目录会在本次调用结束后自动删除。
        with tempfile.TemporaryDirectory(prefix="libai-liuhe-twitter-") as isolated_home:
            child_env.update(
                {
                    "HOME": isolated_home,
                    "USERPROFILE": isolated_home,
                    "APPDATA": isolated_home,
                    "LOCALAPPDATA": isolated_home,
                }
            )
            try:
                process = await asyncio.create_subprocess_exec(
                    command,
                    "user-posts",
                    username,
                    "-n",
                    str(limit),
                    "--json",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=child_env,
                    **process_options,
                )
            except OSError as error:
                raise TwitterCommandUnavailable("宿主机无法启动 twitter-cli") from error

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self._settings.LIUHE_PROVIDER_TIMEOUT_SECONDS,
                )
            except TimeoutError as error:
                process.kill()
                await process.communicate()
                raise TwitterCommandTimeout("twitter-cli 执行超时") from error

        if len(stdout) > _MAX_OUTPUT_BYTES:
            raise TwitterOutputInvalid("twitter-cli 返回内容超过安全上限")
        if process.returncode:
            logger.warning(
                "twitter-cli 调用失败：username=%s returncode=%s stderr_bytes=%s",
                username,
                process.returncode,
                len(stderr),
            )
            raise TwitterCommandFailed("twitter-cli 调用失败")
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TwitterOutputInvalid("twitter-cli 未返回有效 JSON") from error
        return self._parse_posts(payload, username)

    async def _fetch_from_opencli(
        self,
        command: str,
        username: str,
        limit: int,
    ) -> list[TwitterPost]:
        try:
            payload = await run_structured_command(
                command,
                [
                    "twitter",
                    "tweets",
                    username,
                    "--limit",
                    str(limit),
                    "-f",
                    "yaml",
                ],
                provider="OpenCLI twitter",
                timeout_seconds=self._settings.LIUHE_PROVIDER_TIMEOUT_SECONDS,
            )
        except ProviderAuthenticationRequired as error:
            raise TwitterCredentialsUnavailable("OpenCLI 的 X 会话需要重新登录") from error
        except ProviderUnavailable as error:
            raise TwitterCommandUnavailable("宿主机无法启动 OpenCLI") from error
        except ProviderTimeout as error:
            raise TwitterCommandTimeout("OpenCLI Twitter 执行超时") from error
        except ProviderOutputInvalid as error:
            raise TwitterOutputInvalid("OpenCLI Twitter 返回格式无效") from error
        except ProviderCommandFailed as error:
            raise TwitterCommandFailed("OpenCLI Twitter 调用失败") from error
        return self._parse_posts(payload, username)

    @staticmethod
    def _parse_posts(payload: object, username: str) -> list[TwitterPost]:
        if isinstance(payload, dict) and payload.get("ok") is True:
            payload = payload.get("data")
        if not isinstance(payload, list):
            raise TwitterOutputInvalid("twitter-cli 返回格式不是动态列表")

        posts: list[TwitterPost] = []
        seen_ids: set[str] = set()
        expected_username = username.casefold()
        for item in payload:
            if not isinstance(item, dict) or item.get("isRetweet") is True or item.get("is_retweet") is True:
                continue
            raw_id = item.get("id")
            text = item.get("text")
            post_id = str(raw_id) if isinstance(raw_id, (str, int)) else ""
            if not post_id.isdigit() or post_id in seen_ids or not isinstance(text, str) or not text.strip():
                continue
            author = item.get("author")
            if isinstance(author, dict):
                author_username = author.get("screenName")
            elif isinstance(author, str):
                author_username = author
            else:
                author_username = username
            if not isinstance(author_username, str) or not author_username:
                author_username = username
            if author_username.casefold() != expected_username:
                continue
            raw_created_at = item.get("createdAtISO") or item.get("createdAt") or item.get("created_at")
            created_at = raw_created_at if isinstance(raw_created_at, str) and raw_created_at else None
            posts.append(
                TwitterPost(
                    id=post_id,
                    text=text.strip(),
                    created_at=created_at,
                    url=f"https://x.com/{username}/status/{post_id}",
                    author_username=author_username,
                )
            )
            seen_ids.add(post_id)
        if payload and not posts:
            raise TwitterOutputInvalid("twitter-cli 返回了无法解析的动态数据")
        return posts


@lru_cache
def get_twitter_client() -> TwitterClient:
    return TwitterClient(get_settings())
