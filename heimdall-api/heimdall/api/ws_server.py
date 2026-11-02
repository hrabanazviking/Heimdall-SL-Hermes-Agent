"""WebSocket API server — §7.1, in front of the router/dispatcher.

This is the outermost face of the seam: the mind side connects here
(``ws://localhost:8765`` by default, loopback bind), authenticates
with the HMAC first message (see :mod:`heimdall.api.auth`), sends
§7.1 commands, and receives §7.1 responses plus unsolicited §7.1
events broadcast from the Core side.

Standard library only, like the rest of the seam — Python's stdlib
has no WebSocket server, so this module implements the subset of
RFC 6455 the API needs: the opening handshake, text/binary frames
(client-masked, as the RFC requires of clients), fragmentation,
ping→pong, and close. The frame codec is transport-thin and separate
from the session logic so it can be tested over a bare socketpair.

Threading matches the rest of the seam: one accept thread, one
thread per client, a lock around the authenticated-client registry.
The governor-gated :class:`CommandRouter` and the
:class:`EventDispatcher` from slice 1 do all command/event work;
this module only moves frames and enforces that *no* command or
event crosses before authentication.
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
from typing import Any

from ..core.models import SeamEvent
from .auth import HmacAuthenticator
from .command_router import CommandRouter
from .event_dispatcher import EventDispatcher

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

CLOSE_POLICY_VIOLATION = 1008


class WsError(RuntimeError):
    """A WebSocket protocol failure on one connection."""


class ConnectionClosed(WsError):
    """The peer closed the connection (or it simply went away)."""


def compute_accept_key(sec_websocket_key: str) -> str:
    """RFC 6455 §1.3: the Sec-WebSocket-Accept value for a key."""
    digest = hashlib.sha1((sec_websocket_key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_frame(payload: bytes, opcode: int = OP_TEXT) -> bytes:
    """Serialise one unmasked (server→client) frame, FIN set."""
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length <= 0xFFFF:
        header.append(126)
        header += struct.pack("!H", length)
    else:
        header.append(127)
        header += struct.pack("!Q", length)
    return bytes(header) + payload


class _SocketBuffer:
    """Buffered exact reads over one socket (frames + handshake)."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buf = bytearray()

    def read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionClosed("socket closed by peer")
            self._buf += chunk
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def read_until(self, marker: bytes, limit: int = 65536) -> bytes:
        while marker not in self._buf:
            if len(self._buf) > limit:
                raise WsError("header block too large")
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionClosed("socket closed during handshake")
            self._buf += chunk
        end = self._buf.index(marker) + len(marker)
        out = bytes(self._buf[:end])
        del self._buf[:end]
        return out


