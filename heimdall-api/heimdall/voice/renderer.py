"""Catalog TTS renderer — the house voice law (doc 05 §4.3).

Volmarr's standing ruling (2026-10-21): her voice renders straight
from the voice catalog and is used *as it comes* — no cloning, no
voice conversion, no EQ / mastering chain. The exact render command
is::

    /opt/hatch/bin/tts speak --voice avocado_v2:<id> --speed 100 \
        --output <file> --text-stdin

with the sentence text fed on stdin.

Fail closed, like the WebSocket secret rule in earlier slices: the
voice id comes only from configuration — the explicit ``voice_id``
argument or the ``HEIMDALL_VOICE_ID`` environment variable. There is
deliberately **no default and no hardcoded id** in this module (the
voice stable lists other characters' voices; no Yrsa id is invented
here). With no voice id configured, rendering refuses with
:class:`VoiceConfigError` and the runner is never invoked.

The process runner is injectable: tests pass a fake runner and the
real ``/opt/hatch/bin/tts`` is never invoked from a test.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

TTS_BINARY = "/opt/hatch/bin/tts"
VOICE_ID_ENV = "HEIMDALL_VOICE_ID"


class VoiceConfigError(RuntimeError):
    """Raised when TTS is asked to render without configuration."""


def _subprocess_runner(argv: list[str], text: str) -> None:
    subprocess.run(argv, input=text, text=True, check=True)


class CatalogTtsRenderer:
    """Sentence renderer over the voice catalog; see module docs."""

    def __init__(
        self,
        voice_id: str | None = None,
        *,
        runner: Callable[[list[str], str], Any] | None = None,
        output_dir: str | Path | None = None,
        output_factory: Callable[[], str] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._voice_id = voice_id
        self._runner = runner or _subprocess_runner
        self._output_dir = Path(output_dir) if output_dir else None
        self._output_factory = output_factory
        self._environ = environ
        self._counter = 0

    # -- configuration ----------------------------------------------------
    def configured_voice_id(self) -> str | None:
        if self._voice_id:
            return self._voice_id
        env = self._environ if self._environ is not None else os.environ
        value = env.get(VOICE_ID_ENV, "")
        return value or None

    @property
    def is_configured(self) -> bool:
        return self.configured_voice_id() is not None

    # -- rendering ----------------------------------------------------------
    def build_argv(self, output_file: str) -> list[str]:
        voice_id = self.configured_voice_id()
        if not voice_id:
            raise VoiceConfigError(
                "no voice id configured: pass voice_id or set "
                f"{VOICE_ID_ENV}; refusing to render (fail closed, "
                "no default voice is invented)"
            )
        return [
            TTS_BINARY,
            "speak",
            "--voice",
            f"avocado_v2:{voice_id}",
            "--speed",
            "100",
            "--output",
            str(output_file),
            "--text-stdin",
        ]

    def _next_output(self) -> str:
        if self._output_factory is not None:
            return str(self._output_factory())
        self._counter += 1
        name = f"heimdall-voice-{self._counter:04d}.wav"
        if self._output_dir is not None:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            return str(self._output_dir / name)
        return str(Path(tempfile.gettempdir()) / name)

    def render(self, text: str) -> str:
        """Render one sentence; return the output file path."""
        if not self.is_configured:
            # Fail closed before touching the filesystem or runner.
            raise VoiceConfigError(
                "no voice id configured: pass voice_id or set "
                f"{VOICE_ID_ENV}; refusing to render (fail closed, "
                "no default voice is invented)"
            )
        output = self._next_output()
        argv = self.build_argv(output)
        self._runner(argv, text)
        return output

    render_sentence = render
