"""Client tests against a mock transport -- no slskd, no network."""

import httpx2 as httpx
import pytest

from slskd_mcp.client import Client, SlskdError


def make(handler) -> Client:
    c = Client("http://slskd.test/", api_key="k")
    c._http = httpx.AsyncClient(
        headers={"X-API-Key": "k"}, transport=httpx.MockTransport(handler)
    )
    return c


def record(seen: list, payload=None, status=200, text=None):
    def handler(request):
        seen.append(request)
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=payload)

    return handler


@pytest.mark.anyio
async def test_paths_are_v0_and_key_is_attached():
    """The spec's v{version} placeholder is a generation bug; it's always v0."""
    seen: list = []
    async with make(record(seen, {"id": "abc"})) as c:
        await c.start_search("Planet Rhythm")
    req = seen[0]
    assert str(req.url) == "http://slskd.test/api/v0/searches"
    assert req.method == "POST"
    assert req.headers["X-API-Key"] == "k"


@pytest.mark.anyio
async def test_trailing_slash_on_base_url_does_not_double_up():
    seen: list = []
    async with make(record(seen, [])) as c:
        await c.search_responses("xyz")
    assert str(seen[0].url) == "http://slskd.test/api/v0/searches/xyz/responses"


@pytest.mark.anyio
async def test_health_is_outside_the_api_prefix_and_needs_no_auth():
    seen: list = []
    async with make(record(seen, {})) as c:
        assert await c.health() is True
    assert str(seen[0].url) == "http://slskd.test/health"


@pytest.mark.anyio
async def test_error_status_raises_with_the_body_attached():
    async with make(record([], status=401, text="Unauthorized")) as c:
        with pytest.raises(SlskdError) as e:
            await c.searches()
    assert e.value.status == 401
    assert "401" in str(e.value) and "Unauthorized" in str(e.value)


@pytest.mark.anyio
async def test_empty_body_is_not_a_decode_error():
    """Several endpoints answer 204 with no body."""
    async with make(record([], status=204, text="")) as c:
        assert await c.stop_search("id") is None


@pytest.mark.anyio
async def test_list_endpoints_return_a_list_even_when_null():
    async with make(record([], text="null")) as c:
        assert await c.searches() == []
        assert await c.search_responses("x") == []


@pytest.mark.anyio
async def test_unreachable_host_is_a_slskderror_not_a_transport_error():
    def boom(request):
        raise httpx.ConnectError("refused")

    async with make(boom) as c:
        with pytest.raises(SlskdError) as e:
            await c.searches()
    assert "could not reach slskd" in str(e.value)
    # health() is a probe, so it reports False rather than raising
    async with make(boom) as c:
        assert await c.health() is False


@pytest.mark.anyio
async def test_enqueue_posts_the_file_list_to_the_user_path():
    seen: list = []
    async with make(record(seen, {})) as c:
        await c.enqueue("someone", [{"filename": "a.flac", "size": 1}])
    assert str(seen[0].url) == "http://slskd.test/api/v0/transfers/downloads/someone"
    assert seen[0].method == "POST"


def test_from_env_requires_a_key(monkeypatch):
    monkeypatch.delenv("SLSKD_API_KEY", raising=False)
    with pytest.raises(SlskdError):
        Client.from_env()
