"""§7.1 WebSocket server: HMAC auth gate, command routing, broadcast.

Proven with a raw-socket test client speaking real RFC 6455 frames —
no WebSocket library anywhere in the seam or its tests — and a stub
command transport standing in for Heimdall.Core, so the whole front
door is exercised with no grid, no viewer and no login.
"""
import base64
import json
import os
import socket
import struct

import pytest

from heimdall.api.auth import AuthConfigError, HmacAuthenticator
from heimdall.api.command_router import CommandRouter
from heimdall.api.event_dispatcher import EventDispatcher
from heimdall.api.ws_server import (
    HeimdallWsServer,
    compute_accept_key,
    encode_frame,
)
from heimdall.core.governor import MessageGovernor

SECRET = "test-secret-not-a-real-one"
AGENT = "hermes"


# -- pure codec / auth units ------------------------------------------

def test_accept_key_matches_rfc6455_vector():
    # RFC 6455 §1.3 worked example.
    assert (
        compute_accept_key("dGhlIHNhbXBsZSBub25jZQ==")
        == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    )


def test_encode_frame_lengths():
    small = encode_frame(b"hi")
    assert small[0] == 0x81 and small[1] == 2 and small[2:] == b"hi"
    medium = encode_frame(b"x" * 200)
    assert medium[1] == 126 and struct.unpack("!H", medium[2:4])[0] == 200


def test_hmac_token_matches_rfc4231_vector():
    # RFC 4231 test case 1: key 0x0b*20, data "Hi There".
    auth = HmacAuthenticator(b"\x0b" * 20)
    assert auth.token_for("Hi There") == (
        "b0344c61d8db38535ca8afceaf0bf12b"
        "881dc200c9833da726e9376c2e32cff7"
    )


def test_authenticator_fails_closed():
    auth = HmacAuthenticator(SECRET)
    good = {"type": "auth", "agent": AGENT, "token": auth.token_for(AGENT)}
    assert auth.verify_message(good) == AGENT
    assert auth.verify_message({**good, "token": "0" * 64}) is None
    assert auth.verify_message({**good, "type": "cmd"}) is None
    assert auth.verify_message({"type": "auth", "agent": AGENT}) is None
    assert auth.verify_message("not a dict") is None
    assert auth.verify_message(None) is None


def test_server_refuses_to_run_without_a_secret():
    with pytest.raises(AuthConfigError):
        HmacAuthenticator("")
    with pytest.raises(AuthConfigError):
        HmacAuthenticator.from_env("HEIMDALL_WS_SECRET_DEFINITELY_UNSET")


def test_build_default_fails_before_binding_without_secret(monkeypatch):
    monkeypatch.delenv("HEIMDALL_WS_SECRET", raising=False)
    with pytest.raises(AuthConfigError):
        HeimdallWsServer.build_default(port=0)


def test_build_default_wires_with_secret_from_env(monkeypatch):
    monkeypatch.setenv("HEIMDALL_WS_SECRET", SECRET)
    srv = HeimdallWsServer.build_default(port=0)
    assert srv.authenticated_client_count == 0


# -- raw-socket WebSocket client ---------------------------------------

