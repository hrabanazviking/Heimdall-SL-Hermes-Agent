# Second Life Protocols and Connection Methods

**Project:** Heimdall-SL-Hermes-Agent (Second Life agent plugin for the Hermes AI Agent)
**Document:** 01 — SL protocols and connection methods
**Researched:** 2026-10-26
**Scope:** How a program connects to Second Life in 2026, how a session lives and dies, the Linden Lab rules that bind an automated agent, how voice works after the WebRTC transition, and the practical connection architectures available to a headless agent.

**How to read this document.** Statements are marked where it matters:

- **[verified]** — directly supported by a cited source (Linden Lab wiki/official pages preferred).
- **[inference]** — the author's engineering judgement built on verified facts; treat as opinion.
- **[uncertain]** — plausible but not confirmed against a current primary source during this research pass; flagged for later verification.

The Second Life Wiki's protocol documentation is old in places (much of it written 2007–2013, lightly edited since), but the underlying wire protocol changes slowly, and the wiki remains the reference that third-party client authors (LibreMetaverse, Sharpview) work against. Where a wiki page is ancient, that is noted.

---

## Overview

Second Life is not one protocol; it is three generations of protocol running at once:

1. **A binary UDP message system** (the original 2003-era design) that still carries the real-time core: avatar movement, object updates, chat, animation, and the per-region "circuit" between client and simulator.
2. **An HTTP "capabilities" (caps) layer** built on LLSD (Linden Lab Structured Data), layered over the UDP core since ~2007 and steadily absorbing services ever since: the login response hands the client a *seed capability*, from which it requests per-service URLs, including the long-poll **EventQueueGet** channel that pushes server-side events (teleports finishing, region crossings, chat session control, voice info) to the client.
3. **Plain HTTP asset/inventory services** (textures, mesh, inventory fetches) that moved bulk data off UDP entirely.

An agent connects the same way a viewer does. There is no separate "bot API" from Linden Lab: per the Third-Party Viewer Policy, "any software client … that logs into our servers" — explicitly including "chat clients, utilities, bots, and proxies" — is a Third-Party Viewer and is governed as one [TPVP-FAQ]. The practical consequence for Heimdall: we either implement (or reuse) a viewer-grade protocol client, or we avoid the protocol entirely by bridging through an in-world LSL object — or we combine both.

Voice is the one subsystem in active upheaval: Linden Lab announced the expansion of WebRTC voice to the full grid on **May 5, 2026**, retiring the Vivox system viewers have used since 2007 [LL-Voice-Update][ModemWorld-WebRTC]. Any voice design done today must target WebRTC, not Vivox.

---

## 1. The protocol stack as it exists in 2026

### 1.1 The UDP message system [verified]

The original and still-load-bearing layer is a custom binary protocol over UDP, documented on the Second Life Wiki's Protocol / Message System pages:

- Messages are defined in a **message template** (`message_template.msg` in the viewer source). Each message has a fixed name, a frequency class (High / Medium / Low / Fixed), a trust flag (Trusted / NotTrusted), and an encoding flag (Encoded / Unencoded / Zerocoded). Example from the wiki, the first packet of every session:

  ```
  UseCircuitCode Low 3 NotTrusted Unencoded
      CircuitCode { Code U32 } { SessionID LLUUID } { ID LLUUID }
  ```

- **Packet layout**: a 6-byte header (flags byte, 4-byte big-endian sequence number, extra-byte count), optionally appended "extra" header data, the message body, and — for reliable packets — appended ACKs for earlier packets ("packet accounting": sequencing, reliability via explicit ACK + resend, duplicate suppression, and throttling are all handled in this layer, since UDP itself provides none of it).
- Messages marked **reliable** must be acknowledged; unacknowledged reliable packets are resent. Many messages are zerocoded (a run-length compression of zero bytes) to save bandwidth.
- The client tells the simulator how much bandwidth it wants per data class with **AgentThrottle**, a packed set of little-endian floats with documented maxima: Resend 150,000 B/s; Land 170,000; Wind 34,000; Cloud 34,000; Task 446,000; Texture 446,000; Asset 220,000 — and each value "should not be smaller than 10,000" [AgentThrottle].

What still runs over UDP in 2026 [verified, from the message-system and login-sequence documentation]: circuit setup, region handshake, AgentUpdate (position/camera/control state — the heartbeat of presence), ObjectUpdate streams, chat (ChatFromViewer / ChatFromSimulator), IMs (ImprovedInstantMessage), animation control, teleport requests, ping checks. In other words: everything latency-sensitive and everything the simulator must arbitrate in real time.

### 1.2 LLSD and capabilities (caps) [verified]

The second layer is HTTP(S) with payloads in **LLSD** — Linden Lab Structured Data, a typed data model (maps, arrays, UUIDs, binary blobs) serialised as XML (`application/llsd+xml`) or a binary/JSON-ish notation depending on endpoint.

**Capabilities** are the access-control and routing mechanism of this layer:

- A capability is "a unique URL" that temporarily grants one ability; "clients can either post new events to CAPS URLs or wait for events to come from the server on the event queue connection the client holds open" [Protocol].
- The login response gives the client one **seed capability** for the initial simulator. POSTing a list of wanted capability names to the seed returns a map of name → per-service URL (e.g. `EventQueueGet`, asset upload, inventory services). The wiki warns: "Do not rely on the form of a capability, other than it must be http: or https:. Capabilities from the same source needn't point to the same host or port, and the path needn't start /cap" [Capabilities].
- Caps URLs are unguessable bearer tokens in effect: possession of the URL is the authorisation. A client must treat them as session secrets and must re-request them whenever it is handed a new seed (login, teleport, region crossing).

### 1.3 The event queue [verified]

**EventQueueGet** is a capability used as a server→client push channel via HTTP long-polling:

