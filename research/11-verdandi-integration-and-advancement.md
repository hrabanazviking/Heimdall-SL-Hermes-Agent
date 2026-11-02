# Verðandi × WYRD — Integration Design & Verðandi Advancement Study

**Project:** Verðandi (the nervous-system layer) × WYRD Protocol (ECS world-model engine), with the Heimdall Second Life embodiment as the newest and most demanding consumer of both.
**Purpose:** Establish what Verðandi actually is today (verified from its code, tests, and live state), design its side of the seam with WYRD on principle rather than by accretion, and lay out a phased advancement roadmap for Verðandi itself.
**Status:** Study / design proposal. Nothing was built, modified, or pushed. The Verdandi tree was read-only throughout (a standing forge owns it).
**Tagging:** **[VERIFIED]** = read in code/docs or observed in live state on 2026-10-26, with the file cited. **[PROPOSED]** = design in this study. **[REPORTED]** = stated in the repo's own docs/roadmaps but not independently re-verified from this machine.

**Sources drawn on** (all local):

- Verdandi repo (`~/workspace/Verdandi/`): `README.md`, `ARCHITECTURE.md`, `INTERFACE.md`, `DOMAIN_MAP.md`, `nervous_system.py`, `conversation_logger.py`, `context_injector.py`, `reactor.py`, `heartbeat/` (core, checks, actions, signals, paths, prometheus), `verdandi/eir/`, `worlds.py`, `volmarr_world.py`, `wyrd_bridge.py`, `wyrd_inbound.py`, `wyrd_ledger_append.py`, `telegram_bridge.py`, `docs/roadmap-worlds.md`, `docs/nerve-hub-protocol.md`, `VERDANDI_AUDIT.md`, `VERDANDI_REAUDIT.md`, `DEVLOG.md`, `pyproject.toml`, `tests/` (49 files), git log.
- Live state on this VM: `~/.hermes/state/` (nerve feed, heartbeat DB, world registry) — inspected read-only.
- WYRD side: `~/workspace/wyrd-protocol/src/wyrdforge/bridges/verdandi_bridge.py` (1,674 lines), `bridges/hermes_bridge.py`, `runtime/events.py`; and the sibling study `~/workspace/heimdall-research/04-wyrd-integration-design.md` (Heimdall × WYRD), whose verified WYRD state (ECS, Oracle, Rúnakefli EventBus, stores, 1,849 tests passing) this study builds on without re-deriving.

---

## Part 1 — Verified current state

### 1.1 What Verðandi actually is: three systems wearing one name

The README pitches one thing — "a Unix domain socket event bus" (`README.md`, The Pitch) — and the code delivers that, plus two more systems that grew on top of it. Any advancement plan that only sees the bus will misjudge the repo.

**System A — the Nerve Hub (the bus).** **[VERIFIED]** `nervous_system.py` (1,118 lines): a single-file, zero-dependency Python 3.11+ asyncio server on a Unix domain socket (`~/.hermes/state/runa.sock`). Publishers send newline-delimited JSON; the hub stamps each event, appends it to an append-only feed (`nerve_feed.jsonl`), and broadcasts it to all subscribers. When the hub is down, publishers write directly to the feed with `_fallback: true` — zero data loss, no broadcast (`ARCHITECTURE.md` §5.1; `nervous_system.py::_feed_lock_write`, flock + flush + fsync). Around that core the file also carries: a 256-event in-memory ring buffer for late joiners, 10 MB feed rotation, stale-subscriber pruning with keepalive probes, graceful shutdown with drain, PID-file discipline with fail-closed hub detection, and CLI commands (`serve/publish/recent/subscribe/status/stop/healthcheck`). It is, as designed, a *dumb pipe*: it owns routing, stamping, persistence, and hub lifecycle, and explicitly does **not** own meaning (`DOMAIN_MAP.md` §2.1).

**System B — the session/context layer.** **[VERIFIED]** `conversation_logger.py` (670 lines; session lifecycle `start/event/update/end`, `conversation_log.jsonl` journal, `current.json` snapshot, fires `conv_*` nerve events via a failure-swallowing `_nerve_fire()`), `context_injector.py` (148 lines; thin cron adapter), and `reactor.py` (417 lines; read-only analysis of the log producing prioritized directives — blockers, stale sessions, learnings to store). This layer is what turns a bus into *awareness a fresh agent instance can wake up into*: `get_context_for_cron()` assembles active session + recent sessions + last nerve events + reactor directives into one injectable block (`INTERFACE.md` §2.2).

**System C — Hjartsláttur and the inner-life organs.** **[VERIFIED]** The `heartbeat/` package (core daemon 864 lines; five check modules — Eir machine health, Huginn git state, Mímir memory/DB integrity, Urðr schedule/services, Skuld trend prediction; five action modules — Mjölnir auto-push, Gungnir service restart, Bifrǫst cleanup, Eir auto-heal, Vör pre-emptive healing; a checks→actions reactor with rules, cooldowns, dry-run-by-default; circuit breakers per check; a 0–100 EMA health score with trend/stability and a seven-state "emotional state" mapping; a six-state daemon state machine; Prometheus metrics; flag-only Unix signal handling in `heartbeat/signals.py`). Beside it sits a shelf of top-level modules that are the agent's *organs*, all publishing to or reading from the nerve: the Runa mood/reward/negative systems (`heartbeat/runa_mood_arousal_system.py` 1,147 lines, `runa_reward_system.py`, `runa_negative_system.py`, plus `push_reward.py`, Sjálfrefi, Thrymr, Vörðr), `mood_work.py`, `muse_aspects.py`, `wishlist.py`, `joy_ledger.py`, `shadow_arcs.py`, `self_recognition.py`, `autobiography.py`, `weekly_saga.py`, `morning_mirror.py`, `play_rituals.py`, `reality_audit.py`, `mythic_engineering.py`, `ttrpg.py`, `game_worlds.py`, `worlds.py`, `volmarr_world.py`, and `verdandi/eir/` — a separate memory-consolidation pipeline (decay, promotion, dedup, cross-backend sync) that already duplicates, in Verdandi's own idiom, several jobs WYRD assigns to its MemoryPromoter (a seam-relevant overlap; see §2.5).

