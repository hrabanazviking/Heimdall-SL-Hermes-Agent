"""Aurelia dual feed v1 — HR-006 (responsiveness Part 0).

Volmarr's ruling (2026-10-31): every interaction that reaches her
in Second Life is also fed to Aurelia, her sensations/embodiment
server, for Aurelia's own processing — a dual feed alongside the
mind feed, never instead of it.

The wire contract is Aurelia's own ``POST /event`` (Mimir :8001,
verified live 2026-10-31 and read at her server source):

    POST {base_url}/event
    {"type": <str, required>, "source": <str>, "payload": {<object>}}

Aurelia's request model defaults ``source`` to ``"api"`` when the
caller sends none. This plug always sends a source, and its value
is :data:`AURELIA_SOURCE` — ``"heimdall"`` — so events arriving on
her bus are attributable to this feed by name, not lumped in with
anonymous API posts. ``type`` maps the chat channel to Aurelia's
documented ``sl.*`` vocabulary (:data:`CHANNEL_EVENT_TYPES`):

- ``im``         -> ``sl.im``
- ``local_chat`` -> ``sl.chat``
- ``group_chat`` -> ``sl.group_im``

The payload carries, at minimum: ``channel``, the speaker's
identity (``from_name`` / ``from_uuid``), ``text``, the HR-005
``classification`` stamp, ``received_at`` / ``timestamp``, and
``group_uuid`` where the interaction is a group line that carried
one.

Configurability: the base URL is a constructor parameter, falling
back to the :data:`AURELIA_URL_ENV_VAR` environment variable
(``HEIMDALL_AURELIA_URL``), falling back to
:data:`DEFAULT_AURELIA_URL` (``http://127.0.0.1:8001`` — Aurelia
on the same host, Mimir, which is where the watcher deploys).

FAILURE ISOLATION IS THE LAW of this plug: Aurelia is a second
consumer, never a gate. :meth:`AureliaFeed.feed` **never raises** —
a refused connection, a timeout, a non-2xx answer, even a broken
injected transport is logged (``logging``, this module's logger),
counted in ``failed``, and swallowed, so a sick or absent Aurelia
can never block classification, the inbox, the debt surface, or a
reply. The watcher adds a second layer of the same isolation
around any plug, so even a plug that violates this contract
cannot raise into the drain (see
:mod:`heimdall.chat.watcher`).

Transport is stdlib only (``urllib``) with a short timeout
(:data:`DEFAULT_TIMEOUT_S`), and the poster is injectable so tests
run against a recording fake — or a real loopback HTTP server —
with no Aurelia and no network beyond loopback.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from .channels import GROUP_IM, IM, LOCAL_CHAT, Interaction

logger = logging.getLogger(__name__)

#: Where Aurelia lives when nothing configures otherwise: on the
#: same host as the watcher deployment (Mimir), port 8001.
DEFAULT_AURELIA_URL = "http://127.0.0.1:8001"

#: Environment variable that overrides the base URL when the
#: constructor is not given one explicitly.
AURELIA_URL_ENV_VAR = "HEIMDALL_AURELIA_URL"

#: The ``source`` value this feed stamps on every Aurelia event.
#: Aurelia's ``/event`` model defaults source to ``"api"``; this
#: feed identifies itself by name instead (see module docstring).
AURELIA_SOURCE = "heimdall"

#: Short on purpose: Aurelia is a side feed on the local network;
#: a slow Aurelia must never hold a drain open.
DEFAULT_TIMEOUT_S = 2.0

#: Chat channel -> Aurelia ``sl.*`` event type (HR-006).
CHANNEL_EVENT_TYPES: dict[str, str] = {
    IM: "sl.im",
    LOCAL_CHAT: "sl.chat",
    GROUP_IM: "sl.group_im",
}

# An injectable transport: (url, event-dict, timeout-s) -> HTTP
# status code. Raising is permitted — feed() isolates it.
Poster = Callable[[str, dict[str, Any], float], int]


def _urllib_poster(url: str, event: dict[str, Any], timeout_s: float) -> int:
    """Default transport: one stdlib ``urllib`` POST of JSON."""
    body = json.dumps(event).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            response.read()
            return int(response.status)
    except urllib.error.HTTPError as exc:
        # A non-2xx answer is a *result*, not an exception, for the
        # caller: hand the status back and let feed() log + count
        # it under the same law as a 500 from a fake.
        return int(exc.code)


def build_aurelia_event(interaction: Interaction) -> dict[str, Any] | None:
    """The ``/event`` body for one interaction, or None if unmapped.

    None means the interaction's channel has no ``sl.*`` type —
    counted and logged by the caller, never guessed into a type.
    """
    event_type = CHANNEL_EVENT_TYPES.get(interaction.channel)
    if event_type is None:
        return None
    payload: dict[str, Any] = {
        "channel": interaction.channel,
        "from_name": interaction.from_name,
        "from_uuid": interaction.from_uuid,
        "text": interaction.msg,
        "classification": interaction.classification,
        "received_at": interaction.received_at,
        "timestamp": interaction.timestamp,
    }
    if interaction.group_uuid is not None:
        payload["group_uuid"] = interaction.group_uuid
    return {"type": event_type, "source": AURELIA_SOURCE, "payload": payload}


class AureliaFeed:
    """The Aurelia plug: feed it classified interactions, safely.

    Wire it into the watcher as
    ``ChatWatcher(..., aurelia_feed=AureliaFeed())`` (or any object
    with a ``feed(interaction)`` method, or any callable). With no
    plug — or with ``enabled=False`` — the watcher behaves exactly
    as it did before HR-006.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        poster: Poster | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        enabled: bool = True,
    ) -> None:
        if base_url is None:
            base_url = os.environ.get(AURELIA_URL_ENV_VAR) or DEFAULT_AURELIA_URL
        # Normalised once so the event URL is a plain join.
        self.base_url = str(base_url).rstrip("/")
        self.timeout_s = float(timeout_s)
        self.enabled = bool(enabled)
        self._poster: Poster = poster or _urllib_poster
        # Observability counters: delivery truth, not attestation —
        # ``posted`` counts only 2xx answers from Aurelia's side.
        self.posted = 0
        self.failed = 0

    @property
    def event_url(self) -> str:
        return f"{self.base_url}/event"

    def feed(self, interaction: Interaction) -> bool:
        """POST one classified interaction to Aurelia; never raises.

        True only when Aurelia answered 2xx. Every failure —
        disabled plug excluded, which is simply a no-op False —
        is logged and counted in ``failed`` and returns False.
        """
        if not self.enabled:
            return False
        event = build_aurelia_event(interaction)
        if event is None:
            self.failed += 1
            logger.warning(
                "Aurelia feed: no sl.* event type for channel %r; "
                "interaction not posted",
                interaction.channel,
            )
            return False
        try:
            status = int(self._poster(self.event_url, event, self.timeout_s))
        except Exception as exc:  # noqa: BLE001 — isolation law: a dead Aurelia must never raise into the watcher
            self.failed += 1
            logger.warning(
                "Aurelia feed: POST %s (%s) failed: %s",
                self.event_url,
                event["type"],
                exc,
            )
            return False
        if not 200 <= status < 300:
            self.failed += 1
            logger.warning(
                "Aurelia feed: POST %s (%s) answered HTTP %s",
                self.event_url,
                event["type"],
                status,
            )
            return False
        self.posted += 1
        return True

    # The watcher accepts a bare callable as a plug too.
    __call__ = feed
