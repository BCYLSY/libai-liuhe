from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CapabilityHealth(BaseModel):
    backend: str
    available: bool
    authentication: Literal["not-required", "configured", "missing", "unknown"]
    operations: list[str]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    authentication_configured: bool
    capabilities: dict[str, CapabilityHealth]


class CapabilityDescription(BaseModel):
    id: str
    backend: str
    operations: list[str]
    allowed: bool


class CapabilitiesResponse(BaseModel):
    client_id: str
    capabilities: list[CapabilityDescription]


class TwitterPost(BaseModel):
    id: str
    text: str
    created_at: str | None = None
    url: str
    author_username: str


class TwitterPostsResponse(BaseModel):
    provider: Literal["twitter-cli", "OpenCLI twitter"] = "twitter-cli"
    username: str
    fetched_at: datetime
    cached: bool
    posts: list[TwitterPost] = Field(default_factory=list)


class StructuredProviderResponse(BaseModel):
    provider: str
    operation: str
    fetched_at: datetime
    cached: bool
    data: Any


class WebReadRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class WebReadResponse(BaseModel):
    provider: Literal["jina-reader"] = "jina-reader"
    url: str
    fetched_at: datetime
    cached: bool
    content: str


class XiaohongshuNoteRequest(BaseModel):
    url: str = Field(min_length=20, max_length=2048)
