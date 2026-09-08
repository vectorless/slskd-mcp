"""Client tests against a mock transport -- no slskd, no network."""

import json

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


def test_from_env_requires_a_key(monkeypatch):
    monkeypatch.delenv("SLSKD_API_KEY", raising=False)
    with pytest.raises(SlskdError):
        Client.from_env()


@pytest.mark.anyio
async def test_enqueue_uses_the_batch_endpoint_not_the_deprecated_one():
    """POST transfers/downloads/{username} is marked deprecated in the spec."""
    seen: list = []
    async with make(record(seen, {"batch": {"transfers": []}, "failures": []})) as c:
        await c.enqueue("someone", [{"filename": "a.flac", "size": 1}])
    assert str(seen[0].url) == "http://slskd.test/api/v0/transfers/downloads/batches"
    body = json.loads(seen[0].content)
    assert body["username"] == "someone"
    assert body["files"] == [{"filename": "a.flac", "size": 1}]
    assert "searchId" not in body  # omitted when not supplied


@pytest.mark.anyio
async def test_enqueue_carries_search_id_and_destination_when_given():
    seen: list = []
    async with make(record(seen, {})) as c:
        await c.enqueue("u", [], search_id="s-1", destination="/tmp/x")
    body = json.loads(seen[0].content)
    assert body["searchId"] == "s-1"
    assert body["options"] == {"destination": "/tmp/x"}


@pytest.mark.anyio
async def test_enqueue_returns_the_status_because_200_means_total_failure():
    """The batch endpoint uses 200 for 'everything failed' and 201 for success."""
    payload = {"batch": {"transfers": []}, "failures": [{"filename": "a", "message": "no"}]}
    async with make(record([], payload, status=200)) as c:
        status, body = await c.enqueue("u", [{"filename": "a", "size": 1}])
    assert status == 200
    assert body["failures"][0]["message"] == "no"


@pytest.mark.anyio
async def test_cancel_sends_remove_as_a_lowercase_query_flag():
    seen: list = []
    async with make(record(seen, status=204, text="")) as c:
        await c.cancel_download("u", "t-1", remove=True)
    assert seen[0].url.path == "/api/v0/transfers/downloads/u/t-1"
    assert seen[0].url.params["remove"] == "true"
    assert seen[0].method == "DELETE"

    seen.clear()
    async with make(record(seen, status=204, text="")) as c:
        await c.cancel_download("u", "t-1")
    assert seen[0].url.params["remove"] == "false"


@pytest.mark.anyio
async def test_download_status_hits_the_single_transfer_path():
    seen: list = []
    async with make(record(seen, {"id": "t-1", "placeInQueue": 4})) as c:
        t = await c.download_status("u", "t-1")
    assert str(seen[0].url) == "http://slskd.test/api/v0/transfers/downloads/u/t-1"
    assert t["placeInQueue"] == 4
