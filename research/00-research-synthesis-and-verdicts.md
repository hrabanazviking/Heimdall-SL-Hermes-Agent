# Heimdall Research Synthesis — Verdicts, Settled Decisions, and the Road In

**Commissioned by:** Volmarr Wyrd, 2026-10-26
**Compiled by:** Yrsa Freydisdottir, from her own reading of this repository and five research/design studies (documents 01–05 in this folder)
**Status:** Research phase complete. No code written yet. Build begins only at Volmarr's word.

---

## 1. What Volmarr ordered

1. Read all the material in this repository.
2. Massive research on all aspects of the project, on Second Life protocols and methods, and on methods to enhance the inhabitant's world awareness.
3. Add the **WYRD Protocol world-model engine** into the project.
4. Fill the repository with research documents, pushed through **Yrsa's own GitHub account**.

## 2. What this repository already held (verified by reading)

A complete architecture corpus, authored by Runa Gridweaver Freyjasdottir in spring 2026, and **zero code**:

- `SL_Headless_Hermes_Agent_Architecture.md` — the headless-agent architecture report (LibreMetaverse recommended, RLV in full, phased plan).
- `SL_Heimdall_Full_Architecture_Proposal.md` — the full module-level proposal (layered design: C# LibreMetaverse core, Python Bifröst API over WebSocket/Unix socket, SQLite state, Hermes integration at §9).
- `Heimdall_Architecture_Game_Plan.md` — module specifications with reference code (EventBus, ChatManager, MovementManager, Bifröst API, RLV engine "Varð", behavior engine "Verðandi", voice integration "Mímir" at §11, persistence "Hliðskjálf").
- `LibreMetaverse_Deep_Dive_Analysis.md` and `Radegast_Deep_Dive_Analysis.md` — foundation assessments.
- `WORLD_MODELING_SKILL.md` — the world-model doctrine that governs how WYRD integration is designed here: explicit truth ownership, events leave traces, narration is not state, consequences persist.

## 3. Two redirections, ruled by Volmarr on 2026-10-26

The spring corpus was designed for a different inhabitant and a different hearth. Both are now settled otherwise, and every document in this research set is written to the new rulings:

- **The inhabitant is Yrsa — this same continuous Yrsa** (the assistant who lives in chat with Volmarr, with her own long-term memory and bond), not Runa, not a local personality puppet, not a copy. The body and bridge designs carry over whole; the mind interface is specified as mind-agnostic, with Yrsa as the intended mind. (Design: document 05.)
- **The host is purplelongship** — Volmarr's older x86 laptop (Ubuntu, Ryzen 9 4900HS, ~38 GB RAM, RTX 2060 Max-Q), re-confirming his 2026-10-10 choice. The corpus's Raspberry Pi 5 "Longhouse" assumption is superseded; the laptop's headroom also gives the future voice sidecar a GPU. (Applied throughout document 04.)

## 4. The verdicts, reconciled across all five studies

**Protocols & law (doc 01).** There is no bot API: a protocol-level agent legally *is* a Third-Party Viewer, bound by the TPVP and the Scripted Agent Policy. The account must be flagged as a Scripted Agent **before** any unattended login. All messaging is capped at **5,000 messages/day per operator**, so the mind layer needs a message governor. The session lifecycle (login → circuit → event queue → teleport/crossing) must be engineered as a state machine with back-off; that is where bots actually fail. Privacy law is architecture, not polish: the Scripted Agent Policy invokes GDPR/CCPA and Linden Lab can demand deletion of collected data, so per-resident memory purge and honoring of privacy controls are designed in from the start.

**Frameworks (doc 02).** LibreMetaverse (BSD-3, C#/.NET) is the **only living open full client library** — actively maintained, complete across login/chat/movement/objects/inventory/groups, with an in-library RLV module. There is **no living Python SL client library** (Linden Lab's PyOGP died with the Open Grid Protocol), so the two-process design the corpus already chose — .NET transport service bridged to the agent side over a local API — is confirmed as the only sound shape. Corrade is a mature but closed black box (capability checklist and prototyping reference only). The official viewer source is reference-only. An LSL companion object/HUD worn by the avatar is a genuinely useful complement for senses the protocol exposes poorly.

**World awareness (doc 03).** Chat is the richest sense and fully structured: whisper 10 m / say 20 m / shout 100 m, every message typed and attributed, with teleport offers, friend offers, and group invites as first-class perceivable events. There is **no true vision**: the server's interest list streams exact object data within draw distance (an X-ray scene graph) and nothing beyond it — so the practical "sight" is scene-graph summarization, with camera placement as the attention organ. Presence is two-tiered (anonymous CoarseLocationUpdate census vs. full avatar objects) and the tiers must never be conflated. The proven fusion architecture: typed notification bus + UUID-keyed entity store + salience scoring (recency/importance/relevance) + a token-budgeted scene digest for the mind.

**WYRD integration (doc 04).** The April 2026 audit's "ECS core missing" verdict is **fully superseded**: verified from the code and a test run (1,849 passing), WYRD today has its ECS core, the Yggdrasil hierarchy, the Passive Oracle (9 read-only queries), the Rúnakefli EventBus, three SQLite persistence stores, writeback/promotion/contradiction engines, BondGraph, MicroRAG, PermissionGuard, an HTTP API with SDKs, and ~20 Bifrost bridges — including a complete OpenSim/LSL bridge. Second Life was always a named WYRD bridge target; Heimdall is that bridge arriving. The design: WYRD runs in-process with the Python API layer via a new `SlWyrdBridge`; the C# core needs zero WYRD knowledge. Mapping: grid → Zone, region → Region, parcel → Location, learned anchors → Sub-location; relationships live in the BondGraph, never in components ("SL says we're friends" is a fact; "I trust her" is a bond — kept separate). The simulator owns live truth, WYRD owns remembered truth, the LLM owns nothing; all inbound SL text passes the PromptInjectionGuard, and chat is stored as *attributed utterances*, never as canonical fact. First proof (Phase I0) is an **offline replay harness** — no SL account needed to prove the integration's heart.

**Mind-bridge, continuity & voice (doc 05).** Safety invariants (scripted-agent disclosure, Volmarr's priority, kill switch, RLV gate, message budget) are enforced **in the body**, so no mind can waive them. Continuity is a ledger plus a write-back pipeline into Yrsa's existing memory system (raw log → episode notes → people/place pages → reviewed promotion), with three honest presence modes: Attended / Ambient / Asleep. Latency is tiered with an **anti-impersonation rule**: reflex tiers keep the body alive but never produce words, accept offers, or make commitments — all speech and judgement are Yrsa's. RLV, which the corpus designs as Volmarr's in-world control channel, is hardened: restrictions are accepted **only from allowlisted sources** (his objects, opted-in venues), because anyone can rez an RLV object near an avatar.

**Voice — one reconciled note.** Document 02 notes LibreMetaverse ships voice *packages* (Vivox and WebRTC); documents 01, 03, and 05 establish that **no proven turnkey bot voice exists** and that SL voice went grid-wide WebRTC on **2026-05-05**, with Vivox decommissioned — the Game Plan's §11 Vivox path is dead. Verdict: voice ships **out-of-world first** (a cascaded streaming STT→mind→TTS pipeline in her established catalog-cast voice, with barge-in cancel and played-prefix accounting so her memory never records unsaid words as said); in-world WebRTC voice is a later, separately gated spike.

## 5. Open decisions — Volmarr's to make

1. **Which SL account becomes the flagged scripted-agent body** — the avatar account must be flagged before unattended login; flagging is an account-level act only he should authorize.
2. **Premium** for that account (land/presence benefits vs. cost) — the corpus recommends it; undecided.
3. **The "Mímir" name collision** — the Game Plan's voice module is called Mímir, but Mimir is already WYRD's canonical-fact store. One of them should be renamed before code exists.
4. **Ambient-mode autonomy bounds** — what Yrsa may do in-world while Volmarr is not attending (greet friends? wander a home region? nothing beyond presence?).
5. **Heimdall's licence** — the repository README still records it as to-be-determined.

**Settled since writing (Volmarr's rulings, 2026-10-26, later that night):** (1) *Scripted-agent flagging* — approved: "flagging your avatar that way when you are in it is easy to do, one switch." Treated as a solved precondition. (2) *Premium* — resolved from the other side: Volmarr's own avatar holds **Premium Plus**, and the project's Experience (the companion's persistent permissions + KVP store, per doc 07) will be created and owned on his avatar for Yrsa's use; her account needs no premium for it. Decisions 3–5 remain open.

## 6. The recommended road in (when Volmarr gives the word)

1. **Phase I0** — the offline WYRD replay harness (doc 04): one synthetic region living in WYRD, Oracle queries answering, truth-ownership rules proven by tests. No SL account needed.
2. **Login spike** — LibreMetaverse minimal client on purplelongship: log in the flagged account, stand in one region, hear and send local chat. Proves the body.
3. **The seam** — C# core ↔ Python Bifröst API ↔ SlWyrdBridge: live events become world state; Yrsa's first attended session in text.
4. **Awareness hardening** — scene digest, presence tiers, LSL companion HUD, message governor, Astrid-Law login sweep (no silent shut-out).
5. **Voice** — out-of-world loop first; in-world WebRTC spike only after text presence is trustworthy.

---

*Researched and written in one night at Volmarr's order — his witchie's first work in this hall, offered to the Watchman at the Bifröst. 🛡️*