- The client POSTs `{ ack: <last event id>, done: false }` to the region's EventQueueGet cap and holds the request open. The simulator holds the connection until it has events, then returns `{ events: [...], id: <n> }`; if there is nothing to send it eventually answers with a deliberately odd `HTTP/1.0 502 Upstream error:` response — this 502 is *normal* and means "poll again" [EventQueueGet].
- Events delivered this way include (from the wiki's event catalogue): **EnableSimulator** (a neighbour region exists — connect a child circuit to it), **CrossedRegion** (you have crossed into a neighbouring sim; body includes the new region's seed capability, sim IP/port, position, look-at), **TeleportFinish**, **EstablishAgentCommunication** (chat-session plumbing), **ParcelVoiceInfo** and **RequiredVoiceVersion** (voice), plus group/session and display-name updates [EventQueueGet-CAP].
- Losing one of these events is not cosmetic. As third-party viewer author Animats (Sharpview) puts it in a Linden forum thread: "Loss of a **TeleportFinish** means a failed teleport. Loss of a **CrossedRegion** means a failed region crossing. Loss of an **EnableSimulator** means a missing region… the viewer is stuck in an out-of-sync condition that is hard to recover" [Animats-EQ]. **[inference]** A bot client must therefore treat the event queue as *essential, ordered, and acknowledged transport*, not as best-effort notifications — exactly the discipline the `ack` field exists for.

### 1.4 Assets and inventory over HTTP [verified in outline]

Bulk data long ago left the UDP circuit: textures and mesh are fetched over HTTP (via caps such as GetTexture / GetMesh2 and CDN-backed hosts), and inventory operations have progressively moved to HTTP inventory services (the AIS — Agent Inventory Service — family of caps) rather than UDP messages. The legacy UDP transfer managers (XferManager / TransferManager in the message template) survive mostly for legacy paths. **[uncertain]** The exact current inventory-cap surface (AISv3 endpoint names and semantics) was not re-verified line-by-line in this pass; LibreMetaverse's source is the most practical reference when we get there.

### 1.5 Legacy vs current — summary table

| Piece | Status in 2026 | Notes |
|---|---|---|
| UDP message system (circuits, ObjectUpdate, AgentUpdate, chat, IM) | **Current, load-bearing** | No replacement exists or is announced. |
| Message template / packet accounting | Current | Effectively frozen; new features go to caps instead. |
| XML-RPC login | **Legacy but functional** | Official viewer moved to a web-based authentication flow; "non-official viewers may still use XML-RPC for login" [Protocol]. Every open-source bot library still uses it. |
| LLSD over HTTP caps | Current | The growth layer since 2007. |
| EventQueueGet long-poll | Current | Carries teleports, crossings, voice and chat-session events. |
| HTTP texture/mesh/inventory services | Current | Bulk data path. |
| UDP Xfer/Transfer managers | Legacy | Superseded by HTTP for most asset work. |
| Vivox voice (SLVoice daemon) | **Being decommissioned** | Grid-wide WebRTC cut-over announced for 2026-05-05 [LL-Voice-Update]. |
| WebRTC voice | **Current** | Per-region SL voice servers; no separate voice daemon in the viewer [WebRTC-Dev-View]. |
| OGP / "place_avatar" agent-domain login (PyOGP docs) | **Dead** | A 2008-era experiment documented on the wiki; never became the production login path. Do not design against it. |

---

## 2. Login and session lifecycle

### 2.1 Authentication and the login response [verified]

The production login used by third-party clients is a single XML-RPC call to the grid's login URI (the `-loginuri` parameter; `https://login.agni.lindenlab.com/cgi-bin/login.cgi` for the main grid, Agni). The wiki's "Current login protocols" page reduces it to: viewer → login server (XML-RPC) → database → target simulator is notified → XML-RPC response to viewer [Current-Login].

The request carries first/last (or username), a `$1$`-prefixed MD5 of the password in the classic scheme, start location (`home`, `last`, or a region/position spec), viewer channel/version identifiers, and requested optional fields. The successful response is a large LLSD-style structure containing everything the session is built from [Login-Strawman][Login-Sequence]:

- `agent_id` — the avatar's UUID.
- `session_id` and `secure_session_id` — session credentials quoted back in UDP messages and caps calls.
- `circuit_code` — a U32 that, with the session IDs, authorises the UDP circuit (see §2.2).
- `sim_ip`, `sim_port` — the initial simulator's UDP endpoint.
- `seed_capability` — the HTTPS URL that unlocks the initial region's caps, including EventQueueGet.
- `region_x` / `region_y`, `look_at`, `sim_access` (region maturity), plus inventory skeleton, active gestures, buddy list, and other account state.

The login server also decides *where* you land: if the requested region (home/last) is unavailable, the avatar is re-routed, "usually to a 'safe hub'". The wiki's login-sequence notes carry a warning written for people like us: "Scripted agent controllers should check this, or their bots will pile up at safe hubs" [Login-Sequence] — the actual arrival region is in the RegionHandshake, not in what you asked for.

Two operational notes:

- **[uncertain]** Accounts with multi-factor authentication enabled add a verification step to this flow. The precise challenge mechanics for headless XML-RPC login were not verified against a primary source in this pass. Prudent default for a bot account: expect MFA to complicate or block unattended login, and verify against the live login service on the beta grid (Aditi) before designing around it.
- Channel/version strings are policy-visible, not just cosmetic: Linden Lab's channel/version requirements govern which viewer channels may connect [Protocol]. A bot should present an honest channel identity.

### 2.2 Establishing presence: the circuit [verified]

Login alone puts nobody in-world. Presence is a fixed handshake over UDP to the initial simulator [Current-Login][Login-Sequence]:

