"""Single-process deployment runtime — Phase 2 slice 4 (offline part).

This is the assembly point the earlier slices pointed at: ONE process
that wires the whole Python side of the seam together —

    MessageGovernor (doc 05 safety invariants, body-side)
      + CommandRouter over the NDJSON IPC transport (to Heimdall.Core)
      + EventDispatcher, optionally forwarding to a WYRD world sink
      + HeimdallWsServer (§7.1 WebSocket API, HMAC-authenticated)

Each piece was built and tested on its own in slices 1 and 3; this
module only *wires* them, in exactly one place, so deployment is
``python3 -m heimdall.runtime`` under the systemd user unit in
``heimdall-api/deploy/`` — no other process, no other wiring point.

Fail-closed rules, inherited from the slices and kept here:

- **No secret, no start.** The authenticator is built from
  ``HEIMDALL_WS_SECRET`` (via :class:`HmacAuthenticator.from_env`)
  before any socket is bound; a missing secret raises
  :class:`AuthConfigError` and the runtime refuses to start. There is
  no unauthenticated mode.
- **Configured sink, broken sink, no start.** A world sink is loaded
  lazily by import path (``HEIMDALL_WYRD_SINK=module:Class`` env var,
  a ``sink_spec`` argument, or a ``sink_factory`` callable). If that
  load *fails*, the runtime fails closed with
  :class:`RuntimeConfigError` — it never silently runs sinkless and
  pretends the world model is being fed. Running genuinely sinkless
  is possible only when explicitly configured (``allow_sinkless=True``
  or ``HEIMDALL_ALLOW_SINKLESS=1``), which is a testing posture, not a
  deployment posture.

The Core event pump is explicit, not implicit: :meth:`start_event_pump`
reads normalised events from the IPC event socket and re-broadcasts
them through the WebSocket server (which is what pushes them through
the dispatcher to handlers, the world sink, and authenticated
clients). Starting it is the caller's choice because the Core leg
(slice 2) does not exist yet — there is no event socket to pump from
until it does.

Standard library only, Python ≥3.11, like the rest of the seam.
"""
from __future__ import annotations

import importlib
import os
import threading
from collections.abc import Callable
from typing import Any, Self

from .api.auth import AuthConfigError, HmacAuthenticator
from .api.command_router import CommandRouter, CommandTransport
from .api.event_dispatcher import EventDispatcher, WorldSink
from .api.ws_server import DEFAULT_HOST, DEFAULT_PORT, HeimdallWsServer
from .core.governor import MessageGovernor
from .core.ipc_client import IpcClient
from .core.models import SeamEvent

WYRD_SINK_ENV_VAR = "HEIMDALL_WYRD_SINK"
ALLOW_SINKLESS_ENV_VAR = "HEIMDALL_ALLOW_SINKLESS"

__all__ = [
    "ALLOW_SINKLESS_ENV_VAR",
    "WYRD_SINK_ENV_VAR",
    "AuthConfigError",
    "HeimdallRuntime",
    "RuntimeConfigError",
    "load_sink",
    "main",
]


class RuntimeConfigError(RuntimeError):
    """The runtime cannot be assembled as configured (fail closed)."""


