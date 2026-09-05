from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.bilibili_client import get_bilibili_client
from app.cache import FetchResult
from app.config import CAPABILITY_NAMES, Settings, get_settings
from app.main import app
from app.models import TwitterPost
from app.twitter_client import ProviderStatus, TwitterFetchResult, get_twitter_client
from app.web_client import get_web_client
from app.xiaohongshu_client import get_xiaohongshu_client


TOKEN = "a" * 32
NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)
AUTH_HEADERS = {
    "X-Liuhe-Client": "changfeng",
    "Authorization": f"Bearer {TOKEN}",
}


class StubTwitterClient:
    def provider_status(self) -> ProviderStatus:
        return ProviderStatus(command_available=True, credentials_configured=True)

    async def fetch_user_posts(self, username: str, limit: int) -> TwitterFetchResult:
        return TwitterFetchResult(
            username=username,
            posts=[
                TwitterPost(
                    id="123",
                    text="hello",
                    url=f"https://x.com/{username}/status/123",
                    author_username=username,
                )
            ],
            fetched_at=NOW,
            cached=False,
        )


class StubBilibiliClient:
    def command_available(self) -> bool:
        return True

    def subtitle_available(self) -> bool:
        return True

    async def search(self, query: str, limit: int) -> FetchResult[object]:
        return FetchResult([{"bvid": "BV1mDtL6hE4x", "title": query}], NOW, False)

    async def hot(self, limit: int) -> FetchResult[object]:
        return FetchResult([{"bvid": "BV1mDtL6hE4x"}], NOW, False)

    async def video(self, bvid: str) -> FetchResult[object]:
        return FetchResult({"bvid": bvid}, NOW, False)

    async def subtitle(self, bvid: str) -> FetchResult[object]:
        return FetchResult([{"content": "字幕"}], NOW, False)


class StubXiaohongshuClient:
    def command_available(self) -> bool:
        return True

    async def search(self, query: str, limit: int) -> FetchResult[object]:
        return FetchResult([{"title": query}], NOW, False)

    async def note(self, url: str) -> FetchResult[object]:
        return FetchResult({"url": url}, NOW, False)

    async def comments(self, note_id: str) -> FetchResult[object]:
        return FetchResult([{"note_id": note_id}], NOW, False)


class StubWebClient:
    async def read(self, url: str) -> tuple[str, FetchResult[str]]:
        return url, FetchResult("# Example", NOW, False)


def build_client(scopes: set[str] | None = None) -> TestClient:
    enabled_scopes = sorted(scopes if scopes is not None else CAPABILITY_NAMES)
    settings = Settings(
        _env_file=None,
        LIUHE_CLIENT_TOKENS={"changfeng": TOKEN},
        LIUHE_CLIENT_SCOPES={"changfeng": enabled_scopes},
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_twitter_client] = lambda: StubTwitterClient()
    app.dependency_overrides[get_bilibili_client] = lambda: StubBilibiliClient()
    app.dependency_overrides[get_xiaohongshu_client] = lambda: StubXiaohongshuClient()
    app.dependency_overrides[get_web_client] = lambda: StubWebClient()
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_health_reports_all_capabilities_without_exposing_tokens() -> None:
    with build_client() as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert set(response.json()["capabilities"]) == CAPABILITY_NAMES
    assert TOKEN not in response.text


def test_protected_endpoint_requires_client_credentials() -> None:
    with build_client() as client:
        response = client.get("/v1/twitter/users/btibor91/posts")
    assert response.status_code == 401


def test_scope_blocks_unapproved_capability() -> None:
    with build_client({"twitter"}) as client:
        response = client.get(
            "/v1/bilibili/search",
            params={"q": "OpenAI", "limit": 1},
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 403


def test_capabilities_show_client_permissions() -> None:
    with build_client({"twitter", "web"}) as client:
        response = client.get("/v1/capabilities", headers=AUTH_HEADERS)
    allowed = {item["id"]: item["allowed"] for item in response.json()["capabilities"]}
    assert allowed == {
        "twitter": True,
        "web": True,
        "bilibili": False,
        "xiaohongshu": False,
    }


def test_twitter_endpoint_returns_normalized_posts() -> None:
    with build_client() as client:
        response = client.get(
            "/v1/twitter/users/btibor91/posts?limit=20",
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 200
    assert response.json()["posts"][0]["url"] == "https://x.com/btibor91/status/123"


def test_twitter_endpoint_rejects_limit_above_configured_maximum() -> None:
    with build_client() as client:
        response = client.get(
            "/v1/twitter/users/OpenAI/posts?limit=51",
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 422
    assert "单次上限 50" in response.json()["detail"]


def test_web_endpoint_returns_markdown() -> None:
    with build_client() as client:
        response = client.post(
            "/v1/web/read",
            json={"url": "https://example.com/article"},
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 200
    assert response.json()["provider"] == "jina-reader"
    assert response.json()["content"] == "# Example"


def test_bilibili_search_returns_structured_data() -> None:
    with build_client() as client:
        response = client.get(
            "/v1/bilibili/search",
            params={"q": "OpenAI", "limit": 1},
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 200
    assert response.json()["provider"] == "bili-cli"
    assert response.json()["data"][0]["bvid"] == "BV1mDtL6hE4x"


def test_xiaohongshu_search_returns_structured_data() -> None:
    with build_client() as client:
        response = client.get(
            "/v1/xiaohongshu/search",
            params={"q": "人工智能", "limit": 1},
            headers=AUTH_HEADERS,
        )
    assert response.status_code == 200
    assert response.json()["provider"] == "OpenCLI xiaohongshu"
    assert response.json()["data"][0]["title"] == "人工智能"
