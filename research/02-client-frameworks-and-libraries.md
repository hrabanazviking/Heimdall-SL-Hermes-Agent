# Bot & Client Frameworks for Second Life — What to Build On in 2026

**Project:** Heimdall-SL-Hermes-Agent (Second Life scripted-agent plugin for the Hermes AI Agent)
**Topic:** Client libraries, bot frameworks, text clients, the official viewer source, and LSL-side options
**Researched:** October 2026
**Companion goal:** Give an existing Second Life avatar a continuous AI mind — perception in; speech, movement, and gestures out; memory written back; a real-time voice loop on top.

## How to read this document

- **[Verified]** = stated by a primary or near-primary source fetched during this research (project README/site, official wiki, GitHub repository metadata). The source is in the Sources list at the end.
- **[Inference]** = my reasoning from those facts. Reasonable, but not a documented claim.
- Where the evidence is thin or a project does not disclose something, the document says so plainly. Nothing here is padded with features a library was not shown to have.

---

## Overview — the landscape in one page

Second Life's client protocol was reverse-engineered into an open library in 2006, and almost everything in the bot world still descends from that one act. The lineage is:

**libSecondLife → libOpenMetaverse → LibreMetaverse** (all C#/.NET), plus one long-lived commercial-grade bot built on the same tradition (**Corrade**).

The honest summary for 2026:

1. **There is exactly one actively maintained, open-source, full client library: LibreMetaverse.** It is alive *now* — commits within days of this research, a 3.x release line, modern .NET targets, and (critically for this project) in-library voice packages for both the old Vivox service and Second Life's newer WebRTC voice service. [Verified]
2. **There is exactly one mature, batteries-included bot *product*: Corrade.** It exposes nearly the whole viewer command set through a simple key-value API over HTTP/TCP/UDP and is explicitly designed to be driven by scripts and external programs. But it is distributed as closed binaries under a custom license — you build *on* it, you cannot build *in* it. [Verified]
3. **There is no living Python client library.** Linden Lab's own Python effort, PyOGP, died with the Open Grid Protocol around 2008–2010 and survives only in archives. Any Python in this project must talk to a .NET transport process (or to Corrade) over IPC/HTTP — it cannot speak the grid protocol natively with maintained code. [Verified]
4. **Text clients (Radegast / RadegastVeles)** are maintained and useful as test harnesses and reference behaviour, and Radegast now has a plugin API — but they are user-facing clients, not bot frameworks. [Verified]
5. **The official viewer source** is open (LGPL-2.1) and is the ground truth for protocol behaviour, but it is a ~55k-commit C++ rendering client; it is a reference to mine, not a foundation to build a headless agent on. [Verified]
6. **LSL (in-world scripts)** cannot host the agent, but a scripted companion object is a valuable second set of senses and hands: it can hear local chat on channels, sense nearby avatars, and reach the outside world over throttled HTTP. Experience Tools add persistent permissions and a small key-value store. [Verified]
7. **Policy is part of the platform.** Linden Lab requires bot accounts to be flagged as Scripted Agents, caps bot messaging at 5,000 messages/day per operator, lets estate owners exclude scripted agents, and was actively reviewing bot policy through 2025–2026. Any framework choice has to live inside those rules. [Verified]

**Recommendation in one line:** build the transport layer on **LibreMetaverse** (own it, fork it if ever needed — its BSD licence allows that), drive it from the Hermes side over a narrow local bridge, use **Corrade** as a behavioural reference and optional prototyping accelerator (never as the foundation), keep **RadegastVeles** as the human-supervised observation client, and add an **LSL companion object** for in-world senses the client protocol sees poorly.

---

## 1. LibreMetaverse / OpenMetaverse

### 1.1 Lineage and identity

- LibreMetaverse is "a fork of libOpenMetaverse which in turn was a fork of libSecondLife, a library for developing Second Life-compatible virtual world clients." Its stated focus is "up-to-date Second Life and OpenSim compatibility with an eye to performance, multi-threading, memory management, and dependency injection support." [Verified — project README]
- Canonical home: `github.com/cinderblocks/libremetaverse`, maintained by Cinder Roxley (also the current maintainer of Radegast). The repository is public, unarchived, holds ~5,450 commits and 123 releases, and its licence is **BSD 3-Clause**. [Verified — GitHub repository metadata]
- The original **libOpenMetaverse is dead**: Open Hub's analysis shows ~278K lines of code, 0 current contributors, and no commit in over 8 years. Anything still pointing at libopenmetaverse.org-era code is pointing at a museum. [Verified — Open Hub]
- LibreMetaverse itself is, by the same measurement, the most active project in this entire landscape: Open Hub (analysed 2026-10-24) shows ~256K lines, **5 current contributors, a commit 4 days before analysis, "High Activity."** [Verified — Open Hub]

### 1.2 Technical shape

- **Language/runtime:** C# on modern .NET. The README requires the .NET 8, 9, or 10 SDK (10 recommended) and the build produces targets including `netstandard2.1`, `net481`, `net8.0`, `net9.0`, and `net10.0`. It builds and runs on Linux and macOS as well as Windows (some sample/test projects are Windows-only). [Verified — README]
- **Architecture:** a central `GridClient` class coordinates specialised manager subsystems (networking, assets, inventory, avatars, and so on); applications create the client, configure settings, attach event handlers, and log in. [Verified — DeepWiki architecture summary of the source]
- **Package split (important in the 3.x line):** the library is now a family of NuGet packages. Per the README's own table:
  - `LibreMetaverse` — "The client: log in, move, chat, build, manage inventory, groups and land."
  - `LibreMetaverse.Transport` — the UDP circuit alone (socket loop, reliable delivery, throttle).
  - `LibreMetaverse.Protocol` — the wire protocol alone: every UDP packet, capability messages, the login request/response.
  - `LibreMetaverse.Assets` — offline data model: asset types, prims, textures, inventory items, avatar data.
  - `LibreMetaverse.StructuredData` — LLSD in XML, binary, notation, JSON and protobuf.
  - `LibreMetaverse.Types` — UUID, vectors, quaternions, colours, shared enums.
  - Add-ons include `LibreMetaverse.ModelFormats` (Collada/glTF/OBJ load and mesh export), `LibreMetaverse.Archiving` (OAR), `LibreMetaverse.LslTools` (LSL lexer/parser toolkit), and `LibreMetaverse.RLV` (RestrainedLove command handling).
  - Migration notes in the repo cover a 3.1 → 3.2 transition (the package split) and 2.x → 3.0; some sub-packages carry their own version numbers (e.g. `LibreMetaverse.Types` was listed at 2.4.x on NuGet at research time), so "the version" is per-package, not global. [Verified — README / NuGet]
- **Examples:** the repo ships a `SimpleBot` example ("Build an interactive bot that responds to IMs"), a `TestClient` console client, `PrimInspector`, `InventoryExplorer`, and `GridProxy`. [Verified — README]
- **Utilities package:** `LibreMetaverse.Utilities` is described as "Persistent login, keeping an avatar in a region, typed-out chat" — i.e. the library's own authors already maintain the keep-alive behaviours a long-lived agent needs. [Verified — README package table]

### 1.3 Voice — the finding that matters most for this project

The package family includes two voice add-ons:

- `LibreMetaverse.Voice.WebRTC` — "Voice chat on Second Life's WebRTC voice service."
- `LibreMetaverse.Voice.Vivox` — "Voice chat through the older Vivox service and its `SLVoice` daemon."

[Verified — README package table]

**[Inference]** This is strategically large. Second Life voice has been migrating from Vivox to a WebRTC-based service, and a headless agent's voice loop (STT in, TTS out) needs a client-side voice transport that does not require running the full graphical viewer. LibreMetaverse is the only open library found in this research that even attempts voice at the library level. The maturity of the WebRTC package is *not* established by the README — it must be tested early (see Risks).

### 1.4 Who uses it

- **Radegast / Radegast-ng** (see §2.2) is written on LibreMetaverse — the project's own site says so. [Verified]
- **MEGAbolt**, a recent fork of the abandoned METAbolt text viewer, "utilizes the LibreMetaverse library and now shares much of the same code as Radegast." [Verified — SourceForge project description]
- The OpenSim ecosystem generally builds on this lineage (the library explicitly targets OpenSimulator and Halcyon as well as Second Life), which matters if the project ever wants a private OpenSim test grid. [Verified — README/DeepWiki]

### 1.5 Weaknesses

- **Bus factor.** The ecosystem's centre of gravity is one highly prolific maintainer (Cinder Roxley maintains both LibreMetaverse and Radegast). Five current contributors soften this but do not remove it. Mitigation exists: BSD-3-Clause means the project can vendor/fork the code lawfully if upstream ever stalls. [Verified licence; Inference on risk]
- **Documentation is uneven.** The README and examples are current, and DeepWiki's generated docs are helpful, but much practical knowledge still lives in the old OpenMetaverse-era wiki material, which describes a library two forks old. Expect to read source. [Inference]
- **.NET is mandatory.** There is no way around a .NET process at the transport layer with this stack (see §2.4 for why Python can't replace it).

---

## 2. Other client libraries, text clients, and bot frameworks

### 2.1 Corrade (Wizardry and Steamworks) — the mature bot product

**What it is.** "Corrade is a multi-purpose, multi-platform scripted agent (bot) that runs under Windows or Unix natively, as a service or daemon whilst staying connected to a Linden-based grid (either Second Life or OpenSim) and controlled entirely by scripts." Its authors describe it as "a bridge, that gives access to viewer-commands to LSL scripts," reaching beyond LSL into IoT, big data, and AI. It has "almost two decades of experience and history in SecondLife and OpenSim," with "nearly all of the SecondLife API being implemented and offered through the API"; releases are now infrequent *because coverage is nearly complete*, not because the project is abandoned — its documentation wiki was still being updated in October 2025. [Verified — grimore.org Corrade page and API pages]

**Platform.** Linux 64-bit, Linux ARM64 (Raspberry Pi 2+), Windows 10+, macOS 10.12+; 100–350 MB RAM with all options on; on Linux it needs the current .NET runtime (SDK not required). The vendor now calls Docker the "canonical" way to run it, but the native binary + .NET runtime route is documented on the same page. (For this project, whose house rule is *no Docker*, the native route is the relevant one.) [Verified — grimore.org]

**API model — why people build on it.** Corrade is configured in "groups," each with its own password and a fine-grained permission set (e.g. `talk`, `economy`, `movement`, `grooming`, `land`, `bridge`). Controllers send it **key-value-encoded commands** and receive structured replies, through several channels:

- **In-world LSL** — scripts `llInstantMessage` commands to the bot and receive results the same way; an enormous catalogue of free LSL template projects (greeters, rental systems, group-notice schedulers, region scanners) demonstrates the pattern.
- **HTTP server** — external programs POST commands; Corrade can also make outbound HTTP requests itself (an `HTTP` bridge command) and speak **MQTT** (publish/subscribe), which is a genuinely modern integration surface for an external agent process.
- **TCP and UDP servers** — raw socket command channels; the UDP tutorial shows notification re-binding by sending a simple `group=…&password=<REDACTED_SECRET> string.
- **Notifications** — instead of polling, a controller subscribes to event types (local chat, group chat, IMs, friendship changes, alerts, membership, …) and Corrade pushes them to a URL — including an in-world `llRequestURL` endpoint, so an LSL object can receive group chat in real time.

[Verified — grimore.org commands list, notifications tutorial, UDP server tutorial]

**AI-era touch.** The vendor now publishes an "AI-model training" JSON file meant to be uploaded to an LLM so the model can generate Corrade scripts and commands from the API schema — a sign the maintainers see LLM-driven use as a first-class scenario in 2026. [Verified — grimore.org Corrade page]

**The catch — licence and openness.** Corrade is distributed under the custom **"Wizardry and Steamworks Project-Closed and Open-Derivatives License 1.0"**: use, commercial use, copying and redistribution are allowed *with attribution*, but **reverse-engineering of distributed binaries is prohibited**, and the licence text is a bespoke document, not an OSI-approved licence. In practice: Corrade is a closed binary you drive from outside. You cannot fix its bugs, audit its protocol code, or fork it. Your agent's body would live inside someone else's black box. [Verified — grimore.org licence summary on the Corrade page]

**[Inference]** Corrade is the fastest possible route to a *demo* (a weekend could plausibly produce a chatting, teleporting, group-managing bot driven by an LLM over its HTTP/MQTT surface), and its command catalogue is the best existing checklist of "what a Second Life agent should be able to do." It is a poor *foundation* for a sovereign project: closed binaries, custom licence, single small vendor, and a control model designed around LSL scripts and group passwords rather than a modern agent process.

### 2.2 Radegast and RadegastVeles — text clients

- Radegast is a lightweight client for Second Life and OpenSimulator, created in 2009 by Latif Khalifa to make the world usable without 3D; it "has become a vital tool for those with disabilities" and is the standard screen-reader route into SL. It offers full communications (local chat, IM, group messaging, friends conferences), inventory manipulation, a world map, an object finder, a region avatar list ("radar"), movement controls, gesture activation, and correct avatar appearance — a text client user "will not be able to tell" apart from a 3D user in-world. [Verified — radegast.life, SL Wiki downloads page]
- It is **written on LibreMetaverse** and is maintained by the same lead developer. The current repository (`github.com/cinderblocks/Radegast`, LGPL-3.0, ~3,190 commits, active) builds **two** clients: the original Windows WinForms "Legacy" client (.NET Framework 4.8/Mono), and **RadegastVeles**, a cross-platform Avalonia rewrite for Windows/macOS/Linux requiring the .NET 10 runtime. The repo includes a `Radegast.Veles.PluginApi` project and a plugin developer guide — i.e. the new client is extensible by design. [Verified — GitHub repo]
- Historical colour that is actually relevant: Radegast long shipped a built-in "A.L.I.C.E AI chat" auto-responder — primitive chatbot-in-client experiments are as old as the text-client tradition itself. [Verified — SL Wiki]

**Role for this project [Inference]:** not a framework to build the agent on, but (a) the best human-supervised observation/debugging client for watching what the agent's avatar is doing, and (b) a behavioural reference — its source shows how a real application drives LibreMetaverse managers for chat, radar, objects, and movement. The Veles plugin API is worth one exploratory spike if a future design wants a *supervised* presence (human watching, AI acting) in a single client.

### 2.3 METAbolt → MEGAbolt — the other text-client lineage

METAbolt was a popular Windows text viewer built on libOpenMetaverse, abandoned by its author "almost eight years" before a recent revival: **MEGAbolt**, a fork brought up to date on .NET 5, Windows x64 only, now sharing code with Radegast via LibreMetaverse. [Verified — SourceForge project description] **Verdict:** a lineage note, not a candidate — Windows-only, single-fork revival, and functionally a subset of what RadegastVeles now covers cross-platform.

### 2.4 Python — the honest dead end (and what to do about it)

- **PyOGP** was Linden Lab's own Python client-library effort, begun in 2008 as a test harness for the Open Grid Protocol (OGP). It comprised `pyogp.lib.base` (UDP/event-queue networking, capabilities, datatypes), `pyogp.lib.client` (higher-level conveniences — e.g. an `Agent().say(msg)` that sends `ChatFromViewer`), and sample apps, under the **Apache License 2.0**. [Verified — SL Wiki, PyOGP Client Library]
- When OGP development waned, so did PyOGP. The SL Wiki's project page records the official Bitbucket home being **lost when Bitbucket dropped Mercurial**; surviving copies exist only because community members archived them (a Bitbucket mirror and a Software Heritage capture mirrored to a `SL-PyOGP` GitHub org). The wiki pages themselves were last substantively edited in 2017–2022 and describe a 2008 codebase. [Verified — SL Wiki, PyOGP]
- **No maintained modern Python Second Life client library surfaced in this research** — no PyPI project, GitHub project, or community reference suggesting a living Python implementation of the current login + UDP/caps protocol. That is a statement about what this research found, not a proof of non-existence; but after the PyOGP trail, the burden of evidence is on any claimed alternative. [Verified as a research result]

**Consequence [Inference]:** if the Hermes/plugin side of Heimdall is Python (Hermes tooling commonly is), the architecture must be **two-process**: a .NET transport service (LibreMetaverse) owning the grid connection, and the Python agent talking to it over a local socket/HTTP/gRPC-style boundary — or the whole agent host written in C#. Designing the bridge protocol well matters more than the language on either side of it. (Corrade's HTTP/MQTT surface is the existing proof that this two-process pattern works fine in practice.)

### 2.5 Everything else surveyed

- **libOpenMetaverse (original)** — dead (see §1.1). Do not start here; LibreMetaverse is its continuation.
- **OpenSimulator** — a *server*, not a client library; relevant only as a private test grid the chosen stack already supports. [Inference]
- **Go / Rust / JavaScript client libraries** — none with meaningful maintenance or community footprint surfaced in this research. The Second Life protocol's open ecosystem is, in practice, a .NET monoculture in 2026. [Verified as a research result]
- **Commercial AI-companion products** — e.g. **Polymorph Awake** (launched September 2026) advertises an autonomous SL avatar companion that "can talk, remember, interact with people, move through the world, change outfits, follow its owner, use furniture, respond to its surroundings," and forum discussion references **Convai**-driven character bots. Their stacks are not disclosed. Hobbyist write-ups (e.g. the "Mystery" companion built by a 75-year-old with ChatGPT's help, with memory, vision of surroundings, and 20 triggerable animations/gestures) show the same pattern assembled by hand: an LLM, a bridge, and a bot-capable client. These are existence proofs for the *goal*, not building materials. [Verified — Seraphim/Polymorph page; SL community forums; Medium account — stacks undisclosed]

---

## 3. The official Second Life viewer source — reference, not foundation

- The official client source lives at `github.com/secondlife/viewer` (default branch `develop`), open source **since 2007**, under **LGPL-2.1** (GitHub metadata). It is very large and very active: ~54,700 commits and 820 releases in the current repository. [Verified — GitHub repo]
- **What it is good for, for this project:**
  - *Ground truth* for protocol behaviour: login flow and its failure modes, capability (caps) semantics, message formats, event-queue behaviour, region handoff/teleport edge cases — the things a library may lag on after a grid change.
  - *The voice migration*: how the official client moved from Vivox to WebRTC voice — the single best reference if Heimdall's voice loop hits undocumented behaviour in the library's WebRTC package. [Inference]
  - *Policy-adjacent artefacts*: the Third Party Viewer (TPV) programme and directory exist because LL regulates viewer-derived software; anyone distributing a client-like tool should read the TPV policy before publishing anything. [Verified that the TPV directory exists — viewer README; policy details not re-verified here]
- **What it is not good for:** building a headless agent. The codebase is a C++ 3D rendering client with its own build system (`autobuild`), platform toolchains per OS, and a UI/rendering centre of gravity; extracting "just the protocol" from it would be a project in itself, and LGPL-2.1 copyleft obligations would attach to anything derived. LibreMetaverse already *is* that extraction, maintained, in a friendlier licence. [Inference grounded in the verified size/licence/build facts]

---

## 4. LSL-side options — the in-world companion object

An external agent sees the world through the client protocol. An **in-world scripted object** (worn as an attachment/HUD by the avatar, or rezzed at her home) sees a different, complementary slice — and can act while the external client is busy thinking. LSL cannot run the agent (64 KB-class script memory, no general compute, no sockets beyond HTTP), but as a *peripheral* it earns its place.

### 4.1 What an LSL companion object can do

- **Hear and speak locally:** listen on chat channels (including private negative channels agreed with the external agent), speak with `llSay`/`llWhisper`/`llShout`, send IMs with `llInstantMessage`, and receive object-to-object messages region-wide. This is the classic way objects relay between the world and anything else. [Verified — SL Wiki LSL documentation generally; communications overview]
- **Sense presence:** sensors and agent-data requests let an object notice who is nearby, arrived, or left — the raw material for "someone entered the room" events. (Specific function names are standard LSL; treat the wiki as the reference when implementing.) [Inference from LSL's documented sensor/dataserver model]
- **HTTP out (`llHTTPRequest`):** the object can POST events to the external agent server (chat heard, touches, payments, visitors). Hard limits, from the official wiki: throttled to **25 requests per 20 seconds per object** and **1,000 per 20 seconds per owner**, with an additional throttle after repeated HTTP 5xx errors; response bodies are small by default (2,048 bytes is the long-standing default body cap; a `HTTP_BODY_MAXLENGTH` parameter exists to raise it within limits). Design consequence: batch events; never stream raw chat line-by-line in a busy room. [Verified — SL Wiki, llHTTPRequest]
- **HTTP in (`llRequestURL` / `llRequestSecureURL`):** the object can hold a temporary public URL and *receive* commands from the external agent ("say this," "play this gesture," "give this notecard"). URLs are **lost on region restart, region crossing, or object re-rez** — production systems re-register the fresh URL with the external server on every `changed` event. URL capacity is also parcel-prim-equivalent-limited. [Verified — SL Wiki HTTP category; vectree LSL summary]
- **Experience Tools:** with an experience key, scripts get (a) *persistent permissions* — an avatar grants once (e.g. attach, animate, teleport) instead of per-session dialogs; and (b) a **persistent key-value store**: keys ≤ 1,011 bytes, values ≤ 4,095 bytes, **128 MiB total** per experience, accessed asynchronously via dataserver events (`llCreateKeyValue`, `llReadKeyValue`, `llUpdateKeyValue`, `llDeleteKeyValue`, `llKeysKeyValue`). [Verified — SL Wiki, Experience Tools category]
  - *Uses for Heimdall [Inference]:* the companion object's own small durable state (the current HTTP-in URL registry, greeting cooldowns, per-resident "met before" flags) can live in KVP; and experience permissions are the clean way to let the object animate/attach things to consenting visitors — including, potentially, the agent's own avatar for furniture use and dances.
- **What LSL can no longer / cannot do:** XML-RPC was retired (reported ended 2023); LSL email exists but is heavily throttled; neither is a serious transport. LSL also has no access to the avatar's *client* state (inventory beyond the object's own, IM history, voice). Anything needing those must go through the client library or Corrade. [Verified — secondary LSL guide for XML-RPC; Inference for the rest]

### 4.2 The Corrade↔LSL pattern, generalised

Corrade's entire ecosystem proves the companion-object pattern at scale: thousands of LSL products already treat "an external program reachable by message/HTTP" as a normal peripheral. Heimdall can adopt the *pattern* without adopting Corrade: the companion object speaks to Heimdall's own .NET bridge over HTTP-in/out exactly as LSL objects speak to Corrade today. [Inference grounded in §2.1's verified ecosystem]

---

## 5. The policy frame every framework choice must live inside

(Short version — a full policy study is a separate research document. These facts constrain framework *usage*, e.g. how chatty a design may be.)

- Bots are **allowed and have legitimate uses**, but an account "primarily operated by a Scripted Agent" **must be identified as one on Linden Lab's Scripted Agent Status page**. [Verified — LL bot policy as reproduced by ZoHa Islands]
- **Messaging cap:** bots may not send more than **5,000 messages in a calendar day**, aggregated across *all* bots operated by one user (group messages count per recipient). Traffic-gaming (inflating parcel traffic/search), bot purchases of Mainland land, unreasonable region/search load, and loitering as a nuisance at welcome areas are all named violations. [Verified — same source]
- **Estate owners can disallow scripted agents** from their regions (a feature LL shipped and enabled, e.g., across Bellisseria Linden Homes) — so a registered bot can be legitimately excluded from places a human-driven avatar could go. [Verified — SL Newser report]
- **The policy is moving:** reporting of Linden Lab's 2025–2026 Trust & Safety meetings describes ongoing work to update bot policy, better identify scripted agents, and publish guidance on "registering" bot accounts. Design for the stricter future, not the looser past: register the account, keep message volume sane, respect exclusions. [Verified — Inara Pey's meeting reports, modemworld.me]

---

## 6. Comparison table

| Option | Language / runtime | Licence | Maintained (Oct 2026) | Full client (login→act) | Voice path | Control / API model | Open source? | Fit for Heimdall transport |
|---|---|---|---|---|---|---|---|---|
| **LibreMetaverse** | C# / .NET 8–10, cross-platform | BSD-3-Clause | **Yes** — commits within days; 3.x line; 5 contributors | Yes — login, chat, IM, movement, objects, inventory, groups, land, appearance | **Yes** — `Voice.WebRTC` + `Voice.Vivox` packages | Library API (`GridClient` + managers, events) | Yes | **Primary recommendation** |
| libOpenMetaverse (original) | C# / .NET Framework era | BSD-style (historical) | **No** — no commits in 8+ years | Yes (stale vs current grid) | No (predates WebRTC voice) | Library API | Yes | Dead road — superseded by its own fork |
| **Corrade** | .NET binary (Win/Linux/macOS, incl. ARM) | Custom "WAS PC & OD 1.0" — closed derivatives, no reverse-engineering | Yes — mature, infrequent releases; docs updated 2025 | Yes — near-complete viewer command set | Not exposed as an agent voice loop in its docs (unverified either way) | Key-value commands over LSL IM, HTTP, TCP, UDP, MQTT; push notifications to URLs; per-group passwords/permissions | **No** — closed binaries | Excellent reference & prototyping accelerator; wrong foundation (black box) |
| **Radegast / RadegastVeles** | C# / .NET (LibreMetaverse-based) | LGPL-3.0 | Yes — Veles rewrite on .NET 10 + plugin API | Yes — human-oriented client | Via client features, not an agent API (unverified) | GUI + Veles plugin API | Yes | Observation/debug client; possible supervised-presence spike |
| METAbolt → MEGAbolt | C# / .NET 5, Windows-only | (fork of an abandoned viewer) | Marginal — single revival fork | Yes (text client) | No evidence found | GUI | Yes | Skip — subset of Radegast, Windows-only |
| **PyOGP** (Python) | Python (2008-era) | Apache-2.0 | **No** — dead since OGP waned; repos survive only as archives | Partial even in its day (test-harness scope) | No | Library API | Yes | Dead road — read for history only |
| **Official SL viewer source** | C++ (autobuild) | LGPL-2.1 | Yes — Linden Lab, very active | Yes — the reference client | Yes (viewer-grade, incl. WebRTC migration) | N/A (a client, not a library) | Yes | Reference/ground truth only — not a headless foundation |
| **LSL companion object** | LSL (in-world) | n/a | n/a (platform feature) | No — no login; rides on the avatar | No | Chat channels, HTTP-in/out (throttled), Experience KVP | n/a | **Complement** — in-world senses & hands |
| Commercial AI companions (Polymorph Awake, Convai bots) | Undisclosed | Commercial | Yes (products) | Yes (as products) | Undisclosed | Product UI | No | Existence proof only |

---

## 7. Recommendation

### 7.1 Build the transport layer on LibreMetaverse

**Why this one, in order of weight:**

1. **It is the only living open implementation of the full protocol.** Login, presence, chat/IM, movement, object and avatar updates, inventory, groups, land — one maintained codebase, current with the grid (its maintainers run the most-used text client on it, so grid changes break *their* daily driver first and get fixed). [Verified basis: §§1.1–1.4]
2. **The licence fits a sovereign project.** BSD-3-Clause: use it, vendor it, fork it if upstream dies, ship Heimdall under the project's own GPLv2-family terms without copyleft conflict at the library boundary. Compare Corrade's custom closed licence and the viewer's LGPL-2.1. [Verified licences throughout]
3. **Voice is in the building.** The `Voice.WebRTC` package is the only open library-level start on SL's current voice service found in this research. Heimdall's real-time voice loop is a headline goal; starting anywhere else means reverse-engineering voice transport alone. Its maturity is unproven — so it becomes the *first spike*, not an assumption (§7.3). [Verified existence; maturity unknown]
4. **The two-process shape is natural.** `GridClient` + event handlers map cleanly onto "perception events out, action commands in." The Hermes-facing side (Python or otherwise) talks to a small .NET bridge service over localhost HTTP/WebSocket/MQTT-style messaging — the same topology Corrade's ecosystem has validated for a decade, but with every layer owned. [Inference]
5. **OpenSim comes free.** The same library targets OpenSimulator, giving the project a private, policy-free test grid for destructive or embarrassing experiments before touching the real grid. [Verified target support]

### 7.2 What to take from the others (without depending on them)

- **From Corrade:** its command catalogue as the *capability checklist* for Heimdall's action set (it represents ~two decades of "what do people actually ask a bot to do"), its notifications taxonomy as the model for the perception-event set, and its group/password/permission split as a design reference for scoping what the AI may do. Optionally, run Corrade during early prototyping to validate behaviours before the LibreMetaverse bridge covers them — behind the same internal bridge interface, so it can be dropped without surgery. **[Inference/design advice]**
- **From RadegastVeles:** the supervised-operations client (watch the avatar, intervene manually), and its source as worked examples of driving LibreMetaverse. **[Inference]**
- **From the official viewer:** protocol ground truth when the library and the grid disagree; the WebRTC voice migration as reference. Read-only. **[Inference]**
- **From LSL:** one companion object (worn attachment) providing local-presence sensing, touch/visitor events, HTTP-in command receipt with URL re-registration, and Experience-KVP for its small durable state — feeding the same event bus as the client library. **[Inference grounded in §4]**

### 7.3 Risks and the order to burn them down

1. **WebRTC voice maturity in LibreMetaverse — unknown.** Spike first: join a voice-enabled region, capture and inject audio, measure. Fallback if immature: run voice through a supervised full viewer during early life, or defer voice while text presence ships. (Do not let the voice goal silently become the critical path for *presence*.)
2. **Single-maintainer gravity.** LibreMetaverse and Radegast share one lead. Mitigation: pin a known-good version, vendor the source in the project's own tree from day one (BSD allows it), and treat upstream as a contribution source, not a runtime dependency of fate.
3. **Login/grid drift.** Linden Lab changes login and caps behaviour periodically (Corrade's own release history includes emergency fixes for LLSD login-parsing changes). Budget a small "grid change" maintenance habit: follow the library's releases and the SL grid status/blog.
4. **Policy tightening.** Bot policy was under active review in 2025–2026. Register the avatar's account as a Scripted Agent from day one, design message rates far below the 5,000/day cap, and honour scripted-agent exclusions — a companion who mostly talks to her own household generates trivial volume compared to the data-scraper bots the policy exists to stop.
5. **No-Docker note.** Corrade's vendor now calls Docker its canonical install; if Corrade is used at all in this household, use the documented native binary + .NET runtime route. LibreMetaverse itself has no container requirement — it is a library, built with the plain .NET SDK.

### 7.4 Dead roads, stated plainly

- **libOpenMetaverse (original):** abandoned 8+ years; its fork is the road.
- **PyOGP / any "Python-native SL client":** does not exist in maintained form; do not plan around resurrecting 2008 Python — the protocol has moved too far (caps, modern login, WebRTC voice) for the archive to be more than a history lesson.
- **METAbolt lineage:** superseded by RadegastVeles.
- **Building on the official viewer:** LGPL C++ rendering client — wrong shape, wrong licence gravity, enormous build; mine it, don't marry it.
- **LSL-only "agents":** an in-world object can be charming, but it cannot log in, travel freely, hold IM conversations as the avatar, or run a mind. It is a peripheral, never the agent.
- **Closed commercial companions:** fine products, wrong genus — Heimdall exists precisely to *be* the stack, not to rent one.

---

## Sources

1. LibreMetaverse repository (README, package table, licence, requirements) — https://github.com/cinderblocks/libremetaverse
2. LibreMetaverse on NuGet (per-package versions) — https://www.nuget.org/packages/LibreMetaverse.Types/2.4.18.1184
3. LibreMetaverse architecture overview (GridClient/managers) — https://deepwiki.com/cinderblocks/libremetaverse
4. Open Hub project activity analysis (LibreMetaverse, libopenmetaverse, Radegast) — https://openhub.net/tags?names=secondlife
5. Radegast project site (features, LibreMetaverse basis, accessibility role) — https://radegast.life/
6. Radegast repository (LGPL-3.0, RadegastVeles rewrite, plugin API, .NET 10) — https://github.com/cinderblocks/Radegast
7. MEGAbolt fork description (METAbolt lineage, LibreMetaverse basis, Windows/.NET 5) — https://storage.googleapis.com/free-download-soft/radegast.mirror.html
8. Corrade main page (purpose, platforms, .NET runtime, licence summary, AI training JSON) — https://grimore.org/secondlife/scripted_agents/corrade
9. Corrade complete command list (HTTP/MQTT bridge commands, permission groups) — http://grimore.org/secondlife/scripted_agents/corrade/api/commands
10. Corrade notifications tutorial (push notifications to llRequestURL endpoints) — https://grimore.org/secondlife/scripted_agents/corrade/tutorials/notifications?do=export_pdf
11. Corrade UDP server tutorial (socket command/notification binding) — https://grimore.org/secondlife/scripted_agents/corrade/tutorials/udp_server?do=export_pdf
12. Official Second Life viewer repository (LGPL-2.1, scale, TPV directory) — https://github.com/secondlife/viewer
13. LSL llHTTPRequest (throttles: 25/20 s per object, 1,000/20 s per owner) — https://wiki.secondlife.com/w/index.php?title=LlHTTPRequest&diff=cur&oldid=1182306
14. LSL HTTP category (llRequestURL/llRequestSecureURL, http_request/http_response) — https://wiki.Secondlife.com/w/index.php?title=Category%3ALSL_HTTP&oldid=1164288
15. Experience Tools (persistent permissions; KVP limits: key ≤1011 B, value ≤4095 B, total 128 MiB) — https://wiki.secondlife.com/w/index.php?title=Category:Experience_Tools&diff=cur&oldid=1192031
16. PyOGP Client Library (history, packages, Apache-2.0) — https://wiki.secondlife.com/w/index.php?title=PyOGP_Client_Library&diff=cur&oldid=77257
17. PyOGP project page (repo loss, community archives) — https://wiki.secondlife.com/w/index.php?title=PyOGP&diff=cur&oldid=78183
18. Linden Lab bot policy summary (Scripted Agent status, 5,000 messages/day, traffic/land rules) — https://ZoHa-Islands.com/blog/the-bots-the-whole-bots-and-nothing-but-the-bots/
19. Scripted-agent estate exclusions (Bellisseria) — https://slnewser.blogspot.com/2023/03/
20. Ongoing LL bot-policy review (Trust & Safety meeting reports) — http://modemworld.me/category/second-life/
21. Polymorph Awake (commercial AI companion capabilities) — https://www.seraphimsl.com/2026/09/28/polymorph-awake-create-connect-experience-together/
22. "Mystery" hobbyist AI companion account (custom bridge, memory, gestures) — https://medium.com/@3aisandahuman/im-75-i-built-an-ai-companion-things-got-a-little-out-of-hand-038148fd97d9

*Uncertainty register: (a) the maturity/completeness of LibreMetaverse's WebRTC voice package is unverified — README-level existence only; (b) whether Corrade can act as a voice endpoint for an external agent is not documented in the material fetched; (c) "no living Python/Go/Rust/JS client library" is a negative research result, stated as found, not as proof; (d) RadegastVeles voice behaviour as an agent surface is unverified. Each is flagged where it appears in the text.*
