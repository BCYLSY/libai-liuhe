from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_address
import os
from pathlib import Path
import re
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_CLIENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_HOST_PATTERN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
CAPABILITY_NAMES = frozenset({"twitter", "web", "bilibili", "xiaohongshu"})
APP_NAME = "李白六合 Agent Reach 网关"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    LIUHE_BIND_ADDRESS: str = "127.0.0.1"
    LIUHE_PORT: int = Field(default=9204, ge=1, le=65535)
    LIUHE_LOG_LEVEL: str = "info"

    LIUHE_CLIENT_TOKENS: dict[str, SecretStr] = Field(default_factory=dict)
    LIUHE_CLIENT_SCOPES: dict[str, list[str]] = Field(default_factory=dict)
    LIUHE_TWITTER_MAX_POSTS_PER_REQUEST: int = Field(default=50, ge=1)
    LIUHE_WEB_ALLOWED_HOSTS: str = ""

    LIUHE_CACHE_TTL_SECONDS: int = Field(default=60, ge=0, le=3600)
    LIUHE_PROVIDER_TIMEOUT_SECONDS: float = Field(default=90.0, ge=5.0, le=300.0)
    LIUHE_AGENT_REACH_CONFIG_PATH: str = ""
    LIUHE_TWITTER_COMMAND: str = ""
    LIUHE_BILIBILI_COMMAND: str = ""
    LIUHE_OPENCLI_COMMAND: str = ""

    @field_validator("LIUHE_BIND_ADDRESS")
    @classmethod
    def validate_loopback_address(cls, value: str) -> str:
        candidate = value.strip()
        try:
            address = ip_address(candidate)
        except ValueError as error:
            raise ValueError("LIUHE_BIND_ADDRESS 必须是回环 IP 地址") from error
        if not address.is_loopback:
            raise ValueError("LIUHE_BIND_ADDRESS 只允许使用回环地址")
        return candidate

    @field_validator("LIUHE_CLIENT_TOKENS")
    @classmethod
    def validate_client_tokens(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        for client_id, token in value.items():
            if not _CLIENT_ID_PATTERN.fullmatch(client_id):
                raise ValueError(f"无效的六合调用方 ID：{client_id}")
            secret = token.get_secret_value()
            if len(secret) < 32 or secret.startswith("replace-with-"):
                raise ValueError(f"调用方 {client_id} 的 Token 必须替换为至少 32 个字符的随机值")
        return value

    @field_validator("LIUHE_CLIENT_SCOPES")
    @classmethod
    def validate_client_scopes(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        normalized: dict[str, list[str]] = {}
        for client_id, scopes in value.items():
            if not _CLIENT_ID_PATTERN.fullmatch(client_id):
                raise ValueError(f"无效的六合调用方 ID：{client_id}")
            clean_scopes = sorted({scope.strip().casefold() for scope in scopes if scope.strip()})
            invalid = sorted(set(clean_scopes) - CAPABILITY_NAMES)
            if invalid:
                raise ValueError(f"调用方 {client_id} 包含未知能力：{', '.join(invalid)}")
            if not clean_scopes:
                raise ValueError(f"调用方 {client_id} 至少需要一个能力 scope")
            normalized[client_id] = clean_scopes
        return normalized

    @field_validator("LIUHE_WEB_ALLOWED_HOSTS")
    @classmethod
    def validate_web_allowed_hosts(cls, value: str) -> str:
        hosts = [item.strip().casefold().lstrip(".") for item in value.split(",") if item.strip()]
        invalid = [item for item in hosts if not _HOST_PATTERN.fullmatch(item)]
        if invalid:
            raise ValueError(f"无效的 Web 域名：{', '.join(invalid)}")
        return ",".join(sorted(set(hosts)))

    @model_validator(mode="after")
    def validate_scope_clients(self) -> Self:
        token_clients = set(self.LIUHE_CLIENT_TOKENS)
        scope_clients = set(self.LIUHE_CLIENT_SCOPES)
        if token_clients != scope_clients:
            missing = sorted(token_clients - scope_clients)
            unknown = sorted(scope_clients - token_clients)
            details: list[str] = []
            if missing:
                details.append(f"缺少 scope：{', '.join(missing)}")
            if unknown:
                details.append(f"没有对应 Token：{', '.join(unknown)}")
            raise ValueError("LIUHE_CLIENT_TOKENS 与 LIUHE_CLIENT_SCOPES 调用方不一致（" + "；".join(details) + "）")
        return self

    @property
    def allowed_web_hosts(self) -> frozenset[str]:
        return frozenset(item for item in self.LIUHE_WEB_ALLOWED_HOSTS.split(",") if item)

    def scopes_for_client(self, client_id: str) -> frozenset[str]:
        return frozenset(self.LIUHE_CLIENT_SCOPES.get(client_id, ()))

    @property
    def agent_reach_config_path(self) -> Path:
        configured = self.LIUHE_AGENT_REACH_CONFIG_PATH.strip()
        if configured:
            return Path(configured).expanduser()
        # Agent Reach 在 Windows 上也优先把显式 HOME 作为隔离边界；这里必须
        # 使用同一规则，才能读取它实际写入的那一份配置。
        explicit_home = os.environ.get("HOME", "").strip()
        home = Path(os.path.abspath(explicit_home)) if explicit_home else Path.home()
        return home / ".agent-reach" / "config.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
