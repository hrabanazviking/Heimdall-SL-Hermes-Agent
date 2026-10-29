"""IPC transport against a fake Core, and the router above it.

The fake Core is a plain Unix-socket server speaking §7.2 NDJSON —
exactly what Heimdall.Core will speak — so the seam is proven
end-to-end with no grid, no viewer and no login.
"""
import json
import socket
import threading

import pytest

from heimdall.api.command_router import CommandRouter
from heimdall.core.governor import MessageGovernor
from heimdall.core.ipc_client import IpcClient, IpcError
from heimdall.core.models import Command


class FakeCore(threading.Thread):
    """One-shot command server + one-shot event server."""

    def __init__(self, cmd_path, evt_path, events):
        super().__init__(daemon=True)
        self.cmd_path = str(cmd_path)
        self.evt_path = str(evt_path)
        self.events = events
        self.received = []

    def run(self):
        cmd_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        cmd_srv.bind(self.cmd_path)
        cmd_srv.listen(1)
        evt_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        evt_srv.bind(self.evt_path)
        evt_srv.listen(1)

        def serve_events():
            conn, _ = evt_srv.accept()
            with conn:
                for event in self.events:
                    conn.sendall((json.dumps(event) + "\n").encode())
            evt_srv.close()

        threading.Thread(target=serve_events, daemon=True).start()
        conn, _ = cmd_srv.accept()
        with conn:
            data = b""
            while b"\n" not in data:
                data += conn.recv(65536)
            wire = json.loads(data)
            self.received.append(wire)
            reply = {"id": wire["id"], "status": "ok", "result": {"sent": True}}
            conn.sendall((json.dumps(reply) + "\n").encode())
        cmd_srv.close()


@pytest.fixture()
def fake_core(tmp_path):
    core = FakeCore(
        tmp_path / "heimdall.sock",
        tmp_path / "heimdall.sock.events",
        events=[
            {"event": "ChatReceived", "from": "Mara", "msg": "the bridge is closed"},
            {"event": "NonsenseEvent", "msg": "dropped, never guessed at"},
            {"event": "RegionChanged", "region_name": "Viking Village"},
        ],
    )
    core.start()
    import time
    from pathlib import Path

    deadline = time.time() + 5
    while time.time() < deadline:
        if Path(core.cmd_path).exists() and Path(core.evt_path).exists():
            break
        time.sleep(0.01)
    yield core


def _client(tmp_path):
    return IpcClient(
        command_socket=str(tmp_path / "heimdall.sock"),
        event_socket=str(tmp_path / "heimdall.sock.events"),
        timeout_s=5.0,
    )


def test_send_command_round_trip(fake_core, tmp_path):
    reply = _client(tmp_path).send_command(
        Command(id="1", cmd="chat", params={"msg": "Hello!", "channel": 0})
    )
    assert reply["status"] == "ok" and reply["result"]["sent"] is True
    assert fake_core.received[0]["cmd"] == "chat"
    assert fake_core.received[0]["msg"] == "Hello!"


def test_event_stream_normalises_and_drops_unknowns(fake_core, tmp_path):
    events = list(_client(tmp_path).events())
    assert [e.name for e in events] == ["chat", "region_changed"]
    assert events[0].payload["msg"] == "the bridge is closed"


def test_missing_socket_is_an_error_not_a_silence(tmp_path):
    client = IpcClient(command_socket=str(tmp_path / "nope.sock"))
    with pytest.raises(IpcError):
        client.send_command(Command(id="1", cmd="chat"))


def test_router_blocks_reflex_speech_before_transport(fake_core, tmp_path):
    router = CommandRouter(_client(tmp_path), MessageGovernor())
    reply = router.route_wire({"id": "9", "cmd": "chat", "msg": "boo", "tier": "reflex"})
    assert reply["status"] == "error"
    assert reply["blocked_reason"] == "reflex_tier_may_not_speak"
    assert fake_core.received == []  # never reached the Core


def test_router_counts_only_confirmed_sends(fake_core, tmp_path):
    governor = MessageGovernor(cap=1)
    router = CommandRouter(_client(tmp_path), governor)
    reply = router.route_wire({"id": "1", "cmd": "chat", "msg": "hi", "channel": 0})
    assert reply["status"] == "ok"
    blocked = router.route_wire({"id": "2", "cmd": "chat", "msg": "again", "channel": 0})
    assert blocked["blocked_reason"] == "daily_message_cap_reached"