def load_sink(spec: str) -> WorldSink:
    """Load a world sink from an import path ``"module:Attribute"``.

    The attribute may be:

    - a class — instantiated with no arguments,
    - a zero-argument factory callable — called,
    - an already-built instance — used as-is.

    The result must expose a callable ``push_event(event_type,
    payload)`` (the ``BifrostBridge`` surface the dispatcher speaks).
    Any failure — bad spec, unimportable module, missing attribute,
    constructor/factory error, or a result without ``push_event`` —
    raises :class:`RuntimeConfigError`. Fail closed: a configured
    sink that cannot load must stop the runtime, never degrade it to
    silent sinkless running.
    """
    module_name, sep, attr_name = spec.partition(":")
    if not sep or not module_name.strip() or not attr_name.strip():
        raise RuntimeConfigError(
            f"invalid sink spec {spec!r}: expected 'module:Class' "
            f"(e.g. 'wyrdforge.bridges.secondlife_bridge:SlWyrdBridge')"
        )
    try:
        module = importlib.import_module(module_name.strip())
        target = getattr(module, attr_name.strip())
    except Exception as exc:  # import/attribute failure: fail closed
        raise RuntimeConfigError(
            f"could not load WYRD sink {spec!r}: {exc}"
        ) from exc
    sink: Any = target
    if isinstance(target, type) or callable(target):
        # Class or factory: build it. An instance is neither used as a
        # class nor expected to be callable, so this is unambiguous
        # for the sink surface (instances expose push_event, they are
        # not factories).
        if isinstance(target, type):
            try:
                sink = target()
            except Exception as exc:
                raise RuntimeConfigError(
                    f"WYRD sink class {spec!r} could not be "
                    f"instantiated with no arguments: {exc}"
                ) from exc
        elif not hasattr(target, "push_event"):
            try:
                sink = target()
            except Exception as exc:
                raise RuntimeConfigError(
                    f"WYRD sink factory {spec!r} failed: {exc}"
                ) from exc
    if not callable(getattr(sink, "push_event", None)):
        raise RuntimeConfigError(
            f"WYRD sink {spec!r} does not expose a callable "
            f"push_event(event_type, payload); refusing to run with a "
            f"sink that cannot receive events"
        )
    return sink


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


