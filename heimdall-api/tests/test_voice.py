"""Voice sidecar core — Phase 4 slice 1 (doc 05 §4).

Everything is offline and fake-driven: the TTS runner is a fake
(the real /opt/hatch/bin/tts is never invoked), the player is a
fake, clocks are explicit timestamps, and no network, grid, or
audio device is touched.
"""
import pytest

from heimdall.core.governor import MessageGovernor
from heimdall.voice import (
    BackchannelClassifier,
    BargeInController,
    CatalogTtsRenderer,
    SentenceChunker,
    TurnDetector,
    UtteranceLedger,
    VoiceConfigError,
    VoicePipeline,
    chunk_text,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRenderer:
    def __init__(self, events=None):
        self.calls = []
        self.events = events

    def render(self, text):
        self.calls.append(text)
        if self.events is not None:
            self.events.append(("render", text))
        return f"audio:{text}"


class FakePlayer:
    """Player protocol fake.

    ``partials``: {sentence_index: played_chars} for a play that
    stops early without a sound event (returns that many chars).
    ``sounds``: {sentence_index: (played_chars, duration, text)} —
    during that sentence's play, a sound arrives via on_sound with
    the player already reporting ``played_chars`` as its frontier.
    """

    def __init__(self, events=None, partials=None, sounds=None):
        self.events = events
        self.partials = partials or {}
        self.sounds = sounds or {}
        self.plays = []
        self.cancel_calls = 0
        self.flush_calls = 0
        self._current = None

    def play(self, text, audio, utterance_id=None, sentence_index=None,
             on_sound=None):
        index = sentence_index if sentence_index is not None else 0
        self.plays.append((text, audio))
        if self.events is not None:
            self.events.append(("play", text))
        self._current = (index, len(text))
        if index in self.sounds:
            played, duration, transcript = self.sounds[index]
            self._current = (index, played)
            if on_sound is not None:
                on_sound(duration, transcript)
            return played
        if index in self.partials:
            played = self.partials[index]
            self._current = (index, played)
            return played
        return len(text)

    def cancel(self):
        self.cancel_calls += 1

    def flush(self):
        self.flush_calls += 1

    def current_played(self):
        return self._current


def make_pipeline(**kwargs):
    renderer = kwargs.pop("renderer", FakeRenderer())
    player = kwargs.pop("player", FakePlayer())
    pipe = VoicePipeline(renderer=renderer, player=player, **kwargs)
    return pipe, renderer, player


# ---------------------------------------------------------------------------
# Sentence chunker
# ---------------------------------------------------------------------------


def test_chunker_streams_fragments():
    chunker = SentenceChunker()
    assert chunker.feed("Hel") == []
    assert chunker.feed("lo world. How ") == ["Hello world."]
    assert chunker.feed("are you? Fine") == ["How are you?"]
    assert chunker.flush() == "Fine"
    assert chunker.flush() == ""


def test_chunker_does_not_split_decimals():
    text = "The price is 3.5 dollars. Pi is 3.14159. Done."
    assert chunk_text(text) == [
        "The price is 3.5 dollars.",
        "Pi is 3.14159.",
        "Done.",
    ]


def test_chunker_decimal_split_across_fragments():
    chunker = SentenceChunker()
    assert chunker.feed("It is 3.") == []  # may become 3.5: held
    assert chunker.feed("5 today. Done") == ["It is 3.5 today."]
    assert chunker.flush() == "Done"


def test_chunker_quotes_ellipsis_and_clusters():
    text = '"Hello." She waited... Really?! Yes!'
    assert chunk_text(text) == [
        '"Hello."',
        "She waited...",
        "Really?!",
        "Yes!",
    ]
    assert chunk_text("Wait… go (now.) Then.") == [
        "Wait…",
        "go (now.)",
        "Then.",
    ]


def test_chunker_flush_returns_remainder():
    chunker = SentenceChunker()
    assert chunker.feed("No terminator here") == []
    assert chunker.flush() == "No terminator here"


# ---------------------------------------------------------------------------
# Utterance ledger — played-prefix accounting
# ---------------------------------------------------------------------------


def test_ledger_interruption_is_exact():
    ledger = UtteranceLedger()
    text = "One. Two. Three."
    utt = ledger.start_utterance(text, chunk_text(text))
    ledger.mark_sentence_played(utt.utterance_id, 0)
    ledger.interrupt(utt.utterance_id, 1, 2)
    assert ledger.said_text(utt.utterance_id) == "One. Tw"
    assert ledger.unsaid_text(utt.utterance_id) == "o. Three."
    said = ledger.said_text(utt.utterance_id)
    unsaid = ledger.unsaid_text(utt.utterance_id)
    assert said + unsaid == text
    assert ledger.get(utt.utterance_id).interrupted


def test_ledger_completion_and_empty():
    ledger = UtteranceLedger()
    text = "Alpha. Beta."
    utt = ledger.start_utterance(text, chunk_text(text))
    assert ledger.said_text(utt.utterance_id) == ""
    assert ledger.unsaid_text(utt.utterance_id) == text
    ledger.mark_sentence_played(utt.utterance_id, 0)
    ledger.mark_sentence_played(utt.utterance_id, 1)
    ledger.complete(utt.utterance_id)
    assert ledger.said_text(utt.utterance_id) == text
    assert ledger.unsaid_text(utt.utterance_id) == ""
    assert not ledger.get(utt.utterance_id).interrupted


# ---------------------------------------------------------------------------
# Turn detector — speculative ear, committed mouth
# ---------------------------------------------------------------------------


def test_turn_never_ends_while_speech_active():
    det = TurnDetector(confirm_window=0.6)
    det.feed_speech(True, at=0.0)
    det.feed_partial("hello there", at=0.1)
    assert det.poll(at=100.0) is None


def test_turn_never_ends_while_partial_still_changing():
    det = TurnDetector(confirm_window=0.6)
    det.feed_speech(True, at=0.0)
    det.feed_partial("hel", at=0.1)
    det.feed_speech(False, at=0.2)
    det.feed_partial("hello", at=0.5)  # change restarts stability
    assert det.poll(at=0.9) is None
    turn = det.poll(at=1.2)
    assert turn is not None
    assert turn.text == "hello"
    assert turn.channel == "voice"
    assert det.poll(at=2.0) is None  # delivered exactly once


def test_turn_waits_for_speech_stop_plus_stable_window():
    det = TurnDetector(confirm_window=0.6)
    det.feed_speech(True, at=0.0)
    det.feed_partial("stable words", at=0.1)
    det.feed_speech(False, at=1.0)  # stop is later than last change
    assert det.poll(at=1.3) is None
    turn = det.poll(at=1.7)
    assert turn is not None and turn.text == "stable words"


def test_speculative_partial_is_not_a_confirmed_turn():
    det = TurnDetector(confirm_window=0.6)
    det.feed_speech(True, at=0.0)
    det.feed_partial("still forming", at=0.1)
    det.feed_speech(False, at=0.2)
    spec = det.speculative
    assert spec is not None and spec.text == "still forming"
    assert spec.speculative
    assert det.poll(at=0.5) is None  # window not yet served


def test_turn_speech_resuming_cancels_confirmation():
    det = TurnDetector(confirm_window=0.6)
    det.feed_speech(True, at=0.0)
    det.feed_partial("words", at=0.1)
    det.feed_speech(False, at=0.2)
    det.feed_speech(True, at=0.5)  # he starts again: no turn yet
    assert det.poll(at=5.0) is None
    det.feed_speech(False, at=5.1)
    assert det.poll(at=5.5) is None
    assert det.poll(at=5.8) is not None


def test_feed_final_confirms_immediately():
    det = TurnDetector(confirm_window=60.0)
    det.feed_speech(True, at=0.0)
    det.feed_partial("a final answer", at=0.1)
    det.feed_speech(False, at=0.2)
    turn = det.feed_final("a final answer", at=0.3)
    assert turn is not None and turn.text == "a final answer"


# ---------------------------------------------------------------------------
# Backchannel classifier + barge-in controller
# ---------------------------------------------------------------------------


def test_backchannel_classification():
    clf = BackchannelClassifier()
    assert clf.is_backchannel(0.3, "mm-hm")
    assert clf.is_backchannel(0.2, "")  # cough / noise, short
    assert clf.is_backchannel(0.4, "ha ha")
    assert clf.is_backchannel(0.5, "uh-huh")
    assert clf.is_barge(0.5, "stop talking")
    assert clf.is_barge(0.3, "wait")
    assert clf.is_barge(2.0, "mm-hm")  # held too long: not a nod
    assert clf.is_barge(1.5, "")  # long noise is not a backchannel


def test_controller_backchannel_never_cancels():
    ledger = UtteranceLedger()
    text = "Keep talking. More."
    utt = ledger.start_utterance(text, chunk_text(text))
    player = FakePlayer()
    player._current = (0, 4)
    ctl = BargeInController(player, ledger)
    ctl.set_current(utt.utterance_id, 0)
    assert ctl.handle_sound(0.3, "mm-hm") is False
    assert player.cancel_calls == 0 and player.flush_calls == 0
    assert not ledger.get(utt.utterance_id).interrupted


def test_controller_true_barge_cancels_and_ledgers():
    ledger = UtteranceLedger()
    text = "Keep talking. More."
    utt = ledger.start_utterance(text, chunk_text(text))
    player = FakePlayer()
    player._current = (0, 4)
    ctl = BargeInController(player, ledger)
    ctl.set_current(utt.utterance_id, 0)
    assert ctl.handle_sound(0.4, "stop, listen") is True
    assert player.cancel_calls == 1 and player.flush_calls == 1
    assert ledger.said_text(utt.utterance_id) == "Keep"
    assert ledger.unsaid_text(utt.utterance_id) == " talking. More."


# ---------------------------------------------------------------------------
# Pipeline — outbound law
# ---------------------------------------------------------------------------


def test_pipeline_reflex_origin_never_speaks():
    pipe, renderer, player = make_pipeline()
    for origin in ("reflex", "body", "stranger"):
        result = pipe.speak("Hello there.", origin=origin)
        assert not result.allowed
    assert renderer.calls == [] and player.plays == []


def test_pipeline_kill_switch_blocks_speech():
    governor = MessageGovernor()
    pipe, renderer, player = make_pipeline(governor=governor)
    governor.engage_kill_switch()
    result = pipe.speak("Hello there.")
    assert not result.allowed
    assert result.reason == "kill_switch_engaged"
    assert renderer.calls == [] and player.plays == []
    governor.release_kill_switch()
    assert pipe.speak("Hello there.").allowed


def test_pipeline_renders_sentence_by_sentence():
    events = []
    renderer = FakeRenderer(events=events)
    player = FakePlayer(events=events)
    pipe, _, _ = make_pipeline(renderer=renderer, player=player)
    text = "First one. Second two. Third three."
    result = pipe.speak(text)
    assert result.allowed and not result.interrupted
    # Interleaved render/play, one sentence at a time, in order —
    # and the first render carried ONE sentence, never the reply.
    assert events == [
        ("render", "First one."), ("play", "First one."),
        ("render", "Second two."), ("play", "Second two."),
        ("render", "Third three."), ("play", "Third three."),
    ]
    assert renderer.calls[0] == "First one."
    assert result.said_text == text and result.unsaid_text == ""


def test_pipeline_barge_in_mid_play_ledgers_played_prefix():
    player = FakePlayer(sounds={1: (3, 0.4, "stop, wait")})
    pipe, renderer, _ = make_pipeline(player=player)
    text = "First sentence. Second sentence here. Third sentence."
    result = pipe.speak(text)
    assert result.allowed and result.interrupted
    assert player.cancel_calls == 1 and player.flush_calls == 1
    # Sentence 1 fully + 3 chars of sentence 2 were heard.
    assert result.said_text == "First sentence. Sec"
    assert result.unsaid_text == "ond sentence here. Third sentence."
    assert result.said_text + result.unsaid_text == text
    # The third sentence was never rendered or played.
    assert renderer.calls == ["First sentence.", "Second sentence here."]
    assert [p[0] for p in player.plays] == renderer.calls


def test_pipeline_backchannel_does_not_interrupt():
    # The nod arrives just as the first sentence finishes playing.
    player = FakePlayer(sounds={0: (len("First words."), 0.3, "mm-hm")})
    pipe, _renderer, _ = make_pipeline(player=player)
    result = pipe.speak("First words. Second words.")
    assert result.allowed and not result.interrupted
    assert player.cancel_calls == 0
    assert result.said_text == "First words. Second words."


def test_pipeline_partial_play_without_barge_still_interrupts():
    player = FakePlayer(partials={0: 5})
    pipe, _, _ = make_pipeline(player=player)
    result = pipe.speak("A long sentence here. Unplayed.")
    assert result.interrupted
    assert result.said_text == "A lon"
    assert result.unsaid_text == "g sentence here. Unplayed."


# ---------------------------------------------------------------------------
# Pipeline — inbound law
# ---------------------------------------------------------------------------


def test_confirmed_turn_reaches_mind_with_voice_channel():
    heard = []
    detector = TurnDetector(confirm_window=0.5)
    pipe, renderer, _ = make_pipeline(
        mind_callback=heard.append, detector=detector
    )
    pipe.feed_speech(True, at=0.0)
    pipe.feed_partial("hail and well met", at=0.1)
    pipe.feed_speech(False, at=0.2)
    assert heard == []  # not confirmed yet: mind not called
    pipe.poll(at=0.8)
    assert len(heard) == 1
    assert heard[0].text == "hail and well met"
    assert heard[0].channel == "voice"
    assert renderer.calls == []  # hearing never renders


def test_unconfirmed_partial_never_triggers_tts():
    heard = []
    pipe, renderer, player = make_pipeline(mind_callback=heard.append)
    pipe.feed_speech(True, at=0.0)
    pipe.feed_partial("speculative words", at=0.1)
    assert pipe.speculative is not None
    assert pipe.speculative.text == "speculative words"
    assert heard == []
    assert renderer.calls == [] and player.plays == []


# ---------------------------------------------------------------------------
# Catalog TTS renderer — house voice law
# ---------------------------------------------------------------------------


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, text):
        self.calls.append((list(argv), text))