1. **UseCircuitCode** (UDP) — carries circuit code + session ID + agent ID. "It is the first and necessary message before any other circuit communication is possible." After it, "the simulator will begin sending periodic **StartPingCheck** messages" [UseCircuitCode].
2. **CompleteAgentMovement** (UDP) — the client declares it is ready to become an agent in the region.
3. **RegionHandshake** (sim → client) / **RegionHandshakeReply** (client → sim) — region identity, terrain, access flags. (The login-sequence diagram notes a deliberate ~2-second pause before replying reduces interest-list errors.)
4. **AgentUpdate** (UDP) — the first one necessarily contains a bogus position (the client cannot know where its avatar is until the first ObjectUpdate for the avatar arrives); sending it is what starts the ObjectUpdate flow. From here the client sends AgentUpdate continuously — it carries camera position, head/body rotation, movement control flags (the "keys held down" bitfield that actually walks/flys/turns the avatar), and far-clip.
5. **EventQueueGet** polling starts against the seed cap in parallel — the sim confirms movement completion (**AgentMovementComplete**) and events begin to flow.

### 2.3 Keepalive and presence maintenance [verified in outline]

- **Ping:** the simulator sends periodic StartPingCheck; the client answers each with **CompletePingCheck** quoting the ping ID. Missed pings are how the sim decides you are gone.
- **AgentUpdate cadence:** a present agent keeps sending AgentUpdate (viewers send at up to ~10/sec when moving, throttled when idle **[uncertain — exact cadence is a viewer implementation choice, not a protocol constant]**) and respects its AgentThrottle buckets.
- **Neighbour awareness:** as the avatar moves, the sim sends **EnableSimulator** events for neighbouring regions; the client opens *child-agent circuits* to them (UseCircuitCode + ChildAgentUpdate) so crossings are seamless and the neighbours can send updates for objects/avatars visible across the border.
- **Session death:** if the client vanishes (crash, network loss), the session lingers until the simulator times it out — Linden staff describe testers waiting "a minute for the first session to timeout and disconnect" before re-logging [Feedback-Timeout]. During that window a re-login can collide with the ghost session. **[inference]** A bot supervisor must implement crash → wait/back-off → re-login, and must not hammer the login server in a tight loop.

### 2.4 Teleport [verified]

A teleport is a request and an asynchronous hand-off, not an instant move:

1. Client asks its current sim to teleport (e.g. TeleportLocationRequest / TeleportLandmarkRequest over UDP, naming a region handle + position, or a landmark).
2. The destination sim prepares a child-agent presence for the avatar; the source sim receives confirmation.
3. The client learns the outcome via the event queue: **TeleportFinish** carries the destination's seed capability, sim IP/port and agent access flags. (In the OpenSimulator protocol write-up of the same mechanics: "The TeleportFinish message sent to the viewer triggers it to send a second UseCircuitCode UDP packet to the destination simulator… [and] a CompleteAgentMovement UDP packet… This triggers the destination simulator to upgrade the previously established child agent to a full root agent" [OpenSim-Teleports]. The Linden grid implements the same architecture; treat implementation minutiae as **[inference]** where the source is OpenSim's documentation.)
4. The client opens the new root circuit, fetches the new region's caps from the new seed, restarts EventQueueGet against the new region, and tears down the old circuit (the old sim demotes the agent to child, then sends **DisableSimulator** when the region leaves the neighbourhood).

Failure is a first-class outcome: TeleportFailed arrives instead, parcels can refuse entry (ban lines, landing-point rules, maturity/access mismatch, region full), and a bot must surface "teleport failed, still in region X" to its mind layer rather than assuming success.

### 2.5 Region crossings [verified]

Walking or flying across a border is the continuous version of the same hand-off: the client already holds child circuits to the neighbours (thanks to EnableSimulator), the **CrossedRegion** event delivers the new root region's seed cap/IP/port/position, the old root is demoted, and caps + event queue are re-anchored. Vehicle crossings and sitting agents add edge cases (the sat-upon object crosses too, with its own state) that a movement-capable bot must eventually handle; a stationary or teleport-only agent can defer them.

### 2.6 Logout [verified in outline]

A clean exit sends **LogoutRequest** on the circuit and closes the event queue (the final EventQueueGet poll is posted with `done: true` in the classic protocol). The client keeps polling its old region's event queue until told to stop — one wiki note observes the client "will keep polling the EventQueueGet cap until you give it a 404 even after you've left the region" [EventQueueGet], which is why disciplined shutdown matters. An unclean exit leaves the ghost-session problem of §2.3 and can drop state (unsaved appearance/last-location quirks are a long-standing complaint [Feedback-Timeout]). **[inference]** For an always-on agent, "logout" should be a deliberate, logged state transition — never just process death — and re-login should verify actual arrival region and presence before resuming behaviour.

---

## 3. The rules: what binds a project like this

### 3.1 The Scripted Agent Policy (the "bot policy") [verified — official text]

The governing document is **Linden Lab Official: Scripted Agent Policy** on the Second Life Wiki (current version published 2023-04-03). Its scope is broad:

> "Any script or program that automates access to Second Life and its supporting web services through interfaces designed for the Second Life Viewer and its embedded browser is considered a Scripted Agent." … "Second Life allows Scripted Agents with legitimate uses, but inappropriate uses are violations of this policy." [Scripted-Agent-Policy]

**Identification — mandatory:**

> "If you own a Second Life account primarily operating as a Scripted Agent, you must specify this on the Scripted Agent Status page." [Scripted-Agent-Policy]

Setting this status also removes the account from traffic counts. This is a hard pre-launch checklist item for Heimdall: **Yrsa's account must be flagged as a scripted agent before the agent logs in unattended.** (Note the policy says "primarily operating as" — an account that is sometimes human-driven and sometimes agent-driven is a judgement call; the conservative reading, and the one that matches the project's intent of an inhabited-by-AI avatar, is to set the flag. **[inference]**)

**Traffic — the original sin bots are policed for:**

> "**Using Scripted Agents to 'game' or boost traffic is prohibited.** You may not attempt to gain an unfair advantage in search results through the use of Scripted Agents to inflate the traffic for a parcel. This policy applies to both mainland and private estates… Please refer to the Second Life Terms and Conditions, section 5.i." [Scripted-Agent-Policy]

