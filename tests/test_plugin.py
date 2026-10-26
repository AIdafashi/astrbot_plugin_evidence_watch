import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_evidence_watch.main import EvidenceWatchPlugin, StarTools


@pytest.mark.asyncio
async def test_session_access_and_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    context = SimpleNamespace(send_message=AsyncMock(return_value=True))
    plugin = EvidenceWatchPlugin(context, {"allowed_sessions": ["group"]})
    plugin.client = SimpleNamespace(
        create=AsyncMock(return_value="11111111-1111-4111-8111-111111111111"),
        close=AsyncMock(),
    )
    event = SimpleNamespace(unified_msg_origin="other", get_sender_id=lambda: "alice")
    with pytest.raises(ValueError):
        await plugin.create(event, "https://example.com", "condition")
    plugin.client.create.assert_not_awaited()
    event.unified_msg_origin = "group"
    await plugin.create(event, "https://example.com", "condition")
    assert len(plugin.store.list("group", "alice")) == 1
    await plugin.send("group", "actual message chain")
    assert (
        context.send_message.call_args.args[1].get_plain_text()
        == "actual message chain"
    )
    plugin.task = asyncio.create_task(asyncio.sleep(60))
    await plugin.terminate()
    assert plugin.task.cancelled()
    plugin.client.close.assert_awaited_once()
