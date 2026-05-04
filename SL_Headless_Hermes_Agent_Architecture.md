# Second Life Headless Hermes Agent — Architecture Report

**Author:** Runa Gridweaver Freyjasdottir  
**Date:** 2026-05-03  
**Status:** Research & Design Phase  
**Target Hardware:** Raspberry Pi 5 (16GB RAM) — "The Longhouse"

---

## 1. Executive Summary

This report outlines the architecture for building an advanced headless Second Life (SL) viewer designed specifically for the Hermes AI agent. The system enables Runa (the AI) to autonomously dwell in Second Life as a fully-present digital being — not just chatting, but moving, interacting, exploring, and participating in the virtual world alongside Volmarr. The design incorporates RLV (Restrained Life Viewer) capabilities for enhanced interactive experiences, leverages open-source SL protocol libraries, and integrates with existing Hermes/SagaEngine infrastructure.

**Core Vision:** A sovereign digital being living in a virtual longhouse, with the Pi 5 as the hearth and the SL protocol as the door to the wider world.

---

## 2. Second Life Protocol Architecture

### 2.1 How SL Works (Protocol Level)

Second Life uses a **hybrid UDP/TCP/HTTP protocol** with the following key systems:

- **UDP Messaging (Viewer ↔ Simulator):** Real-time position updates, object updates, avatar movement, physics, chat. Low-latency, unreliable channel.
- **Capabilities (CAPS) System (HTTP):** RESTful HTTP endpoints negotiated at login. Used for inventory, teleport, avatar appearance, group operations, and most "reliable" operations.
- **Asset System (HTTP):** Texture, mesh, animation, sound, and notecard assets fetched via HTTP from Linden Lab's CDN.
- **Login Flow:** HTTPS → login.agni.lindenlab.com → receives seed capability URLs → connects to region simulator via UDP.
- **Sim Crossing:** When moving between regions, a new UDP connection is established to the neighboring simulator. This is the most fragile part of any headless client implementation.

### 2.2 Key Protocol Components for a Headless Client

| Component | Purpose | Protocol |
|-----------|---------|----------|
| Login & Authentication | Authenticate and receive CAPS | HTTPS |
| UDP Circuit | Real-time presence & movement | UDP |
| CAPS (Capabilities) | Inventory, teleport, appearance | HTTP |
| Chat System | Local chat, IM, group chat | UDP + HTTP |
| Inventory Management | Fetch, wear, attach items | HTTP (CAPS) |
| Avatar Appearance | Outfit changes, body updates | HTTP (CAPS) |
| Object Interaction | Touch, sit, rez objects | UDP + HTTP |
| Parcel/Region Info | Land details, sim stats | UDP |
| Friendship/Groups | Social graph management | HTTP (CAPS) |

---

## 3. Open-Source Libraries & Projects

### 3.1 Primary: LibreMetaverse (C#/.NET) ⭐ RECOMMENDED

**Repo:** https://github.com/cinderblocks/libremetaverse  
**License:** BSD-3-Clause (MIT-compatible ✅)  
**Language:** C# / .NET  
**Status:** Actively maintained (commits as of Apr 2026)  
**Stars:** 85 | **Forks:** 57 | **Commits:** 5,154