def test_catalog_renderer_argv_is_exact():
    runner = FakeRunner()
    renderer = CatalogTtsRenderer(
        voice_id="testvoice",
        runner=runner,
        output_factory=lambda: "/tmp/heimdall-test-out.wav",
        environ={},
    )
    out = renderer.render("Hello there.")
    assert out == "/tmp/heimdall-test-out.wav"
    assert runner.calls == [(
        [
            "/opt/hatch/bin/tts",
            "speak",
            "--voice",
            "avocado_v2:testvoice",
            "--speed",
            "100",
            "--output",
            "/tmp/heimdall-test-out.wav",
            "--text-stdin",
        ],
        "Hello there.",
    )]


def test_catalog_renderer_reads_voice_id_from_env():
    runner = FakeRunner()
    renderer = CatalogTtsRenderer(
        runner=runner,
        output_factory=lambda: "/tmp/o.wav",
        environ={"HEIMDALL_VOICE_ID": "envvoice"},
    )
    renderer.render("Hi.")
    argv, _ = runner.calls[0]
    assert argv[argv.index("--voice") + 1] == "avocado_v2:envvoice"


def test_catalog_renderer_refuses_when_unconfigured():
    runner = FakeRunner()
    renderer = CatalogTtsRenderer(runner=runner, environ={})
    assert not renderer.is_configured
    with pytest.raises(VoiceConfigError):
        renderer.render("Hi.")
    with pytest.raises(VoiceConfigError):
        renderer.build_argv("/tmp/o.wav")
    assert runner.calls == []  # fail closed: runner never invoked
