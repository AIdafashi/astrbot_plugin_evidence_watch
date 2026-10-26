import json
from unittest.mock import AsyncMock

import httpx
import pytest

from watch import ChangeClient, WatchStore

ID = "11111111-1111-4111-8111-111111111111"


def setup(tmp_path, snapshots):
    store = WatchStore(tmp_path / "watch.sqlite")
    store.add(ID, "group", "alice", "https://example.com/changelog", "新增视频编辑")
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["x-api-key"] == "test-secret"
        if request.method == "POST":
            assert json.loads(request.content)["url"] == "https://example.com/changelog"
            return httpx.Response(201, json={"uuid": ID})
        if request.url.path.endswith("/history"):
            return httpx.Response(
                200, json={str(ts): "ignored-server-path" for ts in snapshots}
            )
        return httpx.Response(
            200, text=snapshots[int(request.url.path.rsplit("/", 1)[1])]
        )

    return (
        store,
        ChangeClient(
            "https://monitor.example.com", "test-secret", httpx.MockTransport(handler)
        ),
        requests,
    )


@pytest.mark.asyncio
async def test_baseline_noise_match_and_persistent_dedup(tmp_path):
    snapshots = {
        100: "title\ntext",
        200: "title\ntext\nad",
        300: "title\ntext\nad\n新增视频编辑",
    }
    store, client, _ = setup(tmp_path, snapshots)
    judge = AsyncMock(
        side_effect=[
            {"match": False},
            {"match": True, "reason": "新增了关注的功能", "evidence": ["新增视频编辑"]},
        ]
    )
    send = AsyncMock(return_value=True)
    await client.poll(store.list()[0], store, judge, send)
    assert judge.await_count == 2
    assert send.await_count == 1
    message = send.call_args.args[1]
    assert (
        "https://example.com/changelog" in message
        and "+新增视频编辑" in message
        and "1970-" in message
    )
    restored = WatchStore(store.path)
    await client.poll(restored.list()[0], restored, judge, send)
    assert send.await_count == 1
    assert restored.list()[0]["stamp"] == 300
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["hallucinated_evidence", "string_boolean", "send_failure", "model_failure"]
)
async def test_failure_retains_cursor_for_retry(tmp_path, mode):
    store, client, _ = setup(tmp_path, {100: "old", 200: "new"})
    store.advance(ID, 100, "old")
    judge = AsyncMock(
        return_value={"match": True, "reason": "change", "evidence": ["new"]}
    )
    send = AsyncMock(return_value=True)
    if mode == "hallucinated_evidence":
        judge.return_value["evidence"] = ["not in changes"]
    if mode == "string_boolean":
        judge.return_value["match"] = "false"
    if mode == "send_failure":
        send.return_value = False
    if mode == "model_failure":
        judge.side_effect = RuntimeError("provider unavailable")
    with pytest.raises((ValueError, RuntimeError)):
        await client.poll(store.list()[0], store, judge, send)
    assert store.list()[0]["stamp"] == 100
    await client.close()


@pytest.mark.asyncio
async def test_creation_domain_boundary_and_auth_header(tmp_path):
    _, client, requests = setup(tmp_path, {})
    assert await client.create("https://example.com/changelog", ["example.com"]) == ID
    for url in [
        "http://127.0.0.1",
        "https://example.com.evil.invalid",
        "file:///etc/passwd",
        "https://name:password@example.com",
    ]:
        with pytest.raises(ValueError):
            await client.create(url, ["example.com"])
    assert len(requests) == 1
    await client.close()


def test_stop_is_scoped_to_owner_and_session(tmp_path):
    store = WatchStore(tmp_path / "db")
    store.add(ID, "group", "alice", "https://example.com", "condition")
    assert not store.stop(ID, "other", "alice")
    assert not store.stop(ID, "group", "bob")
    assert store.list("group", "bob") == []
    assert store.stop(ID, "group", "alice")
    assert store.list() == []


@pytest.mark.asyncio
async def test_redirect_cannot_leak_service_key():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.invalid/"})

    client = ChangeClient(
        "https://monitor.example.com", "secret", httpx.MockTransport(handler)
    )
    with pytest.raises(ValueError, match="302"):
        await client.request("GET", "watch")
    assert len(requests) == 1
    await client.close()