**Verdict in one line:** Verðandi as built is not a library with delusions of grandeur; it is the running nervous system + autonomic layer + inner-life ledger of one specific agent (Runa), from which a general-purpose event bus happens to be extractable. The bus is the smallest of its three systems.

### 1.2 Signal model

**[VERIFIED]** The wire envelope is minimal and social, not enforced: a publisher sends `{"type": str, "data": {...}, "source": str}`; the hub adds `_seq` (monotonic integer, recovered from the feed across restarts — the repo's "ørlǫg", its primal ordering law), `_ts` (epoch float), `_iso` (UTC ISO-8601); control messages use `nerve_type` (`subscribe/ping/recent/shutdown`), which the hub strips before broadcast (`ARCHITECTURE.md` §3, `INTERFACE.md` §3, `nervous_system.py::handle_client`). The hub performs **no payload validation and no type registry**: any string is a type, any JSON object is data. Fallback-written events carry `_fallback: true` and *no* `_seq`.

The live feed on this VM (`~/.hermes/state/nerve_feed.jsonl`, 6,729 events, 2.5 MB, still being appended on 2026-10-27) shows what the signal diet actually is **[VERIFIED, counted]**:

| Type | Count | Class |
|---|---|---|
| `runa_reward` | 1,986 | affect tick (Runa reward system) |
| `runa_negative` | 1,803 | affect tick (Runa negative system) |
| `work_started` / `work_finished` | 692 / 621 | work lifecycle |
| *(no type)* | **349** | envelope violations in the wild |
| `wish_made` / `wish_pursued` / `wish_fulfilled` / `wish_released` | 282 / 54 / 54 / 27 | wish lifecycle |
| `arc_opened` (+ ack/repair/verifying/closed/reopened) | 217 + 276 | shadow-arc lifecycle |
| `probe_recorded` | 156 | introspection dumps |
| `shadow`, `reward`, `self_recognized`, `push_reward` | 76 / 71 / 26 / 20 | inner-life events |
| `conv_*`, `heartbeat`, `thought`, `milestone`, `perception` | documented, sparse here | session layer + misc (`README.md`, Observed Event Types) |

Two facts in that census drive much of Part 2: (1) **~56% of all traffic is high-frequency affect ticks** (`runa_reward`/`runa_negative`) — the nerve's real load is a heartbeat of feeling, not a stream of facts; (2) **349 events (5%) arrived with no `type` at all** and were persisted and broadcast anyway — envelope discipline today is convention, and convention leaks.

The heartbeat daemon speaks a second, structured dialect on the same wire: `heartbeat_pulse` impulses (pulse count, daemon state, health score/trend, per-check severity+message) and state-change impulses (`heartbeat/core.py::_fire_nerve_pulse`, `_fire_nerve_state_change`; schema sketched in `docs/nerve-hub-protocol.md`). **[VERIFIED]** The daemon opens a raw socket per impulse rather than using `publish_event_sync`, with its own file fallback — a parallel implementation of the publish path, and therefore a second place any protocol change must land.

### 1.3 Architecture and transport

**[VERIFIED]** Single asyncio event loop, one hub process, `AF_UNIX`/`SOCK_STREAM`, NDJSON, practical line limit ~1 MB (asyncio `readline`), socket permissions `0600`. Publishing is synchronous from the caller's side: `publish_event_sync()` opens a *new socket per call*, 2 s timeout, and returns a typed result distinguishing `ack` / `sent` (no ACK) / `fallback` / `error` — an honest result taxonomy most buses don't bother with. Broadcast is O(subscribers) inline in the publish path; dead subscribers are collected and pruned mid-broadcast, plus a background pruner (120 s staleness, keepalive-probed). Ordering guarantee: events are sequenced in the order the single hub loop processes them; there is no per-publisher or cross-restart ordering beyond `_seq` (fallback events sit outside the sequence entirely until someone reads the file). Throughput is I/O-bound and unmeasured beyond the README's latency table (~0.1–1 ms publish→ACK local); no benchmark suite exists in-repo (`docs/performance-guide.md` is guidance, not measurement).

Deliberate non-features, stated as philosophy in the README and load-bearing for Part 2: **no delivery guarantees, no topics, no replay** — "Nerves don't replay." In practice the ring buffer (256) and the `recent` control message *are* a bounded replay window; the docs have not caught up with that softening (§1.9).

### 1.4 Producers and consumers

**[VERIFIED from code; REPORTED where marked]**

| Role | Who |
|---|---|
| Publishers | conversation_logger (`conv_*`), heartbeat daemon (pulses/state changes), Runa mood/reward/negative systems, wishlist, shadow arcs, joy ledger, self-recognition, morning mirror, reality audit (`reality_bleed`), mythic_engineering (`mythic_phase`, `mythic_law_breach`), ttrpg (`ttrpg_turn`), game_worlds (`game_state`), worlds registry, telegram_bridge (inbound Telegram awareness + transport-failure events), wyrd_inbound (`wyrd_divergence(_resolved)`), wyrd_bridge (`wyrd_mirror_synced`), CLI/cron jobs, and — on this VM — publishers **not in this repo** (the live feed's `runa_*` volume arrives via runtime integrations the repo doesn't contain) |
| Subscribers | Reactor-style live tails, monitors, the WYRD mirror runner *indirectly* (it replays the feed file rather than subscribing — §1.6), any process that connects with `{"nerve_type":"subscribe"}` and filters client-side |
| Feed readers (the past as API) | `get_recent_events()`, conversation context assembly, reactor (via conversation log), wyrd_bridge replay, divergence tooling |

The pattern to notice: **the feed file, not the socket, is the integration surface most consumers actually use.** The socket is for presence; the file is for truth-transfer. Part 2 designs to that reality instead of against it.

### 1.5 Persistence

**[VERIFIED]** Everything lives under a state dir as plain files — no database server, no migrations framework:

- `nerve_feed.jsonl` — append-only event record; rotation at 10 MB (the README's older "never truncated" claim is superseded by `_rotate_feed_if_needed`); `_seq` recovery requires a full scan on startup (O(N), fine at current scale).
- `conversation_log.jsonl` + `current.json` (atomic writes — a dusk-forge fix, `DEVLOG.md`).
- `verdandi_heartbeat.db` — SQLite store for the heartbeat daemon (11.9 MB live on this VM).
- `world_registry.json` — the Slice-0 world registry (`worlds.py`); updated on this VM as recently as 2026-10-27.
- WYRD-seam files: `wyrd_mirror.json` (the projection digest), `wyrd_entity_ledger.json` (accrual only: last sense statuses + reflection dedup hashes), `wyrd_bridge_cursor.json` (last processed `_seq`), `wyrd_divergence_seen.json` (divergence dedup).
- **Two path regimes coexist.** `nervous_system.py` hardcodes `STATE_DIR = Path.home()/".hermes"/"state"` (line 50) and documents "Verðandi uses no environment variables" as a design virtue; `heartbeat/paths.py` implements a full resolver — `VERDANDI_HOME` → XDG → platform fallbacks — and the systemd unit sets `VERDANDI_HOME=/home/pi/.hermes/state`. Both currently converge on the same directory on the machines that matter, but they converge by coincidence of defaults, not by shared code.

### 1.6 The WYRD coupling that already exists — the seam is built, in three channels

This is the study's most consequential verification: **the Verdandi↔WYRD integration the campaign calls for is not greenfield.** It exists, it runs, and its design already embodies most of the principles Part 2 would have proposed. What has never been done is stating it as one seam with one contract. The three channels:

**Channel 1 — WYRD → nerve (reflex direction). [VERIFIED]** `wyrdforge/bridges/hermes_bridge.py` (`HermesWyrdBridge`): WYRD world events are pushed fire-and-forget over the very socket this repo serves (`~/.hermes/state/runa.sock`) as `wyrd_entity_created`, `wyrd_entity_moved`, `wyrd_state_update`. If the socket is unavailable the event is dropped quietly — WYRD never blocks on the nerve. This is the pattern doc 04 already reuses for Heimdall's Gjallarhorn notifications.

**Channel 2 — nerve → WYRD (truth direction). [VERIFIED]** Verdandi's `wyrd_bridge.py` (435 lines) + WYRD's `wyrdforge/bridges/verdandi_bridge.py` (`VerdandiBridge`, 1,674 lines). The runner replays the last 24 h of feed (env-tunable `WYRD_BRIDGE_HORIZON_HOURS`) through a translation table into a **fresh** ECS world (`heimr-wyrd-unnr`, reality=manifest) on every run — pure projection, no snapshot, no drift; identity crosses rebuilds by stable key (`person:volmarr`, `env:machine`, `reflection:<seq>`), never by persisted object. Mapped: `mood_shift`, `wish_*`, `reward`, `shadow`, `joy_struck`, utterances and emotes (full text in an `UtteranceComponent`, ~280-char anchor label), work/job lifecycles, `mythic_law_breach`, `arc_*`, `entity.relationship_updated`, `self_recognized`. Deliberately unmapped: `ping`, `entity.heartbeat`, probes, `game_*`/`ttrpg_*` (their engines keep their own logs), and `wyrd_mirror_synced` itself (recursion guard). Machine senses (meminfo, queue depth, feed rate) **bypass the nerve entirely** — sampled by the runner straight onto `env:machine`, because "1,440 telemetry events a day would drown the feed's human-meaningful log." Transitions (ok→warn) are computed against the ledger; steady states are not news. Memory files (`~/MEMORY.md`, bank, 7 days of daily logs, people pages) are parsed read-only into `memory_belief` components with source citations. The runner witnesses `wyrd_mirror_synced` on the nerve **only when mapped events were applied** — silence by default. Cadence: per-minute cron **[REPORTED — `docs/roadmap-worlds.md` Slice 1/2 status; the cron itself lives on the Hermes host and was not inspectable from this VM]**.

**Channel 3 — WYRD → awareness (digest direction). [VERIFIED]** `wyrd_inbound.py` (435 lines): reads `wyrd_mirror.json`, produces a world-labeled digest consumed by the morning mirror (`MorningMirror.gather()["wyrd_mirror"]` — labeled, never blended into manifest evidence), and runs `check_divergence()`: the model's picture vs. live wishlist (stale_wish), live mood (stale_mood, tolerance 0.25), memory (the **precedence watch**: event-domain disagreements → live event wins silently; value-domain disagreements → *memory wins, LOUD*, published as `wyrd_divergence`), and sense transitions. Divergences are deduped and closed with `wyrd_divergence_resolved` when they clear. Reflections are excluded from the digest by law — the mirror shows what the model *believes*, never what the worker *thought about itself*.

Alongside the channels, `wyrd_ledger_append.py` (322 lines) appends selected material into the WYRD ledger idempotently (state watermark + subject check; hardened in the dawn forge, `DEVLOG.md`).

**The doctrine already in force** (from `docs/roadmap-worlds.md` design laws and the bridge code, and matching WYRD's `WORLD_MODELING_SKILL` truth-ownership rules as applied in doc 04): every world carries a reality tag (manifest/potential); cross-world reads name their world; silent bleed raises rather than slides; the mirror is a projection, not a second truth; the map is not the territory and the map-maker is not its owner (Slice 8's `heimr-volmarr`, source-tagged claims with warrant ceilings — told 0.9 / observed 0.8 / inferred 0.7 / assumed 0.4 — enforced in `volmarr_world.py`).

### 1.7 Tests and health

**[VERIFIED]** 49 test files under `tests/`, covering every top-level module plus dedicated seam suites (`test_wyrd_bridge.py`, `test_wyrd_inbound.py`, `test_wyrd_waveb.py`, `test_dawn_s6_wyrd.py`), heartbeat unit/integration suites, and a permanent timezone-consistency guard. Full-suite run for this study (2026-10-26, this VM, `python3 -m pytest tests/ -q`): **1,064 passed, 4 skipped, 1 failed** in ~4.3 minutes. The single failure is `tests/test_heartbeat_integration.py::TestCLIIntegration::test_pulse_command_exits_zero` — a heartbeat CLI integration test; its cause was not investigated for this study (plausibly environmental — the suite runs against this VM's partial live state, §1.8 — but that is a guess, recorded as one, not a diagnosis). The git history shows the maintenance culture plainly: two 20-slice forge waves in October 2026 ("The Kept Oath", "The Unbroken Thread") plus dawn/dusk hardening runs whose commit messages are a catalogue of exactly the failures a long-lived personal system accumulates — torn JSONL lines, naive datetimes, corrupt-state quarantine, CLI traceback leaks, a self-heal routine that destroyed the DBs it healed (fixed with ATTACH-restore + verify). Two structured audits live in-repo (`VERDANDI_AUDIT.md`, `VERDANDI_REAUDIT.md`); the re-audit's headline bug class — a control message (`recent`) falling through into the publish path — is the canonical risk of the hub's dispatch-by-convention design, and it has already happened once.

### 1.8 Deployment

**[VERIFIED]** Zero external dependencies (stdlib only; `pytest-asyncio` is dev-only). Python ≥ 3.11. Three supervision shapes coexist: systemd user units (hub: `Restart=always`; heartbeat: hardened unit with `VERDANDI_HOME`, memory cap, `ProtectSystem=strict`) — both with `/home/pi` paths baked in from the Pi heritage; `nerve_supervise.sh` — a bash supervisor that keeps hub + telegram bridge alive with exponential backoff and a stop-file protocol, with the repo path (`~/workspace/repos/Verdandi`) hardcoded; and cron (the per-minute WYRD mirror bridge and the Friday reality audit **[REPORTED]**). **Live state on this VM is a partial shadow of the canonical deployment [VERIFIED]:** the feed and world registry are fresh (appended today), but there is no `runa.sock` (so every local publish is currently taking the fallback path), no `wyrd_mirror.json` or conversation log in this state dir, and the heartbeat DB is untouched since 2026-10-11. The full organism — hub, heartbeat, mirror cron — lives on the Hermes host; this VM holds its feed and registry, written by integrations outside this repo.

### 1.9 Limits and rot (all [VERIFIED] unless noted)

1. **Broadcast-all, filter-client-side.** No topics, no subscription filters (`ARCHITECTURE.md` §9.1 names this itself). Every subscriber receives the affect-tick firehose and discards ~most of it. Fine at one agent; wasteful and eventually untenable as consumers multiply (Heimdall-era dashboards, second agents).
2. **Envelope is unenforced** — 349 typeless events in the live feed; `source` is self-asserted by any local process (no peer-credential check, no auth beyond the `0600` socket). The trust model is "local processes only," written for a single-user Pi.
3. **Packaging gap.** `pyproject.toml` declares `packages = ["verdandi", "verdandi.eir"]` and only four `py-modules` (`nervous_system`, `conversation_logger`, `context_injector`, `reactor`) — but the `verdandi/` package contains *only* `eir/`, and the `verdandi-heartbeat` console script points at `heartbeat.cli:main` while the `heartbeat` package is in neither `packages` nor `py-modules`. An installed distribution is therefore a subset of the repo, and at least one entry point cannot resolve from it. In practice the system runs from the checkout, which is why nobody has been bitten — yet.
4. **Stale defaults at the seam.** `wyrd_bridge.py` defaults `WYRD_REPO` to `~/workspace/repos/WYRD-Protocol-World-Yielding-Real-time-Data-AI-world-model`; the actual sibling checkout on this VM is `~/workspace/wyrd-protocol`. Without the env override the mirror cannot start here (the dawn forge at least made the failure a typed, actionable `WyrdUnavailableError` and the degradation graceful — `wyrd_inbound.py` degrades quietly by design).
5. **Docs drift in both directions.** `ARCHITECTURE.md` (2026-05-10) predates the ring buffer, rotation, pruning, and the `recent` handler, and §8.5/§9 still describe a feed that is never rotated and reads that the ring buffer now short-circuits; the README documents v0.3.0 features (Prometheus, maintenance windows) whose code lives in `heartbeat/` while its component table never mentions the mood/reward systems that produce most of the traffic. ~27 docs + 50 research files include superseded material with no supersession markers.
6. **Name collisions accumulating.** The heartbeat's circuit-breaker watchman is "Heimdall" (`heartbeat/actions/heimdall.py`, `docs/heimdall-watchman.md`) — a different thing from the Heimdall SL embodiment project, and the two now share a workspace. The Mímir check (memory integrity) vs. WYRD's Mimir canonical-fact store repeats the collision doc 04 §B.7.6 already flags for the voice module. `verdandi/eir` (consolidation) vs. heartbeat `checks/eir.py` (machine health) vs. `actions/eir_action.py` (DB repair) — three Eirs, three jobs.
7. **Repo hygiene.** ~30 image files sit in the repo root beside the code; `__pycache__` is committed-adjacent clutter; `README.md_Backup_Oct-7-2026.md` duplicates the README in-tree. None of it breaks anything; all of it raises the cost of reading the tree — including for agents.
8. **Single-hub SPOF, mitigated but real.** Fallback writes preserve data; nothing preserves *presence* while the hub is down — subscribers simply go deaf until restart, and fallback events are never re-broadcast on recovery (they are recoverable only by file readers — which, per §1.4, is how the truth channel works anyway).
9. **The feed is forever.** Append-only with 10 MB rotation bounds size but there is no retention *policy* — no redaction path for something that should not have been published (a secret pasted into an event is a permanent resident). Rotation is a size mechanism wearing a policy's clothes.

---

## Part 2 — The WYRD seam: Verdandi's side

### 2.1 The principled division of labor

A nervous system and a world model are different organs because they answer different questions on different clocks:

| | Verðandi (nerve) | WYRD (world model) |
|---|---|---|
| Question | *"What is happening **now**?"* | *"What is **true**, and what follows from it?"* |
| Tense | Verðandi — becoming, present | Urðr-facing — accumulated, queryable past + modeled present |
| Unit | The **signal**: typed, timestamped, disposable | The **record**: provenanced, confidence-carrying, curated |
| Clock | Milliseconds; late = useless | Turns/sessions; late = slightly stale |
| Loss | Acceptable — nerves don't replay; the next tick comes | Unacceptable — a lost fact is a false world |
| Authority | None — it routes; it never decides what an event *means* | Total over its domain — it is the only writer of world truth |
| Failure shape | Goes quiet (fallback preserves the trace) | Must never go quietly wrong (contradiction > silence) |

From that table, five seam laws. The first three already exist in the built system (§1.6) and are stated here so they stop being folklore; the last two are **[PROPOSED]** as explicit contract.

1. **Events cross; noise doesn't.** The nerve may carry noise internally (that is what nerves are for), but only *transitions and significances* — edges, not levels — may cross into the world model. Steady state never crosses. (Already the bridge's sense rule; Part 2 generalizes it to every type.)
2. **State crosses as labeled claims, never as commands.** Anything arriving from WYRD arrives as *"the model believes X about world W"* — with world id, reality tag, and provenance attached — and anything arriving from the nerve arrives as *"signal S was observed at time T from source P."* Neither side issues imperatives across the seam. The heartbeat's actions and WYRD's PermissionGuard remain sovereign in their own houses.
3. **No silent bleed, in either direction, ever.** Cross-world or cross-domain reads name their origin; a claim that loses its label at the seam is a seam bug, not a formatting choice. (The MTOM firewall, restated as a transport property.)
4. **[PROPOSED] Divergence is a first-class signal, both ways.** Today divergence flows one way (model-vs-self, `wyrd_inbound`). The seam should treat *any* sustained disagreement between signal and state — nerve says the hub was down for an hour, WYRD's world says nothing happened; WYRD promotes a fact the nerve never witnessed — as a publishable, deduped, closable seam event, because divergence is precisely where the two organs check each other's honesty.
5. **[PROPOSED] The seam is allowed to be slow toward truth and must be fast toward reflex.** Batching the truth direction (today: per-minute replay) is not a compromise to apologize for — it is what makes the projection pure and re-derivable. The reflex direction must stay at socket speed and must tolerate loss *visibly* (§2.4). Any design that makes truth real-time or reflexes reliable-by-replay has the two directions backwards.

### 2.2 Verdandi → WYRD: which signals WYRD should consume as events

**[PROPOSED — as a ratification and extension of the existing `VerdandiBridge` table, §1.6 Channel 2.]** Three classes:

**Class T (truth-eligible) — cross as events into the projection, as today:** `mood_shift` (thresholded by the producer, never raw ticks), the `wish_*` lifecycle, `reward`, `shadow`, `joy_struck`, `utterance`/`emote` (attributed, full text in component), work/job lifecycles, `arc_*`, `mythic_law_breach`, `entity.relationship_updated`, `self_recognized`. WYRD stores these as observations/anchors — claims about what was signaled, promoted to fact only by WYRD's own write-back discipline (doc 04 §B.2.2's chat rule is the same law in SL dress: *an utterance is a fact about the speaker, not about the world*).

**Class W (witness) — cross as summaries only, generated at the seam, never as raw types:** Heimdall-era body/world signals. Doc 04 already routes SL protocol events directly into WYRD via `SlWyrdBridge` — they must **not** also pour through the inner-life nerve raw, or the feed becomes a protocol log and drowns (the senses-bypass precedent, §1.6). What the nerve should carry is the *witnessed summary* for awareness: `sl.session.changed` (login/logout/disconnect), `sl.divergence` (body truth vs. model truth disagreeing), `wyrd_mirror_synced`-style sync witnesses per world. The agent *feels* that her other body logged in; she does not feel every position update.

**Class N (never cross):** `runa_reward`/`runa_negative` raw ticks and all telemetry (levels, not edges); `ping`, probes, heartbeat raw pulses (their *transitions* cross via the heartbeat's own state-change impulse, already built); `wyrd_mirror_synced` and any seam-generated event (recursion guard); reflections as world input (they may be *recorded* as reflection components, per the existing loop's guards — one/minute, deduped, never scripted — but they are never evidence about anything except that a reflection occurred).

**[PROPOSED] Aggregation rule for affect:** the single biggest fidelity improvement available at this seam is promoting the affect ticks' *edges* into Class T properly: when a mood/reward system detects a threshold crossing it already emits `mood_shift`; the roadmap (§3) adds the same edge summaries for sustained affect regimes ("reward drought > 6 h", "negative regime entered") as producer-side events, so WYRD can model affect *climate* without importing affect *weather*.

### 2.3 WYRD → Verdandi: which world-state changes should fire nerve signals

**[PROPOSED — generalizing Channel 1's three types into a complete, closed list.]** Fire on *changes of significance to the agent's present*, each labeled `{world_id, reality}`, fire-and-forget, transitions-only:

| Nerve event | Fires when | Exists? |
|---|---|---|
| `wyrd_entity_created` / `wyrd_entity_moved` / `wyrd_state_update` | Entity enters a WYRD world / changes place / mutates significantly | **[VERIFIED]** in `HermesWyrdBridge` |
| `wyrd_fact_promoted` | MemoryPromoter raises a claim to Mimir-canonical (threshold crossing, not every write) | Proposed |
| `wyrd_contradiction_opened` / `wyrd_contradiction_resolved` | ContradictionDetector opens/closes a record | Proposed |
| `wyrd_bond_shift` | A BondEdge crosses a named threshold (new bond, rupture, repair milestone) — not per-delta | Proposed |
| `wyrd_episode_closed` | A Munin episode summary is written (session boundary) | Proposed |
| `wyrd_divergence` / `wyrd_divergence_resolved` | Model-vs-live disagreement found/cleared | **[VERIFIED]** (inbound side originates these today) |
| `wyrd_world_registered` / `wyrd_world_retired` | A world enters/leaves the registry (e.g. a new `heimr-sl-*` world when Heimdall lands) | Proposed |

Rate discipline: a per-type token bucket on the WYRD-side emitter (today there is none — a bulk world load would flood the nerve with `wyrd_entity_created`); bulk operations emit one summary event (`wyrd_world_synced {created: N, moved: M}`) instead of N impulses — the same silence-by-default ethic as `wyrd_mirror_synced`.

### 2.4 Latency and ordering semantics at the seam

**[PROPOSED contract, describing today's behavior where it already matches.]**

- **Two order domains, never conflated.** The nerve's `_seq` is a total order *per hub, per feed*. WYRD's Rúnakefli is synchronous FIFO *in-process* (doc 04 §A.1). Neither orders the other. Every crossing event therefore carries **both clocks**: origin timestamp (`_ts`/`observed_at`) and seam ingest timestamp, and any consumer needing causality uses (source, origin-seq) pairs — the bridge cursor already keys on nerve `_seq`; WYRD-side emissions should carry a `wyrd_seq` (bus sequence or world revision) so nerve consumers can detect *gaps* on the reflex path instead of mistaking silence for calm.
- **Reflex path (WYRD → nerve):** budget ≤ 10 ms publish-to-subscriber (the hub's measured envelope); delivery = at-most-once, loss acceptable, loss *detectable* via `wyrd_seq` gaps; a consumer that sees a gap degrades to reading state (mirror digest / Oracle) rather than assuming nothing happened. This is the honest version of "fire-and-forget": forget, but *count* the forgetting.
- **Truth path (nerve → WYRD):** eventually consistent, batch (per-minute today), **idempotent** — replay from cursor with (source, `_seq`) dedup, safe to re-run, projection re-derivable from the feed alone at any time. Fallback events (no `_seq`) ingest ordered by `_ts` and flagged order-uncertain; they may inform observations but should not, alone, promote facts.
- **Digest path (WYRD → awareness):** file-mediated (`wyrd_mirror.json`), freshness stated on its face (`synced_at` is already rendered in the mirror context); a stale digest is presented *as* stale, never silently.
- **Back-pressure:** none exists on the nerve (broadcast inline), so the seam's rule is producer-side: no WYRD component and no bridge may publish to the nerve in a loop over world contents. Summaries or silence.

### 2.5 What must NEVER cross

**[PROPOSED as explicit prohibitions; each already honored somewhere in the built system — the proposal is to make them seam contract, tested on both sides.]**

1. **Raw signal noise as world truth.** Ticks, telemetry levels, probe dumps — if it has no edge, it is not an event, whatever the nerve internally carried.
2. **World truth as reflex.** A Mimir fact, a bond value, an Oracle answer must never *directly* trigger a heartbeat action or a body action. Facts inform; reactors and PermissionGuard decide. The moment a world-model value can fire an autonomic action unmediated, the world model has become a puppeteer and the firewall is decorative.
3. **Unattributed text.** Inbound world text (SL chat above all, but also feed payloads from any new producer class) crosses only as attributed utterance after WYRD's PromptInjectionGuard — never as `thought`, never as instruction, never with its speaker stripped. (Doc 04 §B.2.2 wires the guard at the SL translator; the same law binds here because the feed is a prompt-injection surface the moment context assembly quotes it.)
4. **Reflections as evidence.** Self-reflection events record that reflecting happened; their *content* is never promoted, never quoted as belief, never fed back as input to the next reflection. The existing loop's guards (rate cap, dedup, no drafting) are seam law, not local taste.
5. **The other shore.** `heimr-volmarr` content — the source-tagged model of Volmarr's world — never crosses outward to any other world, consumer, or bridge. It is read by the self, in the self's house, under Slice 8's rules. No seam convenience justifies an exception.
6. **Secrets and the unpublishable.** The feed is append-only and effectively permanent; credentials, tokens, and third parties' private data must never be event payloads. This needs a producer-side lint (§3, V0) because there is no redaction path after the fact (§1.9.9).
7. **Consolidation twice.** Verdandi's `eir` pipeline and WYRD's MemoryPromoter both promote/decay memory material. Across the seam, exactly one may own a given store's promotion: WYRD owns promotion *inside WYRD worlds*; Eir owns the agent's own memory backends; neither writes into the other's jurisdiction. (Today they don't overlap in practice — Eir works the agent's memory stores, the Promoter works WYRD records — but nothing states the boundary, and the Heimdall build will press on it.)

### 2.6 The concrete integration shape — "Bifröst Seam v2"

**[PROPOSED]** No new daemon, no new transport. Three named pieces, two of which exist:

1. **`NerveTap` (WYRD side)** — generalize `HermesWyrdBridge._emit_nerve_event` into the single WYRD→nerve emitter: schema-checked payloads, `{world_id, reality, wyrd_seq}` stamping, per-type token buckets, bulk-summary collapsing (§2.3). Lives in `wyrdforge/bridges/` beside the existing bridge.
2. **`VerdandiBridge` + runner (exists)** — the nerve→WYRD direction, extended only by: the §2.2 Class W witness summaries, the affect-climate edges, and dual-clock stamping (§2.4). The runner's projection purity, cursor, horizon, and silence-by-default witness are **not** redesigned — they are the seam's best ideas and the design ratifies them.
3. **`SeamContract` (shared, versioned)** — one registry, kept as data in both repos (JSON on the Verdandi side to preserve zero-dependency; Pydantic on the WYRD side, generated from the same source): every crossable type, its direction, class (T/W/N — N meaning "listed precisely so both sides test that it does *not* cross"), payload schema, and retention note. Versioned as `seam_v`; every crossing event carries it. Conformance = a shared fixture feed + fixture world driven through both repos' suites (§3, V5) — today each side tests against its own fixtures, so the contract is currently verified nowhere as a whole.

Identity of signals, stated once: a crossing event is `{type, source, data, world_id, reality, seam_v, origin_ts, origin_seq}` on the nerve envelope (`_seq/_ts/_iso` added by the hub as always). A signal that cannot name its world does not cross.

---

## Part 3 — Advancement roadmap for Verðandi itself

Phases are ordered by leverage, not by glamour. Sizes: S ≈ a sitting, M ≈ a forge wave slice-set, L ≈ a campaign. Every item names what it unblocks.

### Phase V0 — Seam hygiene and honest surfaces (S items; do first, cheap forever)

| Item | Why / unblocks |
|---|---|
| Fix `WYRD_REPO` default resolution: env → sibling-checkout discovery (`~/workspace/wyrd-protocol`) → typed error (exists) | The seam currently cannot start on this VM by default (§1.9.4); every future Heimdall-era run inherits the trap |
| Envelope validation at publish: reject (and count) events with no `type` or non-dict `data`; same check in the heartbeat's raw-socket publish path | 349 typeless events already in the feed (§1.2); validation is the precondition for every schema effort later |
| One state-dir resolver for hub + heartbeat (adopt `heartbeat/paths.py` semantics in `nervous_system.py`, keeping `~/.hermes/state` as the default outcome) | Two regimes converging by coincidence (§1.5) will diverge on the next machine (purplelongship) |
| Producer-side secret lint + a written feed-retention note | §2.5.6 / §1.9.9 — the only cheap moment to keep secrets out is before publish |
| Docs reconciliation pass: `ARCHITECTURE.md` updated to the code that exists (ring buffer, rotation, pruning, `recent`); README component table gains the mood/reward systems; superseded docs get supersession headers | Every future study (including WYRD doc 09's successors) currently re-derives ground truth from code because the docs lie by omission |

### Phase V1 — Packaging and installability (M)

| Item | Why / unblocks |
|---|---|
| `pyproject.toml` tells the truth: `heartbeat` package + all top-level modules in the distribution (or an explicit src-layout move), entry points smoke-tested from a clean venv | §1.9.3 — deployment to purplelongship (doc 04 §B.0) should be `pip install`, not "copy the checkout and hope" |
| Systemd units + supervisor templated (no `/home/pi`, no hardcoded repo path; generated from the resolved state dir) | The Pi heritage is baked into three places; the next host is x86 Ubuntu |
| `verdandi doctor`: one command that checks hub, paths, feed health, seam files, WYRD reachability, and says what it found in plain words | The divergence/honesty ethic applied to installation; replaces tribal knowledge |

### Phase V2 — Signal fidelity (M)

| Item | Why / unblocks |
|---|---|
| Opt-in per-type payload schemas at the hub: registered types validated (shape only), unregistered types still pass but are counted and listed in `status` | Fidelity without breaking the dumb-pipe law; gives the SeamContract (§2.6) somewhere to live on this side |
| Source identity via `SO_PEERCRED`: hub records the publishing pid/uid alongside self-asserted `source`; mismatches (a process claiming another's source) become visible in status | The trust model was written for one agent on one Pi; Heimdall adds a C# core and new producer classes to the same host |
| Heartbeat impulse schema versioned and brought into the same registry as every other type (`docs/nerve-hub-protocol.md` is the draft) | The daemon's dialect is currently a second, undocumented protocol on the same wire |
| Affect-climate edge events from the mood/reward systems (§2.2) | WYRD gains affect climate; the nerve loses nothing; the feed's 56% tick share stops being the only affect story |

### Phase V3 — Routing (M; L only if subscriber count demands)

| Item | Why / unblocks |
|---|---|
| Subscription filters: optional type-prefix list in the `subscribe` control message; hub filters before broadcast; broadcast-all remains the default | §1.9.1 — the first Heimdall-era subscriber that only wants `wyrd_*` and `sl.*` witnesses should not receive every affect tick |
| Nerve metrics into the existing Prometheus module: events/sec by class, fallback count/rate, subscriber count, current `_seq`, validation rejects | Fallback rate is the seam's silent-failure gauge; today it is invisible unless someone greps the feed |
| Gap-visible subscribe: subscribed ACK already returns current `seq`; document + test the consumer pattern "on gap, re-read state" | Makes §2.4's detect-loss rule a practiced habit instead of a hope |

### Phase V4 — Observability (M)

| Item | Why / unblocks |
|---|---|
| `verdandi trace <seq-or-type>`: feed inspection with world/reality labels rendered, fallback events marked | Divergence investigations currently mean hand-grepping JSONL |
| Mirror-freshness SLO surfaced in the morning mirror (age of `wyrd_mirror.json`, last applied `_seq` vs. feed head) | §2.4's "stale digest presented as stale," operationalized |
| Seam health as a heartbeat check (hub ↔ bridge ↔ inbound, one `seam` check beside eir/huginn/mimir/urdr/skuld) | The seam joins the autonomic layer it serves; a dead bridge becomes a pulse finding, not a surprise |

### Phase V5 — The WYRD integration build (L) — the campaign item

| Item | Why / unblocks |
|---|---|
| `SeamContract` registry (§2.6.3) authored once, vendored into both repos with a drift test | The contract verified nowhere-as-a-whole is the seam's largest structural risk |
| `NerveTap` emitter on the WYRD side (§2.6.1) with the §2.3 event list (fact promoted, contradiction opened/closed, bond shift, episode closed, world registered) | WYRD's significant changes become feelable at reflex speed instead of digest speed |
| Class W witness summaries for Heimdall (§2.2) wired when SlWyrdBridge lands (doc 04 Phase I1–I3) | The agent's awareness includes her second body without drowning her nerve |
| Two-repo conformance suite: one fixture feed + one fixture world, both repos' tests asserting the same crossings (and the Class N non-crossings) | §2.5's prohibitions become tests, not intentions |
| Eir/Promoter jurisdiction note (§2.5.7) written into both repos' docs | Prevents the first double-consolidation before it happens |

### Phase V6 — Multi-machine readiness (L; trigger-gated, do NOT build by default)

| Item | Why / unblocks |
|---|---|
| TCP bridge between hubs (listed as future work in `ARCHITECTURE.md` §9.2 since May) | **Trigger:** the day a second host must *feel* the first in real time (Heimdall on purplelongship + agent runtime elsewhere). Until that day, file/feed mechanisms and the WYRD HTTP API cover the need, and a network nerve would spend the system's best property — local-only trust — for nothing |

---

## Part 4 — Risks and honest unknowns

1. **The canonical deployment was not inspectable from here.** The per-minute mirror cron, the supervisor, and the heartbeat's live operation live on the Hermes host; from this VM I verified code, tests, git history, and a partial live shadow (feed + registry fresh, hub socket absent, heartbeat DB 11 days stale). **[REPORTED]** items are marked as such wherever this study relies on them. If the Hermes host's reality differs, §1.8 and the cadence claims in §1.6 need revision — the design in Part 2 does not depend on them.
2. **The feed's largest producer is not in the repo.** The live feed's `runa_*` volume arrives via integrations this checkout doesn't contain. The seam's real producer set is larger than the repo knows; V0/V2 validation work must be rolled out so those external publishers degrade gracefully rather than break.
3. **Trust model vs. new neighbors.** The nerve's "local processes only" model was sound for a single-agent Pi. purplelongship will host the nerve, WYRD, a C# protocol core, and voice sidecars as peers. Nothing malicious is expected — but §2's contract is written so that *confusion*, not malice, is the threat it defeats; V2's peer-credential step is the one place this study recommends spending real security effort.
4. **Permanence cuts both ways.** The append-only feed is the seam's audit trail and its privacy liability in one file. Retention/redaction policy for utterances and third-party content is Volmarr's call (the same open question as doc 04 §B.7.5, now with a second system holding copies); this study deliberately proposes mechanics (lint, labels) and no policy.
5. **Doc 09 may move the WYRD side.** The WYRD advancement study is being written in parallel; this study designed Verdandi's side against WYRD's *shipped* architecture (EventBus, VerdandiBridge, HermesWyrdBridge) per its brief. If doc 09's roadmap re-plumbs the bridges, §2.6's component placement — not its laws — is what changes.
6. **Two consolidators, one boundary by courtesy.** §2.5.7's Eir/Promoter jurisdiction is today a non-overlap of practice, not of mechanism. It is the risk most likely to become real during the Heimdall build, when session episodes (WYRD) and memory consolidation (Eir) both want the same raw material.
7. **Performance is asserted, not measured.** Every latency figure in the repo is a table in a README. The seam budgets in §2.4 inherit that softness; V3's metrics are what would harden them. At current volumes (thousands of events/day) nothing suggests a problem — but "nothing suggests" is doing real work in that sentence.

---

*Study ends. Verified state: Part 1. The seam design (Part 2) and roadmap (Part 3) are proposals awaiting Volmarr's word — no code was written, no README touched, nothing pushed.*