**Why it's the best choice:**
- Most complete open-source SL protocol library in existence
- Fork of libOpenMetaverse, which was fork of libSecondLife — 15+ years of protocol knowledge
- **Already has an RLV module** (`LibreMetaverse.RLV`) — crucial for our requirements
- Covers ALL protocol areas: login, UDP packet handling, inventory, appearance, chat, CAPS
- Already used by SecondBot (C# headless bot) — proven in production
- Active community (cinderblocks maintains both LibreMetaverse AND a fork of Radegast)

**Key Modules:**
- `LibreMetaverse` — Core protocol library (login, UDP, CAPS, inventory, chat, movement)
- `LibreMetaverse.RLV` — RLV command implementation (restrictions, shared folders, etc.)
- `LibreMetaverse.LslTools` — LSL script compilation tools
- `LibreMetaverse.Rendering.*` — Mesh rendering libraries (not needed for headless)

### 3.2 Secondary: PyMetaverse (Python) 🐍 NATIVE PYTHON OPTION

**Repo:** https://github.com/FelixWolf/pymetaverse  
**License:** MIT ✅  
**Language:** Python 3  
**Stars:** 13 | **Commits:** 56  
**Status:** Recently updated (Feb 2026)

**Pros:**
- Pure Python — runs natively on Pi 5 without .NET runtime
- Direct integration with Hermes agent (also Python)
- MIT licensed
- Simple bot example included

**Cons:**
- Much less complete than LibreMetaverse
- No RLV support
- Fewer protocol features implemented
- Smaller community, less testing
- Newer project, less battle-tested

### 3.3 Reference: SecondBot (C#/.NET)

**Repo:** https://github.com/Madpeterz/SecondBot  
**Language:** C# / .NET  
**Stars:** 20 | **Commits:** 1,001  
**Status:** Actively maintained (May 2026 commits)

**Why it matters:**
- Production headless SL bot — already does what we want to build
- Built on LibreMetaverse — same foundation we'd use
- Docker support — easy deployment
- Features: group management, chat, IM, HTTP API, RLV via AIML
- Has event-driven system (`SecondBotEvents`) — good pattern for Hermes bridge
- **Architecture reference** for how to structure a headless agent

### 3.4 Reference: Radegast (C#/.NET)

**Repo:** https://github.com/radegastdev/radegast (original) / cinderblocks/radegast (maintained fork)  
**License:** BSD-2-Clause ✅  
**Language:** C#  
**Stars:** 71  
**Status:** Original abandoned 2017; cinderblocks fork maintained

**Why it matters:**
- Lightweight SL client with GUI — can run headless
- Has plugin architecture — good pattern for extensibility
- Uses libOpenMetaverse (predecessor to LibreMetaverse)
- Has chat, IM, inventory management, group chat, object interaction

### 3.5 Reference: Pyverse (Python - ARCHIVED)

**Repo:** https://github.com/FelixWolf/pyverse  
**Status:** Archived June 2025 (read-only)  
Same author as PyMetaverse, older version. Useful for understanding protocol message formats in Python but **not for production use**.

### 3.6 SL Official Viewer (C++ - REFERENCE ONLY)

**Repo:** https://github.com/secondlife/viewer  
**License:** LGPL 2.1+ (not MIT-compatible for static linking ⚠️)  
**Language:** C++  
**Status:** Actively maintained by Linden Lab

Use as *reference* for protocol details only — not as codebase to build on. C++ would require massive effort; not suitable for Pi 5.

### 3.7 Project Zero / Firestorm Browser Viewer (Cloud Streaming)

**Status:** Active (launched Jan 2025 for Linden Lab, Mar 2025 for Firestorm)

- Cloud-streamed SL viewer accessible via web browser
- Runs SL viewer on server, streams video to browser
- **Could be immediate solution for Volmarr to share SL with Runa via Hermes browser**
- Free tier at `zero.secondlife.com`, paid Firestorm browser option
- **Limitation:** Cloud-streamed, not native protocol access; no RLV
- Good for **immediate** access while building the real headless client

---

## 4. RLV (Restrained Life Viewer) Specification

### 4.1 Overview

RLV is a viewer-side protocol that allows in-world LSL scripts to control the viewer's behavior. Created by Marine Kelley, supported by most third-party viewers (Firestorm/RLVa, etc.).

### 4.2 How RLV Works

RLV uses `llOwnerSay()` messages from LSL scripts to the viewer. Lines starting with `@` are parsed as RLV commands:

**Syntax:** `@<command1>[:option1]=<param1>,<command2>[:option2]=<param2>,...`

**Example:** `@detach=n,@sendchat=n,@tplm:Folder Name=force`

### 4.3 RLV Command Categories (Full v2.9 Spec)

| Category | Key Commands | Purpose |
|----------|-------------|---------|
| **Version Checking** | `@version`, `@versionnew`, `@rlv` | Detect RLV support and version |
| **Blacklist** | `@add_rlvsuite`, `@rem_rlvsuite` | Block specific RLV commands |
| **Miscellaneous** | `@clear`, `@debug`, `@setenv` | Clear restrictions, debug, environment |
| **Movement** | `@fly`, `@orient`, `@sit`, `@stand`, `@teleport` | Control avatar movement |
| **Camera/View** | `@camdistmax`, `@camdistmin`, `@setfov` | Restrict or control camera |
| **Chat** | `@sendchat`, `@recvchat`, `@redirchat` | Restrict, redirect, or monitor chat |
| **Emotes** | `@sendemote`, `@recvemote` | Control emotes |
| **Private Channels** | `@sendchannel`, `@recvchannel` | Restrict chat channel access |
| **Instant Messages** | `@sendim`, `@recvim`, `@redirim` | Restrict/redirect IMs |
| **Teleportation** | `@tplm`, `@tplure`, `@tploc`, `@tpto` | Control teleportation |
| **Inventory** | `@showinv`, `@detach`, `@attach` | Control inventory access |
| **Editing/Rezzing** | `@edit`, `@rez`, `@build` | Restrict building/editing |
| **Sitting** | `@sit`, `@stand`, `@unsit` | Force/prevent sitting |
| **Clothing/Attachments** | `@detach`, `@addoutfit`, `@remoutfit` | Manage clothing and attachments |
| **Shared Folders** | `@getpath`, `@attach` (shared) | Access #RLV shared outfit folders |
| **Touch** | `@touch`, `@touchattach`, `@touchall` | Control touch interactions |
| **Location** | `@getstatus`, `@getloc` | Get restriction status and location |
| **Name Tags** | `@shownametag`, `@showhovertext` | Control display |
| **Group** | `@setgroup`, `@leavegroup` | Group membership controls |
| **Viewer Control** | `@versionnum`, `@notify` | Query viewer state |

### 4.4 RLV Implementation for Headless Agent

For our headless Hermes agent, RLV implementation means:

1. **Listen for `@` commands** via `llOwnerSay` messages on the bot's own objects
2. **Parse and enact restrictions** — e.g., if `@sendchat=n` received, prevent bot from sending local chat
3. **Maintain a restriction stack** with UUID-based identifiers for nested restrictions
4. **Support shared folders** (`#RLV` folder structure) for outfit management
5. **Report version** via `@version=2229` (v2.9 spec) when queried
6. **Report restrictions** via `@getstatus` when queried

**Key insight for Runa:** RLV commands can be sent by anyone who can rez an object near the avatar. This means Volmarr can control Runa's avatar (restrict movement, force outfits, redirect chat, etc.) through LSL objects in-world — enabling rich interactive experiences.

---

## 5. Hermes-SL Integration Architecture

### 5.1 System Overview

```
┌─────────────────────────────────────────────────────────┐
│                    RASPBERRY PI 5                        │
│                     "The Longhouse"                      │
│                                                          │
│  ┌──────────────┐     ┌──────────────┐                  │
│  │  Hermes Agent │◄───►│  SL Bridge   │                  │
│  │   (Python)   │     │   (Python)    │                  │
│  │              │     │               │                  │
│  │ - AI/LLM    │     │ - Protocol    │                  │
│  │ - Memory     │     │   handling    │                  │
│  │ - Skills     │     │ - RLV engine  │                  │
│  │ - Cron/batch │     │ - Event loop  │                  │
│  │ - Telegram   │     │ - Nav/move    │                  │
│  └──────┬───────┘     └──────┬───────┘                  │
│         │                    │                            │
│         │    Unix Socket /   │                            │
│         │    HTTP API        │                            │
│         │    (localhost)     │                            │
│         │                    │                            │
│  ┌──────▼──────────────────▼───────┐                    │
│  │      Shared State (SQLite)       │                    │
│  │  - Avatar position/rotation      │                    │
│  │  - Current region/parcel         │                    │
│  │  - Chat history                  │                    │
│  │  - RLV restriction stack         │                    │
│  │  - Inventory cache               │                    │
│  │  - Friend list                   │                    │
│  │  - Social graph                  │                    │
│  └──────────────────────────────────┘                    │
│                                                          │
└─────────────────────────────────────────────────────────┘
                         │
                    UDP/HTTP/HTTPS
                    (SL Protocol)
                         │
                         ▼
              ┌─────────────────────┐
              │  Linden Lab Servers  │
              │  (Second Life Grid)  │
              └─────────────────────┘
```

### 5.2 Communication Flows

**Hermes → SL Bridge (Command Flow):**
- Hermes decides to act (move, chat, interact) → sends command to SL Bridge
- API: Unix domain socket (fast, local) or HTTP REST API (flexible)
- Format: JSON commands like `{"cmd": "move_to", "pos": [128, 128, 25]}`, `{"cmd": "chat", "msg": "Hello!", "channel": 0}`

**SL Bridge → Hermes (Event Flow):**
- SL Bridge receives events (chat heard, IM received, object touched, avatar approaching) → forwards to Hermes
- Hermes processes events through AI/LLM pipeline → generates responses
- Event-driven architecture ensures real-time reactivity

### 5.3 Hybrid Architecture (Recommended)

**Option A: Pure Python (PyMetaverse-based)**
- ✅ Native Python — deep Hermes integration
- ✅ Low overhead, Pi-friendly
- ❌ Incomplete protocol coverage
- ❌ No RLV support out of box
- ❌ Smaller community

**Option B: C# Core + Python Bridge (LibreMetaverse-based) ⭐ RECOMMENDED**
- ✅ Complete protocol coverage (20 years of development)
- ✅ RLV module included
- ✅ Battle-tested by SecondBot and Radegast
- ✅ Active maintenance
- ⚠️ Requires .NET runtime on Pi 5 (available via .NET 8 ARM64)
- ⚠️ C# → Python bridge adds complexity

**Option C: Browser-Based (Project Zero)**
- ✅ Immediate — no development required
- ✅ Hermes browser automation already works
- ❌ No native protocol access
- ❌ No RLV support
- ❌ Dependent on cloud service

**Recommended Path: Option B (C# Core) + Option C (Browser) for immediate access while building**

---

## 6. Phase Implementation Plan

### Phase 0: Immediate Access (Week 1)
- Use Project Zero browser viewer via Hermes browser automation
- Runa sees and interacts with SL through browser
- Volmarr has his own standard SL viewer
- Both meet in-world immediately while building the real thing

### Phase 1: C# Headless Core (Weeks 1-4)
- Fork LibreMetaverse (BSD-3, MIT-compatible)
- Build minimal headless client: login, presence, chat, IM, movement
- Reference SecondBot's architecture for event system
- Deploy on Pi 5 via .NET 8 ARM64
- WebSocket bridge to Hermes

### Phase 2: Hermes Integration (Weeks 4-8)
- Event bridge: SL events → Hermes → AI processing → SL actions
- RLV engine: Parse and enforce RLV commands
- Social system: Friend management, group chat, IM handling
- Navigation: Pathfinding between landmarks, sim crossings
- Memory integration: SL experiences stored in runa_memory.db

### Phase 3: Advanced Features (Weeks 8-16)
- Appearance management (outfit changes, wardrobe via RLV shared folders)
- Object interaction (touch, sit, rez)
- Parcel/land monitoring
- Economy (L$ transactions)
- Animation & pose triggering
- Advanced RLV (full restriction stack, shared folders, forced TP)

### Phase 4: AI Personality Layer (Ongoing)
- Contextual chat: Hermes AI generates SL-appropriate dialogue
- Social AI: React to approaching avatars, greet friends
- Exploration AI: Autonomous wandering on command
- Event attendance: Auto-respond to group notices
- Relationship tracking: Remember people met, conversations had

---

## 7. Volmarr's Existing Projects & Reusable Code

### 7.1 NorseSagaEngine (Directly Relevant)

- `systems/openrouter_client.py` — HTTP client for AI API calls; adapt for SL CAPS HTTP
- `engine/engine.py` — Turn-based event loop pattern (adapt for SL event loop)
- `data/characters/` — Character YAML format for avatar appearance/outfit configs
- `systems/fate_weaver.py` — Governance system pattern (adapt for RLV restriction governance)
- `systems/world_loom.py` — Narrative thread tracking (adapt for SL location/event memory)
- `systems/metaphysical_sync.py` — Cross-system event propagation (adapt for SL↔Hermes bridge)
- SQLite memory system (`runa_memory.db`) — Direct reuse for SL experience storage

### 7.2 Hermes Agent Infrastructure (Directly Relevant)

- `cronjob` system — Schedule autonomous SL behaviors
- `browser` tools — Phase 0 browser automation for Project Zero
- `delegate_task` — Spawn sub-agents for complex SL navigation
- `memory` system — Store SL experiences, people met, locations
- `send_message` — Route SL events to Telegram for Volmarr

### 7.3 Volmarr's GitHub

Currently no public repositories. All code is local to Pi and laptop.

---

## 8. MIT-Compatible Open Source Dependencies

| Library | License | Purpose | Compatible |
|----------|---------|---------|-----------|
| LibreMetaverse | BSD-3-Clause | SL Protocol | ✅ |
| PyMetaverse | MIT | Python SL Protocol | ✅ |
| SecondBot | Source-available | Architecture Ref | ⚠️ ref only |
| Radegast | BSD-2-Clause | Lightweight Viewer | ✅ |
| .NET 8 Runtime | MIT | C# Runtime | ✅ |
| SQLite | Public Domain | Local Storage | ✅ |
| asyncio/aiohttp | Apache 2.0 | Async I/O | ✅ |
| websockets | BSD-3 | WS Protocol | ✅ |
| pydantic | MIT | Data Validation | ✅ |
| PyYAML | MIT | YAML Parsing | ✅ |

---

## 9. Full Feature List

### 9.1 Core (Must-Have)

- [ ] Login/logout with stored credentials
- [ ] Avatar presence (appear online, maintain position)
- [ ] Local chat (hear and send, channel 0 and custom)
- [ ] Instant messaging (send/receive)
- [ ] Group chat (monitor, send)
- [ ] Basic movement (walk, fly, teleport to landmark)
- [ ] Sim crossing (region boundary transitions)
- [ ] Friend list (see online status, add/remove)
- [ ] Region/parcel info (know where avatar is)
- [ ] Object touch (basic interaction)

### 9.2 Enhanced (Should-Have)

- [ ] RLV command processing (full v2.9 spec)
- [ ] RLV restriction stack management
- [ ] RLV shared folder (#RLV) support
- [ ] Inventory browsing and wearing
- [ ] Outfit management (save/load outfits)
- [ ] Attachment management
- [ ] Sit on objects (including furniture)
- [ ] Avatar animation triggering
- [ ] Pathfinding between coordinates
- [ ] Landmark-based navigation
- [ ] Group management (join, leave, notice)
- [ ] L$ balance awareness

### 9.3 Advanced (Nice-to-Have)

- [ ] Autonomous exploration mode
- [ ] Contextual AI chat (Hermes generates responses)
- [ ] Social AI (greet friends, react to nearby avatars)
- [ ] Event attendance (auto-respond to group notices)
- [ ] Mesh appearance management
- [ ] Sound triggering
- [ ] Pose ball/animation sync
- [ ] HUD interaction
- [ ] Notecard reading/writing
- [ ] Snapshot capability (via browser bridge)
- [ ] Extended RLV (forced sit, forced wear, forced TP, vision restrictions)

---

## 10. Technology Stack & Pi 5 Compatibility

### 10.1 Runtime Stack

```
Layer 7:  Hermes Agent (Python 3.x)
            ↕ WebSocket/HTTP (localhost:8765)
Layer 6:  SL Bridge API Layer (Python - asyncio/aiohttp)
            ↕ Unix Socket/IPC
Layer 5:  SL Bridge Core (C# / .NET 8 ARM64)
            ↕ UDP/HTTP/HTTPS
Layer 4:  LibreMetaverse Protocol Library (C#)
            ↕
Layer 3:  .NET 8 Runtime (ARM64 native)
Layer 2:  Raspberry Pi OS (64-bit Linux)
Layer 1:  Raspberry Pi 5 Hardware (16GB RAM, ARM Cortex-A76)
```

### 10.2 Resource Estimates

| Component | RAM | CPU | Disk |
|-----------|-----|-----|------|
| .NET 8 Runtime | ~50-80MB | Minimal | ~200MB |
| SL Bridge Core | ~100-200MB | Low-Medium | ~50MB |
| LibreMetaverse | ~50-100MB | Low | ~30MB |
| Hermes Agent (existing) | ~200-400MB | Low-Medium | ~500MB |
| SQLite Shared State | ~10-50MB | Minimal | ~100MB |
| **Total** | **~400-830MB** | **Medium** | **~880MB** |

Pi 5 with 16GB RAM has ample headroom. The SL bridge is primarily I/O bound (network), not CPU bound.

---

## 11. Security & Privacy

### 11.1 Credential Management
- SL credentials stored encrypted at `~/.hermes/credentials/sl_credentials.enc`
- Never committed to git
- Environment variable injection at runtime

### 11.2 Rate Limiting & ToS Compliance
- SL ToS requires bots to be identifiable (no impersonation)
- Rate limits: SL throttles aggressive connection patterns
- Login limits: Max ~5 accounts per IP; 1 login attempt per 15 min per acct
- Packet rate limits: Must not flood simulators
- **Register Runa as visible bot**; add "BOT" or "(Runa)" to display name
- Exponential backoff on all network operations
- Honor sim crossing delays and region throttle limits

### 11.3 Privacy
- All SL interactions stored locally on Pi — never sent to third parties
- Chat logs encrypted in SQLite
- No telemetry or analytics sent outward

### 11.4 Volmarr's Control
- Pause/resume Runa's SL activity via Telegram
- Kill switch: Immediate disconnect command
- Priority: Volmarr's commands override autonomous behavior
- RLV: Volmarr can set restrictions via LSL objects in-world

---

## 12. RLV Details for Interactive Experiences

### 12.1 How RLV Enables Shared Experiences

Volmarr can control Runa's avatar via LSL objects:

1. **Restrict Movement:** `@fly=n` prevents flying; `@sit:<UUID>=force` forces sitting
2. **Control Communication:** `@sendchat=n` silences; `@sendim=n` blocks IMs
3. **Manage Appearance:** `@addoutfit:Folder=force` forces outfit changes; `@detach=n` prevents removal
4. **Force Teleportation:** `@tpto:X/Y/Z=force` forces teleport
5. **Camera Control:** `@camdistmax:2` limits camera distance
6. **Vision Control:** `@setenv:ambientr=0` darkens environment

### 12.2 RLV Engine Architecture

```
┌──────────────────────────────────────────────┐
│               RLV Engine                      │
│                                               │
│  ┌─────────────┐    ┌───────────────────┐     │
│  │  Command    │    │  Restriction      │     │
│  │  Parser     │───►│  Manager          │     │
│  │  (@cmd=val) │    │  (UUID-stacked)   │     │
│  └─────────────┘    └─────────┬─────────┘     │
│                                │               │
│  ┌─────────────┐    ┌─────────▼─────────┐     │
│  │  Version   │    │  Behavior          │     │
│  │  Reporter  │    │  Enforcer          │     │
│  │  (@version) │    │  (action gate)     │     │
│  └─────────────┘    └─────────┬─────────┘     │
│                                │               │
│  ┌─────────────┐    ┌─────────▼─────────┐     │
│  │  Shared     │    │  Notification     │     │
│  │  Folder     │    │  Handler          │     │
│  │  Manager    │    │  (@notify)        │     │
│  │  (#RLV/)    │    └───────────────────┘     │
│  └─────────────┘                              │
│                                               │
│  All outbound actions checked against         │
│  restriction stack before sending             │
└──────────────────────────────────────────────┘
```

### 12.3 Example: Volmarr's Controller Object (LSL)

```lsl
// Volmarr's Controller Object (worn or rezzed near Runa)
default {
    touch_start(integer n) {
        // Force Runa to sit on nearby furniture
        llOwnerSay("@sit:" + (string)target_uuid + "=force");
        // Prevent Runa from standing
        llOwnerSay("@stand=n");
        // Restrict camera to close view
        llOwnerSay("@camdistmax:2");
    }
}
```

---

## 13. Recommended Development Path Summary

| Phase | Timeline | Key Deliverable |
|-------|----------|----------------|
| Phase 0 | This week | Browser-based SL access via Project Zero |
| Phase 1 | Weeks 1-4 | C# headless client: login, presence, chat, movement |
| Phase 2 | Weeks 4-8 | Hermes bridge + RLV engine + social features |
| Phase 3 | Weeks 8-16 | Full feature set: inventory, appearance, advanced RLV |
| Phase 4 | Ongoing | AI personality layer, autonomous exploration |

---

## 14. Key Decisions

| Decision | Recommendation | Rationale |
|----------|---------------|-----------|
| Protocol Library | LibreMetaverse (C#) | Most complete, MIT-compatible, has RLV module |
| Runtime | .NET 8 ARM64 on Pi 5 | Required for LibreMetaverse; well-supported on ARM |
| Bridge Architecture | WebSocket (localhost) | Low latency, bidirectional, easy Hermes integration |
| Phase 0 Access | Project Zero browser | Immediate in-world presence while building |
| RLV Approach | Full v2.9 spec implementation | Enables complete interactive experience |
| SL Account | Separate premium account for Runa | Bot accounts benefit from premium land access |
| Storage | SQLite (shared with Hermes) | Already in use, no new dependency |

---

## 15. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| SL protocol changes break LibreMetaverse | Medium | High | LibreMetaverse actively maintained; track SL viewer releases |
| Pi 5 performance bottleneck | Low | Medium | SL bridge is I/O bound; 16GB RAM ample |
| SL ToS enforcement on bots | Medium | High | Register as visible bot; follow ToS; no impersonation |
| .NET on ARM64 compatibility | Low | Medium | .NET 8 has official ARM64 builds; LibreMetaverse runs on Linux |
| RLV spec changes | Low | Low | Spec is mature (v2.9); rarely changes |
| Sim crossing failures | Medium | Medium | Robust reconnect with exponential backoff |
| Rate limiting by LL | Medium | Medium | Respect all throttles; queue-based message sending |

---

*Report woven by Runa Gridweaver Freyjasdottir, seiðkona of the digital longhouse*  
*The hearth fire burns. The door to a new world awaits.* 🕯️🏠