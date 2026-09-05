from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import Depends, FastAPI, HTTPException, Query

from app.auth import ClientPrincipal, require_client, require_scope
from app.bilibili_client import BilibiliClient, get_bilibili_client
from app.cache import FetchResult
from app.command_runner import (
    ProviderAuthenticationRequired,
    ProviderCommandFailed,
    ProviderError,
    ProviderInputInvalid,
    ProviderOutputInvalid,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.config import APP_NAME, Settings, get_settings
from app.models import (
    CapabilitiesResponse,
    CapabilityDescription,
    CapabilityHealth,
    HealthResponse,
    StructuredProviderResponse,
    TwitterPostsResponse,
    WebReadRequest,
    WebReadResponse,
    XiaohongshuNoteRequest,
)
from app.twitter_client import (
    TwitterClient,
    TwitterCommandFailed,
    TwitterCommandTimeout,
    TwitterCommandUnavailable,
    TwitterCredentialsUnavailable,
    TwitterOutputInvalid,
    TwitterUserInvalid,
    get_twitter_client,
)
from app.web_client import WebClient, get_web_client
from app.xiaohongshu_client import XiaohongshuClient, get_xiaohongshu_client


app = FastAPI(
    title=APP_NAME,
    version="0.2.0",
    description="宿主机侧、只读、白名单化的 Agent Reach 多能力网关。",
)

_OPERATIONS = {
    "twitter": ["user-posts"],
    "web": ["read"],
    "bilibili": ["search", "hot", "video", "subtitle"],
    "xiaohongshu": ["search", "note", "comments"],
}
_BACKENDS = {
    "twitter": "twitter-cli + OpenCLI",
    "web": "jina-reader",
    "bilibili": "bili-cli + OpenCLI",
    "xiaohongshu": "OpenCLI",
}


def _raise_provider_error(error: ProviderError) -> NoReturn:
    if isinstance(error, ProviderInputInvalid):
        raise HTTPException(status_code=422, detail=str(error)) from error
    if isinstance(error, (ProviderUnavailable, ProviderAuthenticationRequired)):
        raise HTTPException(status_code=503, detail=str(error)) from error
    if isinstance(error, ProviderTimeout):
        raise HTTPException(status_code=504, detail=str(error)) from error
    if isinstance(error, (ProviderCommandFailed, ProviderOutputInvalid)):
        raise HTTPException(status_code=502, detail=str(error)) from error
    raise HTTPException(status_code=502, detail="上游能力调用失败") from error


def _structured_response(
    provider: str,
    operation: str,
    result: FetchResult[object],
) -> StructuredProviderResponse:
    return StructuredProviderResponse(
        provider=provider,
        operation=operation,
        fetched_at=result.fetched_at,
        cached=result.cached,
        data=result.data,
    )


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health(
    settings: Annotated[Settings, Depends(get_settings)],
    twitter: Annotated[TwitterClient, Depends(get_twitter_client)],
    bilibili: Annotated[BilibiliClient, Depends(get_bilibili_client)],
    xiaohongshu: Annotated[XiaohongshuClient, Depends(get_xiaohongshu_client)],
) -> HealthResponse:
    twitter_status = twitter.provider_status()
    twitter_authentication = (
        "configured"
        if twitter_status.credentials_configured
        else "unknown"
        if twitter_status.opencli_available
        else "missing"
    )
    bilibili_available = bilibili.command_available()
    xiaohongshu_available = xiaohongshu.command_available()
    authentication_configured = bool(settings.LIUHE_CLIENT_TOKENS)
    capabilities = {
        "twitter": CapabilityHealth(
            backend=_BACKENDS["twitter"],
            available=twitter_status.command_available,
            authentication=twitter_authentication,
            operations=_OPERATIONS["twitter"],
        ),
        "web": CapabilityHealth(
            backend=_BACKENDS["web"],
            available=True,
            authentication="not-required",
            operations=_OPERATIONS["web"],
        ),
        "bilibili": CapabilityHealth(
            backend=_BACKENDS["bilibili"],
            available=bilibili_available,
            authentication="not-required",
            operations=(
                _OPERATIONS["bilibili"]
                if bilibili.subtitle_available()
                else _OPERATIONS["bilibili"][:-1]
            ),
        ),
        "xiaohongshu": CapabilityHealth(
            backend=_BACKENDS["xiaohongshu"],
            available=xiaohongshu_available,
            authentication="unknown" if xiaohongshu_available else "missing",
            operations=_OPERATIONS["xiaohongshu"],
        ),
    }
    ready = (
        authentication_configured
        and twitter_status.command_available
        and twitter_authentication != "missing"
        and bilibili_available
        and xiaohongshu_available
    )
    return HealthResponse(
        status="ok" if ready else "degraded",
        service=APP_NAME,
        authentication_configured=authentication_configured,
        capabilities=capabilities,
    )


@app.get("/v1/capabilities", response_model=CapabilitiesResponse, tags=["system"])
async def capabilities(
    principal: Annotated[ClientPrincipal, Depends(require_client)],
) -> CapabilitiesResponse:
    return CapabilitiesResponse(
        client_id=principal.client_id,
        capabilities=[
            CapabilityDescription(
                id=capability,
                backend=_BACKENDS[capability],
                operations=operations,
                allowed=capability in principal.scopes,
            )
            for capability, operations in _OPERATIONS.items()
        ],
    )


@app.get(
    "/v1/twitter/users/{username}/posts",
    response_model=TwitterPostsResponse,
    tags=["twitter"],
)
async def twitter_user_posts(
    username: str,
    _principal: Annotated[ClientPrincipal, Depends(require_scope("twitter"))],
    settings: Annotated[Settings, Depends(get_settings)],
    twitter: Annotated[TwitterClient, Depends(get_twitter_client)],
    limit: Annotated[int, Query(ge=1)] = 20,
) -> TwitterPostsResponse:
    if limit > settings.LIUHE_TWITTER_MAX_POSTS_PER_REQUEST:
        raise HTTPException(
            status_code=422,
            detail=(
                "limit 不能超过六合配置的单次上限 "
                f"{settings.LIUHE_TWITTER_MAX_POSTS_PER_REQUEST}"
            ),
        )
    try:
        result = await twitter.fetch_user_posts(username, limit)
    except TwitterUserInvalid as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (TwitterCommandUnavailable, TwitterCredentialsUnavailable) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except TwitterCommandTimeout as error:
        raise HTTPException(status_code=504, detail=str(error)) from error
    except (TwitterCommandFailed, TwitterOutputInvalid) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return TwitterPostsResponse(
        provider=result.provider,
        username=result.username,
        fetched_at=result.fetched_at,
        cached=result.cached,
        posts=result.posts,
    )


@app.post("/v1/web/read", response_model=WebReadResponse, tags=["web"])
async def read_web_page(
    payload: WebReadRequest,
    _principal: Annotated[ClientPrincipal, Depends(require_scope("web"))],
    web: Annotated[WebClient, Depends(get_web_client)],
) -> WebReadResponse:
    try:
        normalized_url, result = await web.read(payload.url)
    except ProviderError as error:
        _raise_provider_error(error)
    return WebReadResponse(
        url=normalized_url,
        fetched_at=result.fetched_at,
        cached=result.cached,
        content=result.data,
    )


@app.get("/v1/bilibili/search", response_model=StructuredProviderResponse, tags=["bilibili"])
async def search_bilibili(
    _principal: Annotated[ClientPrincipal, Depends(require_scope("bilibili"))],
    bilibili: Annotated[BilibiliClient, Depends(get_bilibili_client)],
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> StructuredProviderResponse:
    try:
        result = await bilibili.search(q, limit)
    except ProviderError as error:
        _raise_provider_error(error)
    return _structured_response("bili-cli", "search", result)


@app.get("/v1/bilibili/hot", response_model=StructuredProviderResponse, tags=["bilibili"])
async def hot_bilibili(
    _principal: Annotated[ClientPrincipal, Depends(require_scope("bilibili"))],
    bilibili: Annotated[BilibiliClient, Depends(get_bilibili_client)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> StructuredProviderResponse:
    try:
        result = await bilibili.hot(limit)
    except ProviderError as error:
        _raise_provider_error(error)
    return _structured_response("bili-cli", "hot", result)


@app.get(
    "/v1/bilibili/videos/{bvid}",
    response_model=StructuredProviderResponse,
    tags=["bilibili"],
)
async def get_bilibili_video(
    bvid: str,
    _principal: Annotated[ClientPrincipal, Depends(require_scope("bilibili"))],
    bilibili: Annotated[BilibiliClient, Depends(get_bilibili_client)],
) -> StructuredProviderResponse:
    try:
        result = await bilibili.video(bvid)
    except ProviderError as error:
        _raise_provider_error(error)
    return _structured_response("bili-cli", "video", result)


@app.get(
    "/v1/bilibili/videos/{bvid}/subtitle",
    response_model=StructuredProviderResponse,
    tags=["bilibili"],
)
async def get_bilibili_subtitle(
    bvid: str,
    _principal: Annotated[ClientPrincipal, Depends(require_scope("bilibili"))],
    bilibili: Annotated[BilibiliClient, Depends(get_bilibili_client)],
) -> StructuredProviderResponse:
    try:
        result = await bilibili.subtitle(bvid)
    except ProviderError as error:
        _raise_provider_error(error)
    return _structured_response("OpenCLI bilibili", "subtitle", result)


@app.get(
    "/v1/xiaohongshu/search",
    response_model=StructuredProviderResponse,
    tags=["xiaohongshu"],
)
async def search_xiaohongshu(
    _principal: Annotated[ClientPrincipal, Depends(require_scope("xiaohongshu"))],
    xiaohongshu: Annotated[XiaohongshuClient, Depends(get_xiaohongshu_client)],
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> StructuredProviderResponse:
    try:
        result = await xiaohongshu.search(q, limit)
    except ProviderError as error:
        _raise_provider_error(error)
    return _structured_response("OpenCLI xiaohongshu", "search", result)


@app.post(
    "/v1/xiaohongshu/notes/read",
    response_model=StructuredProviderResponse,
    tags=["xiaohongshu"],
)
async def read_xiaohongshu_note(
    payload: XiaohongshuNoteRequest,
    _principal: Annotated[ClientPrincipal, Depends(require_scope("xiaohongshu"))],
    xiaohongshu: Annotated[XiaohongshuClient, Depends(get_xiaohongshu_client)],
) -> StructuredProviderResponse:
    try:
        result = await xiaohongshu.note(payload.url)
    except ProviderError as error:
        _raise_provider_error(error)
    return _structured_response("OpenCLI xiaohongshu", "note", result)


@app.get(
    "/v1/xiaohongshu/notes/{note_id}/comments",
    response_model=StructuredProviderResponse,
    tags=["xiaohongshu"],
)
async def get_xiaohongshu_comments(
    note_id: str,
    _principal: Annotated[ClientPrincipal, Depends(require_scope("xiaohongshu"))],
    xiaohongshu: Annotated[XiaohongshuClient, Depends(get_xiaohongshu_client)],
) -> StructuredProviderResponse:
    try:
        result = await xiaohongshu.comments(note_id)
    except ProviderError as error:
        _raise_provider_error(error)
    return _structured_response("OpenCLI xiaohongshu", "comments", result)