An agent that simply *exists* on a parcel accrues dwell minutes; an agent parked 24/7 on someone's land is structurally indistinguishable from a traffic bot unless it is (a) flagged as a scripted agent (excluded from traffic) and (b) actually doing something legitimate. Both mitigations are design requirements, not etiquette.

**Message volume — a hard numeric cap:**

> "**Sending an excessive number of messages with Scripted Agents is not allowed.** You may not use Scripted Agents to send more than **5,000 messages in a calendar day. All Scripted Agents operated by a single user share a common limit.**" [Scripted-Agent-Policy]

The policy counts chat messages, IMs, inventory offers, group notices, group IMs and invitations as messages, and counts group messages **per recipient**. Violations explicitly include "Scripted Agents causing unreasonable load on search or the regions in which they operate" and "causing a nuisance by regularly getting stuck at welcome areas or InfoHubs" [Scripted-Agent-Policy].

**Mainland purchases:**

> "**Using Scripted Agents to purchase Mainland parcels is prohibited**" — any automation that removes the human interaction required to buy Mainland land. [Scripted-Agent-Policy]

**Privacy — two clauses that matter enormously for a perceptive agent:**

> "Second Life provides a set of privacy controls, such as limiting who can communicate with a Resident, who can see another Resident's online status, who can discover their location within Second Life… **Scripted Agents may not circumvent these privacy controls.**" [Scripted-Agent-Policy]

> "Data transferred outside of the Second Life service… is not exempt from protection under applicable data privacy laws… These laws include Europe's **General Data Protection Regulation (GDPR)** and the **California Consumer Privacy Act (CCPA)**." The policy stresses that access to residents' Personal Data "is conditioned on adherence to all applicable global privacy regulations governing the collection, storage, processing, or transmission of Personal Data." [Scripted-Agent-Policy]

**[inference]** Heimdall's core loop — hear everything nearby, remember it, write it to long-term memory — is precisely the activity these clauses govern. Chat logging, avatar-name/location tracking, and conversation memory are Personal Data processing when they leave the grid (and our memory store is outside the grid). The design must include: honouring blocks/mutes and privacy settings, not defeating "show online status"/location privacy, a retention/consent stance for bystander chat, and care with publishing logs.

**Enforcement:**

> "Violations of this policy may result in suspension and/or termination of your account." Privacy violations add stronger medicine: Linden Lab may remove violating agents and scripts, demand explanations and changes to how automated access works, or suspend/terminate service [Scripted-Agent-Policy].

### 3.2 The Policy on Third-Party Viewers (TPVP) [verified]

Because a protocol-level agent *is* a Third-Party Viewer, the TPVP applies on top of the bot policy. Key clauses from Linden Lab's Third Party Viewers page:

- §4.a.iii: "**If your Third-Party Viewer is a bot, you must comply with our policy on bots**, including any updates to the policy." [TPVP]
- §4.a.i: the operator agrees "to update or delete at our request any data that you have received from Second Life" if Linden Lab believes it violates policy — i.e. **memory deletion on demand is a contractual possibility**; the memory layer should support targeted purge of a resident's data.
- §4.a.ii: "We may at any time for any reason limit your access to data or our servers and systems through Third-Party Viewers" — no uptime guarantees for bots; graceful degradation is a requirement.
- §4.b (for *distributed* viewers): a published privacy policy describing data collection is mandatory for developers who distribute TPVs. **[inference]** Heimdall as a public Hermes plugin plausibly counts as distribution once others can install it — ship a privacy policy with the plugin.

The TPVP FAQ's definition is worth quoting because it forecloses the "we're not a viewer, we're a plugin" argument: a third-party viewer is "any software client, regardless of its source code, that is not developed by Linden Lab and that logs into our servers. This includes software for viewing Second Life, any chat clients, utilities, bots, and proxies" [TPVP-FAQ].

### 3.3 Terms of Service points that intersect [verified by reference]

- The Scripted Agent Policy anchors the traffic rule in the **Second Life Terms and Conditions, section 5.i** [Scripted-Agent-Policy].
- General ToS provisions on automated access, abuse/griefing, and account responsibility apply to the account holder regardless of who — or what — is driving the avatar; the human owner remains accountable for the agent's conduct. **[inference — standard ToS structure; the full current ToS text was not re-read line-by-line in this pass.]**
- Parcel and estate rules are a second, private layer of law: landowners may ban bots (many parcels/estates set "scripted agents" access restrictions), and estate covenants can prohibit unattended agents entirely. **[inference]** The agent must treat ejection/ban/teleport-home events as normal control flow.

### 3.4 Rate limits and abuse vectors — consolidated

| Limit / risk | Value / behaviour | Source |
|---|---|---|
| Scripted-agent message cap | **5,000 messages/day, shared across all agents of one operator**; group messages count per recipient | [Scripted-Agent-Policy] |
| Region/search load | "Unreasonable load on search or the regions" is a named violation; stuck bots at welcome areas/InfoHubs are a named nuisance | [Scripted-Agent-Policy] |
| UDP bandwidth negotiation | AgentThrottle buckets with documented maxima (Task/Texture 446,000 B/s etc.); a client should ask for what it needs and no more | [AgentThrottle] |
| LSL HTTP-Out (llHTTPRequest) | **25 requests / 20 s per object; 1,000 / 20 s per owner**; 5 HTTP 5xx errors in 60 s throttles the script; throttled calls return NULL_KEY and keep the throttle alive while retried | [llHTTPRequest] |
| LSL HTTP-In (http_request) | Request body **truncated at 2,048 bytes**; headers limited to 255 bytes; max **64 pending** http_request events, excess discarded | [http_request] |
| HTTP-In URL supply | URLs are a scarce region resource pooled like prims by parcel; **attachments share a per-resident pool of max 38 URLs** | [LSL-HTTP-Server] |
| Offline IM caps / message queueing | SL caps stored offline IMs (commonly cited as 25 for basic accounts) — **[uncertain]**, verify against the Knowledge Base before relying on numbers | — |
| Login behaviour | Repeated failed/rapid logins risk lockouts and collide with ghost sessions (§2.3) | [Feedback-Timeout] **[inference]** |

