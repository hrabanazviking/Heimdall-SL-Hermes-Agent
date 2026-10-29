"""Voice sidecar — out-of-world voice loop (Phase 4 slice 1).

Interim code name "voice sidecar" (the Mímir name collision is
Volmarr's open decision; this module is never named Mímir).

Contents, per design doc 05 §4:

- :mod:`heimdall.voice.chunker` — incremental sentence chunker for
  sentence-streamed TTS (§4.3).
- :mod:`heimdall.voice.ledger` — utterance ledger with
  played-prefix accounting: only played audio counts as said (§4.4).
- :mod:`heimdall.voice.turns` — VAD + stable-partial turn detector:
  speculative ear, committed mouth (§4.3).
- :mod:`heimdall.voice.barge` — backchannel classifier and barge-in
  controller: backchannels never cancel, true speech cancels fast
  (§4.4).
- :mod:`heimdall.voice.renderer` — catalog TTS renderer under the
  house voice law: straight from the catalog, voice id from config
  only, fail closed when unconfigured (§4.3).
- :mod:`heimdall.voice.pipeline` — the orchestrator: confirmed
  turns to the mind (``channel="voice"``), and speech only from the
  mind — reflex tiers never speak, the kill switch blocks speech.

In-world WebRTC voice (§4.5) is a later gated spike and is not in
this package.
"""

from .barge import (
    BACKCHANNEL,
    BARGE_IN,
    BackchannelClassifier,
    BargeInController,
)
from .chunker import SentenceChunker, chunk_text
from .ledger import (
    SentenceState,
    Utterance,
    UtteranceLedger,
)
from .pipeline import SpeakResult, VoicePipeline
from .renderer import (
    TTS_BINARY,
    VOICE_ID_ENV,
    CatalogTtsRenderer,
    VoiceConfigError,
)
from .turns import SpeculativePartial, Turn, TurnDetector

__all__ = [
    "BACKCHANNEL",
    "BARGE_IN",
    "TTS_BINARY",
    "VOICE_ID_ENV",
    "BackchannelClassifier",
    "BargeInController",
    "CatalogTtsRenderer",
    "SentenceChunker",
    "SentenceState",
    "SpeakResult",
    "SpeculativePartial",
    "Turn",
    "TurnDetector",
    "Utterance",
    "UtteranceLedger",
    "VoiceConfigError",
    "VoicePipeline",
    "chunk_text",
]
