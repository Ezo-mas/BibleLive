"""Display sends start in the same order as their state changes."""

import asyncio
import json

import pytest

from core.server.app import _handle_display_reference
from core.server.models import BibleReference


class SlowDisplay:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.sent = []
        self.count = 0

    async def accept(self):
        pass

    async def send_text(self, data):
        self.count += 1
        if self.count == 2:
            self.started.set()
        await self.release.wait()
        self.sent.append(json.loads(data))


@pytest.mark.parametrize("approval_first", [True, False])
@pytest.mark.parametrize("manual", [True, False])
def test_approval_and_clear_leave_display_consistent(app_state, approval_first, manual):
    async def scenario():
        socket = SlowDisplay()
        await app_state.connection_manager.connect(socket, "display")
        ref = BibleReference(book="Genesis", chapter=1, verse_start=1, confidence="high")
        app_state.pending_references.append(ref)
        approval = (
            _handle_display_reference(
                socket, app_state, {"book": "Genesis", "chapter": 1, "verse_start": 1}
            )
            if manual
            else app_state.approve_and_notify(ref.id)
        )
        clear = app_state.clear_and_notify()
        actions = (approval, clear) if approval_first else (clear, approval)
        tasks = asyncio.gather(*actions)
        await asyncio.wait_for(socket.started.wait(), timeout=1)
        socket.release.set()
        await tasks
        assert [message["type"] for message in socket.sent] == (
            ["reference", "clear"] if approval_first else ["clear", "reference"]
        )
        last = socket.sent[-1]
        if app_state.current_display is None:
            assert last["type"] == "clear"
        else:
            assert last == {"type": "reference", "data": app_state.current_display.model_dump()}

    asyncio.run(scenario())
