from __future__ import annotations

from dataclasses import dataclass
from hmac import compare_digest
from typing import Annotated, Callable, Coroutine, Any

from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings


@dataclass(frozen=True)
class ClientPrincipal:
    client_id: str
    scopes: frozenset[str]


async def require_client(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClientPrincipal:
    if not settings.LIUHE_CLIENT_TOKENS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="六合尚未配置调用方 Token",
        )

    client_id = request.headers.get("X-Liuhe-Client", "").strip()
    authorization = request.headers.get("Authorization", "")
    scheme, separator, supplied_token = authorization.partition(" ")
    configured_token = settings.LIUHE_CLIENT_TOKENS.get(client_id)

    expected = configured_token.get_secret_value() if configured_token else "0" * 32
    valid = (
        bool(client_id)
        and separator == " "
        and scheme.casefold() == "bearer"
        and bool(supplied_token)
        and compare_digest(supplied_token, expected)
        and configured_token is not None
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="六合调用凭据无效",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return ClientPrincipal(client_id, settings.scopes_for_client(client_id))


def require_scope(
    scope: str,
) -> Callable[[ClientPrincipal], Coroutine[Any, Any, ClientPrincipal]]:
    async def dependency(
        principal: Annotated[ClientPrincipal, Depends(require_client)],
    ) -> ClientPrincipal:
        if scope not in principal.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"调用方未获授权使用 {scope} 能力",
            )
        return principal

    return dependency