def _client_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """One client frame: FIN set, masked (as RFC 6455 requires)."""
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length <= 0xFFFF:
        header.append(0x80 | 126)
        header += struct.pack("!H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack("!Q", length)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bytes(header) + mask + masked


class WsTestClient:
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buf = bytearray()
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                "GET / HTTP/1.1\r\nHost: localhost\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        raw = self._read_until(b"\r\n\r\n")
        head = raw.decode("iso-8859-1")
        assert "101" in head.split("\r\n")[0]
        expected = compute_accept_key(key)
        assert expected in head

    def _fill(self, n: int):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("server closed the socket")
            self.buf += chunk

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("server closed during handshake")
            self.buf += chunk
        end = self.buf.index(marker) + len(marker)
        out = bytes(self.buf[:end])
        del self.buf[:end]
        return out

    def send_text(self, text: str):
        self.sock.sendall(_client_frame(text.encode()))

    def send_json(self, value):
        self.send_text(json.dumps(value))

    def send_ping(self):
        self.sock.sendall(_client_frame(b"hb", opcode=0x9))

    def recv_frame(self):
        self._fill(2)
        first, second = self.buf[0], self.buf[1]
        del self.buf[:2]
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            self._fill(2)
            (length,) = struct.unpack("!H", bytes(self.buf[:2]))
            del self.buf[:2]
        elif length == 127:
            self._fill(8)
            (length,) = struct.unpack("!Q", bytes(self.buf[:8]))
            del self.buf[:8]
        self._fill(length)
        payload = bytes(self.buf[:length])
        del self.buf[:length]
        return opcode, payload

    def recv_json(self):
        opcode, payload = self.recv_frame()
        assert opcode == 0x1, f"expected text frame, got opcode {opcode}"
        return json.loads(payload)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# -- server fixture ------------------------------------------------------

class StubTransport:
    """Stands in for Heimdall.Core behind the router."""

    def __init__(self):
        self.sent = []

    def send_command(self, command):
        self.sent.append(command)
        return {"status": "ok", "result": {"sent": True, "channel": 0}}


@pytest.fixture()
def server():
    transport = StubTransport()
    governor = MessageGovernor()
    router = CommandRouter(transport, governor)
    dispatcher = EventDispatcher()
    auth = HmacAuthenticator(SECRET)
    srv = HeimdallWsServer(router, dispatcher, auth, port=0)
    srv.start()
    srv.transport = transport  # for assertions
    yield srv
    srv.stop()


def _authed_client(server) -> WsTestClient:
    client = WsTestClient(server.port)
    auth = HmacAuthenticator(SECRET)
    client.send_json(
        {"type": "auth", "agent": AGENT, "token": auth.token_for(AGENT)}
    )
    reply = client.recv_json()
    assert reply["status"] == "ok" and reply["agent"] == AGENT
    return client


# -- gate behaviour -------------------------------------------------------

def test_command_before_auth_is_rejected_and_never_routed(server):
    client = WsTestClient(server.port)
    client.send_json({"id": "1", "cmd": "chat", "msg": "too soon"})
    reply = client.recv_json()
    assert reply["status"] == "error"
    assert server.transport.sent == []
    client.close()


def test_bad_token_is_rejected(server):
    client = WsTestClient(server.port)
    client.send_json({"type": "auth", "agent": AGENT, "token": "f" * 64})
    reply = client.recv_json()
    assert reply["status"] == "error"
    assert server.authenticated_client_count == 0
    client.close()


def test_authed_command_routes_and_reflex_is_governor_blocked(server):
    client = _authed_client(server)
    reply = None
    client.send_json({"id": "1", "cmd": "chat", "msg": "Hello!", "channel": 0})
    reply = client.recv_json()
    assert reply["status"] == "ok" and reply["result"]["sent"] is True
    assert len(server.transport.sent) == 1

    client.send_json(
        {"id": "2", "cmd": "chat", "msg": "boo", "tier": "reflex"}
    )
    reply = client.recv_json()
    assert reply["status"] == "error"
    assert reply["blocked_reason"] == "reflex_tier_may_not_speak"
    assert len(server.transport.sent) == 1  # governor stopped it
    client.close()


def test_malformed_json_after_auth_gets_error_not_disconnect(server):
    client = _authed_client(server)
    client.send_text("{not json")
    reply = client.recv_json()
    assert reply == {"status": "error", "error": "invalid_json"}
    client.close()


def test_ping_gets_pong(server):
    client = _authed_client(server)
    client.send_ping()
    opcode, payload = client.recv_frame()
    assert opcode == 0xA and payload == b"hb"
    client.close()


# -- events out -----------------------------------------------------------

def test_broadcast_reaches_authed_clients_in_canonical_form(server):
    client = _authed_client(server)
    event = server.broadcast_core_event(
        {"event": "ChatReceived", "from": "Mara", "msg": "the bridge is closed"}
    )
    assert event is not None and event.name == "chat"
    wire = client.recv_json()
    assert wire["event"] == "chat"
    assert wire["from"] == "Mara" and wire["msg"] == "the bridge is closed"
    client.close()


def test_unknown_core_event_dead_letters_and_is_not_broadcast(server):
    client = _authed_client(server)
    assert server.broadcast_core_event({"event": "NonsenseEvent"}) is None
    assert server._dispatcher.dead_letters[-1]["event"] == "NonsenseEvent"
    client.sock.settimeout(0.3)
    with pytest.raises((socket.timeout, TimeoutError)):
        client.recv_frame()
    client.close()