class HeimdallRuntime:
    """The assembled seam: governor + router + dispatcher + WS server.

    Construct via :meth:`build` for the fail-closed assembly rules, or
    directly when the pieces are already built (tests do both).
    """

    def __init__(
        self,
        *,
        governor: MessageGovernor,
        dispatcher: EventDispatcher,
        router: CommandRouter,
        ws_server: HeimdallWsServer,
        transport: CommandTransport,
        sink: WorldSink | None = None,
    ) -> None:
        self.governor = governor
        self.dispatcher = dispatcher
        self.router = router
        self.ws_server = ws_server
        # ``server`` reads better at deployment call sites.
        self.server = ws_server
        self.transport = transport
        self.sink = sink
        self._pump_thread: threading.Thread | None = None
        self._pump_stop = threading.Event()
        self.event_pump_error: Exception | None = None
        self._started = False

    # -- assembly ---------------------------------------------------
    @classmethod
    def build(
        cls,
        *,
        transport: CommandTransport | None = None,
        command_socket: str | None = None,
        event_socket: str | None = None,
        sink: WorldSink | None = None,
        sink_spec: str | None = None,
        sink_factory: Callable[[], WorldSink] | None = None,
        allow_sinkless: bool = False,
        governor: MessageGovernor | None = None,
        authenticator: HmacAuthenticator | None = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        environ: dict[str, str] | None = None,
    ) -> HeimdallRuntime:
        """Assemble the runtime, failing closed on every bad config.

        Order matters: the authenticator is built FIRST, so a missing
        ``HEIMDALL_WS_SECRET`` refuses the build before any other
        wiring (and long before any socket is bound in ``start()``).
        Sink resolution order: explicit ``sink`` instance >
        ``sink_factory`` > ``sink_spec`` argument > ``HEIMDALL_WYRD_SINK``
        env var > explicit sinkless opt-in > refuse.
        """
        env = os.environ if environ is None else environ

        # 1. Auth first (slice-3 behaviour): no secret, no runtime.
        if authenticator is None:
            secret = env.get("HEIMDALL_WS_SECRET", "")
            if not secret:
                raise AuthConfigError(
                    "HEIMDALL_WS_SECRET is not set: the WebSocket API "
                    "refuses to run unauthenticated"
                )
            authenticator = HmacAuthenticator(secret)

        # 2. Resolve the world sink; a configured-but-broken sink is
        #    a hard failure, never a silent downgrade.
        resolved_sink: WorldSink | None = sink
        if resolved_sink is None and sink_factory is not None:
            try:
                resolved_sink = sink_factory()
            except Exception as exc:
                raise RuntimeConfigError(
                    f"WYRD sink factory failed: {exc}"
                ) from exc
            if not callable(getattr(resolved_sink, "push_event", None)):
                raise RuntimeConfigError(
                    "WYRD sink factory returned an object without a "
                    "callable push_event(event_type, payload)"
                )
        if resolved_sink is None:
            spec = sink_spec or env.get(WYRD_SINK_ENV_VAR, "").strip()
            if spec:
                resolved_sink = load_sink(spec)
        if resolved_sink is None and not (
            allow_sinkless or _env_flag_from(env, ALLOW_SINKLESS_ENV_VAR)
        ):
            raise RuntimeConfigError(
                "no WYRD world sink configured and sinkless running "
                "was not explicitly allowed: set "
                f"{WYRD_SINK_ENV_VAR}=module:Class, pass a sink / "
                "sink_factory, or explicitly allow sinkless running "
                f"({ALLOW_SINKLESS_ENV_VAR}=1, for testing only)"
            )

        # 3. Transport, governor, dispatcher, router, server.
        if transport is None:
            kwargs: dict[str, Any] = {}
            if command_socket is not None:
                kwargs["command_socket"] = command_socket
            if event_socket is not None:
                kwargs["event_socket"] = event_socket
            transport = IpcClient(**kwargs)
        governor = governor if governor is not None else MessageGovernor()
        dispatcher = EventDispatcher(sink=resolved_sink)
        router = CommandRouter(transport, governor)
        ws_server = HeimdallWsServer(
            router, dispatcher, authenticator, host=host, port=port
        )
        return cls(
            governor=governor,
            dispatcher=dispatcher,
            router=router,
            ws_server=ws_server,
            transport=transport,
            sink=resolved_sink,
        )

    # -- lifecycle --------------------------------------------------
    def start(self) -> None:
        """Start the WebSocket server (the runtime's listening face)."""
        self.ws_server.start()
        self._started = True

    def stop(self) -> None:
        """Stop the event pump (if running) and the WebSocket server."""
        self._pump_stop.set()
        self.ws_server.stop()
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- events -----------------------------------------------------
    def dispatch_core_event(self, wire: dict[str, Any]) -> SeamEvent | None:
        """Push one Core wire event through dispatcher → sink/clients.

        Delegates to the WS server's broadcast path, so subscribers,
        the world sink, dead-lettering and authenticated-client
        broadcast all behave exactly as in slice 3.
        """
        return self.ws_server.broadcast_core_event(wire)

    def start_event_pump(self) -> threading.Thread:
        """Pump IPC-transport events into the dispatcher in a thread.

        The transport must expose ``events()`` yielding
        :class:`SeamEvent` (the real :class:`IpcClient` does). Each
        event is re-broadcast in wire form through
        :meth:`dispatch_core_event`. A transport failure is recorded
        on :attr:`event_pump_error` and ends the pump; it never takes
        the WebSocket server down with it.
        """
        events_fn = getattr(self.transport, "events", None)
        if not callable(events_fn):
            raise RuntimeConfigError(
                "transport does not expose events(); cannot pump "
                "Core events from it"
            )
        self._pump_stop.clear()

        def _pump() -> None:
            try:
                for event in events_fn():
                    if self._pump_stop.is_set():
                        break
                    self.dispatch_core_event(
                        {"event": event.raw_name, **event.payload}
                    )
            except Exception as exc:  # noqa: BLE001 — isolate: the pump dies alone, recorded on event_pump_error; the WS server must not fall with it
                self.event_pump_error = exc

        self._pump_thread = threading.Thread(target=_pump, daemon=True)
        self._pump_thread.start()
        return self._pump_thread


def _env_flag_from(env: dict[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def main() -> None:  # pragma: no cover - deployment entry point
    """Deployment entry point: ``python3 -m heimdall.runtime``.

    Configuration is entirely out-of-band environment (systemd
    ``EnvironmentFile`` in deployment): ``HEIMDALL_WS_SECRET``
    (required), ``HEIMDALL_WYRD_SINK=module:Class`` (required unless
    ``HEIMDALL_ALLOW_SINKLESS=1`` is set explicitly for testing).
    """
    runtime = HeimdallRuntime.build()
    runtime.start()
    print(
        f"Heimdall Bifröst runtime listening on "
        f"ws://{runtime.ws_server.host}:{runtime.ws_server.port} "
        f"(sink: {'configured' if runtime.sink is not None else 'SINKLESS — explicitly allowed'})"
    )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        runtime.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