class WsConnection:
    """One WebSocket connection: handshake, frames, messages."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._io = _SocketBuffer(sock)
        self._send_lock = threading.Lock()
        self.agent: str | None = None  # set once authenticated

    # -- handshake -------------------------------------------------
    def handshake(self) -> None:
        raw = self._io.read_until(b"\r\n\r\n").decode("iso-8859-1")
        lines = raw.split("\r\n")
        if not lines or not lines[0].startswith("GET "):
            self._reject_handshake()
            raise WsError("not a WebSocket upgrade request")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        if headers.get("upgrade", "").lower() != "websocket":
            self._reject_handshake()
            raise WsError("missing Upgrade: websocket")
        key = headers.get("sec-websocket-key", "")
        if not key:
            self._reject_handshake()
            raise WsError("missing Sec-WebSocket-Key")
        accept = compute_accept_key(key)
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        self._sock.sendall(response.encode("ascii"))

    def _reject_handshake(self) -> None:
        try:
            self._sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        except OSError:
            pass

    # -- frames ----------------------------------------------------
    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._io.read_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", self._io.read_exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._io.read_exact(8))
        mask_key = self._io.read_exact(4) if masked else b""
        payload = self._io.read_exact(length) if length else b""
        if masked:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload

    def recv_message(self) -> str:
        """Return the next complete text/binary message as text.

        Ping frames are answered with pong inline; a close frame is
        echoed and reported as :class:`ConnectionClosed`. Fragmented
        messages are reassembled.
        """
        assembled = bytearray()
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == OP_CLOSE:
                self._send_frame(payload, OP_CLOSE)
                raise ConnectionClosed("close frame received")
            if opcode == OP_PING:
                self._send_frame(payload, OP_PONG)
                continue
            if opcode == OP_PONG:
                continue
            if opcode in (OP_TEXT, OP_BINARY, 0x0):
                assembled += payload
                if fin:
                    return assembled.decode("utf-8", errors="replace")
                continue
            raise WsError(f"unsupported opcode: {opcode}")

    def _send_frame(self, payload: bytes, opcode: int) -> None:
        with self._send_lock:
            try:
                self._sock.sendall(encode_frame(payload, opcode))
            except OSError as exc:
                raise ConnectionClosed(f"send failed: {exc}") from exc

    def send_text(self, text: str) -> None:
        self._send_frame(text.encode("utf-8"), OP_TEXT)

    def send_json(self, value: dict[str, Any]) -> None:
        self.send_text(json.dumps(value, separators=(",", ":")))

    def close(self, code: int = 1000) -> None:
        try:
            self._send_frame(struct.pack("!H", code), OP_CLOSE)
        except WsError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


class HeimdallWsServer:
    """§7.1 WebSocket front door over a router + dispatcher pair."""

    def __init__(
        self,
        router: CommandRouter,
        dispatcher: EventDispatcher,
        authenticator: HmacAuthenticator,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        self._router = router
        self._dispatcher = dispatcher
        self._auth = authenticator
        self.host = host
        self.port = port
        self._listener: socket.socket | None = None
        self._clients: set[WsConnection] = set()
        self._clients_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopping = False

    # -- lifecycle -------------------------------------------------
    def start(self) -> None:
        """Bind, listen, and serve in a background thread."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(16)
        listener.settimeout(0.25)
        self._listener = listener
        self.port = listener.getsockname()[1]  # real port when 0 given
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            client.close()

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stopping:
            try:
                sock, _addr = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._serve_client, args=(sock,), daemon=True
            ).start()

    # -- clients ---------------------------------------------------
    @property
    def authenticated_client_count(self) -> int:
        with self._clients_lock:
            return len(self._clients)

    def _register(self, conn: WsConnection) -> None:
        with self._clients_lock:
            self._clients.add(conn)

    def _unregister(self, conn: WsConnection) -> None:
        with self._clients_lock:
            self._clients.discard(conn)

    def _serve_client(self, sock: socket.socket) -> None:
        conn = WsConnection(sock)
        try:
            conn.handshake()
            self._authenticate(conn)
            self._command_loop(conn)
        except WsError:
            pass  # a failed/gone client is a closed door, not a crash
        except OSError:
            pass
        finally:
            self._unregister(conn)
            conn.close()

    def _authenticate(self, conn: WsConnection) -> None:
        raw = conn.recv_message()
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            message = None
        agent = self._auth.verify_message(message)
        if agent is None:
            try:
                conn.send_json(
                    {"type": "auth", "status": "error",
                     "error": "authentication failed"}
                )
            finally:
                conn.close(CLOSE_POLICY_VIOLATION)
            raise WsError("authentication failed")
        conn.agent = agent
        # Register BEFORE the ok reply goes out: a client that acts
        # on the ok (e.g. immediately expects broadcasts) must never
        # race ahead of its own registration.
        self._register(conn)
        conn.send_json({"type": "auth", "status": "ok", "agent": agent})

    def _command_loop(self, conn: WsConnection) -> None:
        while True:
            raw = conn.recv_message()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                conn.send_json({"status": "error", "error": "invalid_json"})
                continue
            if not isinstance(message, dict) or "cmd" not in message:
                conn.send_json(
                    {"status": "error", "error": "unknown_message",
                     "detail": "expected a §7.1 command with a 'cmd' field"}
                )
                continue
            try:
                reply = self._router.route_wire(message)
            except Exception as exc:  # noqa: BLE001 — isolate: one bad command must not kill the connection (transport down, core refused, …)
                reply = {
                    "id": message.get("id", ""),
                    "status": "error",
                    "cmd": message.get("cmd", ""),
                    "error": f"transport error: {exc}",
                }
            conn.send_json(reply)

    # -- wiring ------------------------------------------------------
    @classmethod
    def build_default(
        cls,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> HeimdallWsServer:
        """Wire the server to the real Core IPC transport.

        The authenticator is built from the environment first, so a
        missing ``HEIMDALL_WS_SECRET`` fails before any socket is
        bound. Deployment wiring on purplelongship (slice 4) supplies
        the secret and the SlWyrdBridge-sink dispatcher.
        """
        from ..core.governor import MessageGovernor
        from ..core.ipc_client import IpcClient

        authenticator = HmacAuthenticator.from_env()
        transport = IpcClient()
        router = CommandRouter(transport, MessageGovernor())
        return cls(router, EventDispatcher(), authenticator, host=host, port=port)

    # -- events out --------------------------------------------------
    def broadcast_core_event(self, wire: dict[str, Any]) -> SeamEvent | None:
        """Push one Core-side event through the dispatcher to clients.

        The dispatcher normalises, handles local subscribers and the
        world sink, and dead-letters unknowns — only a normalised
        event is broadcast, in §7.1 unsolicited form
        (``{"event": <canonical>, ...payload}``), and only to
        authenticated clients. Returns the event, or None if it
        dead-lettered.
        """
        event = self._dispatcher.dispatch_wire(wire)
        if event is None:
            return None
        out = {"event": event.name, **event.payload}
        text = json.dumps(out, separators=(",", ":"))
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.send_text(text)
            except WsError:
                self._unregister(client)
                client.close()
        return event


def main() -> None:  # pragma: no cover - deployment entry point
    server = HeimdallWsServer.build_default()
    server.start()
    print(f"Heimdall Bifröst API listening on ws://{server.host}:{server.port}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