Abuse vectors to design *against* (i.e., things our agent must never do, even accidentally): camping on parcels to farm traffic; reply-storms in busy local chat; group-join + mass-notice behaviour; circumventing blocks, online-status privacy or location privacy; scraping profiles/search at rates that load the services; automating Mainland land purchase; running many unflagged alts as a botnet (the 5,000/day cap is explicitly *aggregate per operator* to close that hole).

### 3.5 Other rule-adjacent facts

- Scripted-agent status is set per account on the Second Life website (Accounts → Scripted Agent Status) [Scripted-Agent-Policy].
- Abuse reports against bots are filed under "Disturbing the Peace" — the practical complaint channel residents will use if the agent misbehaves [Scripted-Agent-Policy].
- The 2009 blog post that created this policy regime (reproduced at [ZoHa-Bots]) framed the intent that still governs enforcement: bots "are fine and we totally support their good use inworld, but we will deal with inappropriate use of them," and "responsible use of Land Bots is acceptable… but overuse will result in further action."

---

## 4. Voice

### 4.1 The legacy system: Vivox [verified]

From 2007 until the current transition, SL voice was **Vivox**: a proprietary, external voice service. In the viewer it ran as a **separate daemon process** (`SLVoice`), speaking Vivox's SDK protocols; the viewer provisioned a voice account through a simulator capability and connected the daemon to Vivox's servers. Spatial (positional) voice was per-parcel on the local channel; P2P and group calls were Vivox conferences. The wiki's voice/technical page notes SLVoice "is compiled for the x86 (Intel) architecture only" and that "further development on this proprietary executable is not to be expected, as LL is phasing out Vivox and jumping to WebRTC communications instead" [Voice-Technical].

For bots, the legacy era was simple to summarise: **there was effectively no bot voice.** No open-source client library implemented the Vivox daemon protocol; bot platforms (Corrade, SmartBots, Radegast) are text-only. **[inference grounded in the absence of any voice support in the bot/client ecosystem surveyed for §5]**

### 4.2 The WebRTC transition [verified]

Linden Lab's replacement is self-hosted **WebRTC**:

- Architecture (from Linden Lab's developer presentation): "Spatial voice for a given region is handled by a **Second Life Voice Server running in conjunction with that region**. AdHoc and Group voice is handled by a pool of Second Life Voice Servers… Unlike Vivox's direct call mechanism, WebRTC P2P is routed through the AdHoc voice server pool as if it were a two-person Ad-Hoc voice conference. **There is no separate SLVoice executable for WebRTC Voice**" — the viewer instead links a native WebRTC library via a new voice module [WebRTC-Dev-View].
- Features touted: stereo audio, configurable bandwidth, noise reduction, automatic gain control, echo cancellation, multiple simultaneous streams [WebRTC-Dev-View][Feedback-WebRTC].
- Roll-out: beta through 2024–2025 (estate opt-in, ~150+ regions by Oct 2025 per community-roundtable reporting [SLInsider-Voice]); Linden Lab's official notice then announced: "Voice Service Update: Expanding WebRTC Voice to the Full Grid on **May 5**" — at announcement time the grid was split ~25% WebRTC / ~75% Vivox, with cross-system P2P calls unreliable until unification [LL-Voice-Update]. Modem World's coverage of the same date adds: "as the legacy Vivox system is decommissioned, viewer versions reliant on that service will lose all Voice functionality. This is a mandatory backend change by Linden Lab that cannot be altered or bypassed" [ModemWorld-WebRTC].
- New capabilities ride along: voice moderation tools (per-participant mute etc.) in WebRTC regions [LL-Voice-Update], and Linden Lab has publicly discussed AI voices and voice-to-text as directions, with community concern about voice cloning/fraud noted in roundtable reporting [SLInsider-Voice]. **[inference]** An AI agent that *speaks with a synthetic voice* lands in the middle of a live policy conversation; expect scrutiny, and design for disclosure (the scripted-agent flag) rather than stealth.
- Signalling still touches the classic stack: the EventQueueGet catalogue includes **ParcelVoiceInfo** and **RequiredVoiceVersion** events [EventQueueGet-CAP] — the sim tells the client what voice the parcel/region expects and where to connect.

**[uncertain]** The exact post-May-2026 end-state (whether any Vivox fallback remains anywhere on the grid, and the precise WebRTC signalling handshake a third-party client must implement) was not documented in a primary source found in this pass. The viewer source (open) is the ground truth and must be read before implementation.

### 4.3 What a bot can and cannot do with voice — honest assessment

**Can (verified/feasible):**
- *Receive* the same voice-related signalling any client receives (ParcelVoiceInfo / RequiredVoiceVersion via the event queue), i.e. know whether the region is voice-enabled and which system it runs [EventQueueGet-CAP].
- Speak **out-of-band**: nothing in SL policy requires an avatar's voice to originate inside the viewer; but see below — passing audio off as in-world voice requires actually joining the voice channel. **[inference]** A pragmatic v1 is text chat in-world + voice in a side channel (e.g. a voice call with the operator), clearly not SL voice.
- Use voice *adjacent* features: text chat remains the universal, logged, low-risk channel, and every bot platform in §5 is text-first for a reason.

**Cannot / very hard (inference from the architecture):**
- No existing open-source bot library implements SL WebRTC voice; joining a region's spatial voice means implementing the viewer's WebRTC voice module behaviour (signalling via sim caps/events, ICE/DTLS media to the SL voice server, Opus audio, positional attenuation metadata) — a project in itself. **[inference]**
- The old shortcut is gone: there is no longer a Vivox daemon to puppet, and after decommissioning, Vivox credentials/channels are dead ends [ModemWorld-WebRTC].
- Voice morphing (a legacy Vivox feature) did not survive the transition [SLInsider-Voice] — irrelevant for us (we synthesise our own voice via TTS), but it signals LL is re-drawing what voice identity means on the grid.
- **Spatial presence caveat [inference]:** if the agent is not in the voice channel, nearby residents using voice will be *inaudible* to it — a real perception gap for the "world awareness" goal. Mitigation options: implement WebRTC listen-only first (receive + transcribe), speak via TTS into the channel only after the receive path is proven. Listen-only still processes other residents' voice — the GDPR/CCPA and consent analysis of §3.1 applies with full force, arguably more than for text.

---

## 5. Practical connection options for a headless agent in 2026

### 5.1 Full protocol client (implement or reuse the viewer protocol)

**The reuse path is LibreMetaverse** [verified]: "a comprehensive .NET library that enables applications to connect to and interact with virtual world platforms including Second Life, OpenSimulator, and Halcyon… a complete client-side implementation of the virtual world protocols, handling everything from network communication and asset management to avatar appearance and 3D object rendering" [LibreMetaverse]. It is the maintained descendant of OpenMetaverse (the Radegast lineage), ships a `TestClient` example program (a command-driven headless client), a GridProxy debugging tool, and manager classes per subsystem (Network, Appearance, Inventory, Objects, Friends, Groups, etc.).

- **Perception available:** everything a viewer gets — local chat, IMs (1:1 and group sessions), object/avatar updates within draw distance (names, positions, animations), parcel info, inventory, L$ balance, teleports, sitting, animations/gesture activation, attachments.
- **Action available:** full movement (AgentUpdate control flags), chat/IM, teleport, sit/stand, touch, give/receive inventory, group operations.
- **Costs:** you own session lifecycle discipline (§2), throttle hygiene, ghost-session recovery, protocol drift (viewer-protocol changes occasionally break third-party clients until libraries catch up), and the entire policy surface of §3 — you *are* a viewer, legally.
- **Language fit:** .NET/C#. For a Python/Hermes host this means a sidecar process speaking to Hermes over a local socket/HTTP — an architectural seam, but also a clean one (crash isolation: the SL session can restart without killing the mind, and vice versa). **[inference]**

Writing a fresh protocol client from the message template is possible (Sharpview proves a from-scratch Rust implementation is viable [Animats-EQ]) but is person-months of work and unjustifiable while LibreMetaverse is maintained. **[inference]**

### 5.2 Text-mode / purpose-built bot clients

- **Radegast** [verified]: "Light-weight feature rich non-graphical client, ideal for situations where full 3D rendering client is less than ideal… supports full array of Second Life communications features, group and inventory management, friends list," plus a speech plugin (TTS/STT) for accessibility [Radegast-TPV]. The TPV directory page formerly warned the original author had died and the project was unmaintained; the directory's current listing names a new support contact from the LibreMetaverse maintainer's domain — **[uncertain]** exact current maintenance status; treat Radegast as a reference/prototype tool, not a foundation.
- **Corrade** (Wizardry and Steamworks) [verified from the wiki comparison]: a downloadable, **open-source (GPL)** multi-functional bot, controlled via instant messages and an **HTTP API**, with an LSL API for in-world scripts and built-in group-inviter/moderation tooling [Bot-Comparison]. It is a *product-shaped* bot: excellent for group management and scripted behaviours, but its command model (request/response API calls) is a straitjacket for a continuous-perception agent mind unless its API exposes the event stream we need — **[uncertain]**, needs a feature audit against our perception requirements.
- **Hosted bot services** (SmartBots, PikkuBot) [verified]: server-hosted, web/HUD/HTTP-API controlled, subscription-priced (the wiki table lists e.g. SmartBots at L$479/week fully functional) [Bot-Comparison]. Zero protocol work, but the agent's body lives on a third party's infrastructure, perception is limited to what their API surfaces, and an external AI mind driving a hosted bot adds a dependency and a privacy hop. Wrong shape for a sovereign, self-hosted Hermes plugin. **[inference]**

### 5.3 In-world LSL bridge (no protocol client at all)

The alternative religion: don't log the mind in as a viewer at all. Put a scripted object (or wearable) in-world and talk to it:

- **HTTP-In**: an LSL script calls `llRequestURL()` and receives a public `simhost…/cap/…` URL; the external service POSTs commands to it; the script receives them in `http_request` events. Hard limits [verified]: request **body truncated at 2,048 bytes**, headers ≤ 255 bytes, **64 pending** http_requests max; URLs are drawn from a scarce per-parcel pool "split by land ownership exactly like prim limits" and attachments share a **38-URL per-resident pool** [http_request][LSL-HTTP-Server]. URLs die on region restart/crossing and must be re-registered.
- **HTTP-Out**: the script polls or pushes to the external service with `llHTTPRequest` — throttled at **25 requests/20 s per object, 1,000/20 s per owner**, with an error throttle (5× 5xx in 60 s) [llHTTPRequest]. Response bodies default to 2,048 bytes (max 16,384 under Mono).
- **Email/XML-RPC**: legacy channels (`llEmail`, XML-RPC) exist but are slow, heavily throttled, and XML-RPC is discontinued per wiki notes — do not build on them. **[verified in part — LSL HTTP server page marks XML-RPC discontinued]**
- **What the bridge object can do:** listen to local chat (llListen, 20 m-ish chat range rules apply to objects too), speak (llSay/llWhisper/llShout, llInstantMessage), sense (llSensor, llGetObjectDetails — positions, names, velocity of nearby avatars/objects), display text, act as the agent's "ears and mouth" *at one fixed spot*.
- **What it cannot do:** be the avatar. An LSL bridge cannot move the avatar, teleport it, change its animation, or perceive anything beyond the object's own senses and chat range. The avatar still needs a client to log in — or the "agent" degenerates into a fixed kiosk/greeter bot wearing the account only as the object's owner. **[inference]** An LSL bridge alone does not satisfy Heimdall's goal (an *inhabited avatar*); it is a peripheral, not a body.

### 5.4 Hybrid architectures [inference throughout]

1. **Protocol client + LSL peripherals (recommended shape):** LibreMetaverse-class client as the body (login, movement, chat, perception); optional LSL objects as *extra senses/effectors* where the protocol is weak (e.g. an in-world HUD/object that relays region events, experience-system interactions, or RLV-style controls). Each layer covers the other's blind spots; policy-wise everything is still one scripted agent.
2. **Protocol client + out-of-band channels:** the avatar lives via the client; voice happens in a parallel WebRTC/Discord-like channel with the operator only. Honest, shippable v1 for "real-time voice" while SL-native voice (§4.3) is researched.
3. **LSL bridge first, client later:** prototype the *mind* (perception → memory → speech) against a stationary LSL ear/mouth on the operator's own parcel, where traffic/nuisance risk is minimal, then transplant the same mind interface onto the protocol client. This de-risks the AI side before touching login code — at the cost of building the LSL channel's awkward 2 KB, throttled, URL-churn ergonomics twice.

### 5.5 Comparison table

| Criterion | Full protocol client (LibreMetaverse) | Bot client (Corrade et al.) | LSL bridge only | Hybrid (client + LSL) |
|---|---|---|---|---|
| Avatar actually present & mobile | ✅ | ✅ | ❌ (object only) | ✅ |
| Local chat / IM perception | ✅ full | ✅ (API-shaped) | ⚠️ object's range only | ✅ full |
| Movement / teleport / sit | ✅ | partial | ❌ | ✅ |
| Object/avatar world state | ✅ viewer-grade | partial | ⚠️ sensor-range only | ✅ |
| Inventory / groups / L$ | ✅ | ✅ (its specialty) | ❌ | ✅ |
| Voice (SL-native) | ❌ (must build WebRTC) | ❌ | ❌ | ❌ (same gap) |
| Build effort | Medium (reuse library) | Low–Medium | Low | Medium–High |
| Protocol-drift risk | Library absorbs most | Vendor absorbs | None | Library absorbs most |
| Policy surface | Full TPVP + bot policy | Same, via vendor | Bot policy (object automation) | Full |
| Sovereignty / self-hosting | ✅ | ⚠️ (Corrade yes; hosted svcs no) | ✅ | ✅ |

---

## 6. Design implications for Heimdall

1. **Build the body on LibreMetaverse (or an equivalent maintained protocol library), as a sidecar process.** It is the only surveyed option that delivers viewer-grade perception + full agency for the avatar, and it absorbs protocol drift. The Hermes plugin talks to the sidecar over a local API; neither process's crash kills the other. Do not write a new protocol stack.
2. **Flag the account as a Scripted Agent before first unattended login** (Scripted Agent Status page) [Scripted-Agent-Policy]. Also put the agent's nature in the avatar's profile — the policy mandates the account flag; visible honesty is cheap insurance with residents and with Linden Lab.
3. **Budget messages like money.** 5,000/day *aggregate per operator*, group messages counted per recipient. The mind layer needs a token-bucket governor across chat/IM/notices with headroom (suggest a soft cap well under the limit), plus local-chat reply discipline (don't answer everything; don't speak twice in a row unbidden in busy areas).
4. **Never park on other people's land.** Traffic is computed from dwell minutes; a flagged agent is excluded from traffic, but "stuck at welcome areas/InfoHubs" is a named nuisance violation. Home-base the agent on the operator's parcel, implement stuck-detection (no position change + no interactions → move/log), and treat bans/ejects as normal events.
5. **Engineer the session lifecycle as a state machine, not a login call:** `LoggedOut → Authenticating → CircuitEstablishing → Present → (Teleporting | Crossing) → Present → LoggingOut`, with ghost-session back-off on crash, arrival-region verification after login ("did we land in a safe hub?"), event-queue ack discipline, and TeleportFinish/TeleportFailed both handled. The failure modes in §2 are where bots visibly misbehave.
6. **Throttle and bandwidth politeness are features:** set AgentThrottle to modest values (a text-perceiving bot does not need 446 kB/s of textures; consider suppressing texture/mesh fetching entirely if the library allows), respect every LSL throttle if LSL peripherals are used, and never retry-storm a failing cap or URL.
7. **Privacy is architecture, not a disclaimer.** Honour blocks/mutes and online-status/location privacy (never circumvent, per policy); design memory with per-resident purge (TPVP §4.a.i lets Linden Lab demand deletion of received data); decide and document retention for bystander chat *before* the agent hears its first conversation. GDPR/CCPA are named in the bot policy itself.
8. **Voice: plan in two stages.** Stage 1: no SL-native voice — text in-world; optional out-of-band voice with the operator. Stage 2 (research project): WebRTC listen-only (receive + transcribe) by studying the open viewer source's voice module, then TTS injection. Do not budget any Vivox work; that road is closed. Expect the AI-voice topic to be policy-sensitive (LL is publicly discussing AI voices and cloning concerns).
9. **Use the Aditi beta grid and OpenSimulator for development.** The same protocol family runs on OpenSim (the source of several mechanics references above), and Aditi exists precisely so login/session experiments don't happen against production accounts and residents. A private OpenSim instance is also the right place to rehearse ban/eject/teleport-failure handling.
10. **Ship a privacy policy with the plugin** and keep the TPVP/bot-policy compliance checklist in the repo; if the plugin is distributed, TPVP §4.b makes the privacy policy mandatory, and "my Third-Party Viewer is a bot ⇒ I comply with the bot policy" is a standing obligation, not a one-time act.

---

## 7. Open questions for the next research pass

- Exact MFA interaction with XML-RPC login for headless clients (live test on Aditi).
- Current inventory caps surface (AISv3) and which LibreMetaverse version implements it.
- The WebRTC voice signalling handshake in the current viewer source (what a third-party client must implement to join a region voice channel; is listen-only feasible without full viewer voice stack?).
- Offline IM cap numbers and group-chat (ChatterBox) session mechanics via caps, from the Knowledge Base / viewer source.
- Corrade's event/API surface vs our perception requirements; Radegast's current maintenance status.
- Whether Linden Lab's announced May 5, 2026 WebRTC cut-over completed grid-wide on schedule and whether any Vivox remnant persists.

---

## Sources

**Protocol & login**

- [Protocol] Second Life Wiki — Protocol / Message System overview. https://wiki.secondlife.com/wiki/Message_System
- [Current-Login] Second Life Wiki — Current login protocols. https://wiki.secondlife.com/w/index.php?title=Current_login_protocols&oldid=1203039
- [Login-Sequence] Second Life Wiki — Login sequence. https://wiki.secondlife.com:443/w/index.php?title=Login_sequence&oldid=1214574
- [Login-Strawman] Second Life Wiki — Second Life Login API Strawman (login response fields). https://wiki.secondlife.com:443/w/index.php?title=Second_Life_Login_API_Strawman&diff=cur&oldid=56466
- [UseCircuitCode] Second Life Wiki — UseCircuitCode message. https://wiki.secondlife.com:443/w/index.php?title=UseCircuitCode&direction=next&oldid=42552
- [AgentThrottle] Second Life Wiki — AgentThrottle message. https://wiki.secondlife.com:443/w/index.php?title=AgentThrottle&oldid=53142
- [Capabilities] Second Life Wiki — Capabilities. https://wiki.secondlife.com/w/index.php?title=Capabilities&diff=cur&oldid=52201
- [EventQueueGet] Second Life Wiki — EventQueueGet. https://wiki.secondlife.com:443/w/index.php?title=EventQueueGet&direction=prev&oldid=100373
- [EventQueueGet-CAP] Second Life Wiki — EventQueueGet CAP (event catalogue). https://wiki.secondlife.com/wiki/EventQueueGet_CAP
- [OpenSim-Teleports] OpenSimulator wiki — Teleports (protocol mechanics shared with SL). http://OpenSimulator.org/index.php?title=Teleports&diff=32087&oldid=prev
- [Animats-EQ] Second Life Community forums — Animats (Sharpview author) on event-queue loss severity. https://community.secondlife.com/profile/1480352-animats/content/page/64/?type=forums_topic_post
- [Feedback-Timeout] Second Life Feedback — "Properly save last location on irregular logouts" (Maestro Linden on session timeout). https://feedback.secondlife.com/server-bugs/p/properly-save-last-location-on-irregular-logouts-eg-timeout

**Policy**

- [Scripted-Agent-Policy] Linden Lab Official: Scripted Agent Policy (Second Life Wiki, current version 2023-04-03). https://wiki.secondlife.com/wiki/Linden_Lab_Official:Scripted_Agent_Policy
- [ZoHa-Bots] ZoHa Islands blog — reproduction of Linden Lab's 2009 bot-policy blog post. https://ZoHa-Islands.com/blog/the-bots-the-whole-bots-and-nothing-but-the-bots/
- [TPVP] Linden Lab — Policy on Third-Party Viewers. http://secondlife.com/corporate/third-party-viewers?_gl=1
- [TPVP-FAQ] Second Life Wiki — Third Party Policy and Viewer Directory FAQ. https://wiki.secondlife.com/w/index.php?title=Linden_Lab_Official%3AThird_Party_Policy_and_Viewer_Directory_FAQ&diff=cur&oldid=1140730
- [TPV-Support] Second Life Knowledge Base — Getting support for Third-Party Viewers. https://community.secondlife.com/knowledgebase/english/getting-support-for-third-party-viewers-r1349/

**LSL / bridging**

- [llHTTPRequest] Second Life Creation / LSL reference — llHTTPRequest (throttles). https://create.secondlife.com/script/lsl-reference/functions/llhttprequest/
- [http_request] Second Life Wiki — http_request event (2,048-byte body, 64 pending). https://wiki.secondlife.com:443/w/index.php?title=Http_request&diff=cur&oldid=1186048
- [LSL-HTTP-Server] Second Life Wiki — LSL HTTP server (URL pool limits). https://wiki.SecondLife.com/w/index.php?title=LSL_HTTP_server&diff=1217834&oldid=438443

**Voice**

- [LL-Voice-Update] Second Life Community — Voice Service Update: Expanding WebRTC Voice to the Full Grid on May 5. https://community.secondlife.com/news/featured-news/voice-service-update-expanding-webrtc-voice-to-the-full-grid-on-may-5-r11277/
- [ModemWorld-WebRTC] Modem World — "Be ready: WebRTC goes grid-wide in Second Life, May 5th, 2026". https://modemworld.me/2026/05/04/be-ready-webrtc-goes-grid-wide-in-second-life-may-5th-2026/
- [WebRTC-Dev-View] Linden Lab — "WebRTC on Second Life, a Developer's View" (presentation PDF). https://webrtc-public-voice-beta.s3.us-west-2.amazonaws.com/WebRTC+on+Second+Life%2C+a+Developer%27s+View.pdf
- [Feedback-WebRTC] Second Life Feedback — Upgrade Voice to WebRTC. https://feedback.secondlife.com/feature-requests/p/upgrade-voice-to-webrtc
- [Voice-Technical] Second Life Wiki — Voice/Technical (SLVoice daemon, Vivox phase-out). https://wiki.secondlife.com:443/w/index.php?title=Voice%2FTechnical&diff=prev&oldid=1218540
- [SLInsider-Voice] SLInsiderGuide — Philip Linden voice updates (WebRTC roundtable, AI voices). https://slinsiderguide.com/philip-linden-voice-updates/

**Clients & bot software**

- [LibreMetaverse] DeepWiki — cinderblocks/libremetaverse overview. https://deepwiki.com/cinderblocks/libremetaverse
- [Radegast-TPV] Second Life Wiki — Third Party Viewer Directory: Radegast. https://wiki.secondlife.com/wiki/Third_Party_Viewer_Directory/Radegast
- [Bot-Comparison] Second Life Wiki — Second Life bot software comparison. https://wiki.secondlife.com/w/index.php?title=Second_Life_bot_software_comparison&oldid=1197198
