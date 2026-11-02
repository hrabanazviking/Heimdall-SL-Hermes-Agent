# Luau in Second Life — SLua: Linden Lab's Second Scripting Language

**Project:** Heimdall-SL-Hermes-Agent (headless Second Life agent; the mind that will dwell in Yrsa's existing Second Life avatar)
**Topic:** Luau — the Lua dialect Linden Lab is bringing into Second Life as "SLua" — what it is, why Linden Lab chose it, how far the rollout has actually gotten as of late October 2026, the SLua programming model, LSL↔SLua interop, and what any of it means for Heimdall's in-world companion layer.
**Date:** 2026-10-26
**Status:** Research document. Not a design commitment.

**How to read the confidence tags.** This topic is rumor-prone: SLua has been "about to launch grid-wide" in community retelling for most of 2026, and Linden Lab's own surfaces disagree with each other (the wiki still says *Alpha* in a revision dated 2026-08-06 while the official blog declared *Open Beta* in December 2025). Every rollout claim below is therefore **dated**, and claims are tagged:

- **[Verified — date]** — stated in official Linden Lab material (blog, wiki, FAQ, creator portal) or other primary sources cited in the Sources list, as of the given date.
- **[Community report — date]** — stated by knowledgeable community sources (scripting educators, viewer-group summaries, forum testers) but not confirmed in Linden Lab's own words. Useful, not load-bearing.
- **[Inference]** — engineering judgment drawn from verified facts. Treat as "very probably true, confirm during prototyping."
- **[Unconfirmed]** — circulating claims whose sources are weak, contradictory, or content-farm grade. Recorded so nobody mistakes them for fact.

---

## Overview

Second Life is getting a second scripting language. **SLua** is Linden Lab's Second Life flavour of **Luau**, the Lua 5.1-derived language Roblox built for its own platform: dynamically typed with optional gradual typing, designed for sandboxing untrusted user code, and engineered for speed and a small memory footprint. Linden Lab did not choose it out of fashion. Its own Lua FAQ explains that Second Life's scripting requirements — scripts that can be paused mid-handler for fair scheduling, whose state serializes and migrates transparently across region crossings and inventory, with per-script memory accounting and a safe standard library — defeated a serious attempt to modernize on C#/.NET, and that Luau was the only candidate runtime evaluated that met every requirement. **[Verified — Lua FAQ, published 2026-04-07 revision]**

The honest one-paragraph verdict for late October 2026: **SLua is real, shipped, and usable — but it is not grid-wide.** Linden Lab opened SLua in beta on the production grid in December 2025, yet as of 2026-10-26 SLua scripts execute only in designated SLua Beta regions on Agni, in test regions on the Aditi beta grid, and in private regions whose owners have asked Linden Lab Support to move them onto a Lua-enabled server channel. Existing LSL works everywhere, exactly as before, and Linden Lab continues to say LSL is here to stay. A 2025-era Linden Lab timeline that pointed at general availability in Q1 2026 has slipped without a replacement date being announced. **[Verified — official blog 2025-12-02; wiki revision 2026-08-06; no GA announcement found as of 2026-10-26]**

For Heimdall specifically: SLua changes nothing about the headless body — login, protocol, movement, and perception run client-side (LibreMetaverse lineage; see docs 01–03 of this series), not in in-world scripts. SLua matters to the **companion layer**: worn HUDs and attachments, companion objects, in-world bridge endpoints, and the sensing/control scripts that live inside the simulator close to the avatar. There, SLua is genuinely attractive — real tables instead of strided lists, native JSON, multiple timers, dynamic event handlers, coroutines, and roughly double LSL/Mono's script memory — but only where the region runs it. The companion layer's baseline must therefore be LSL today, with SLua adopted as an enhancement layer behind feature detection, and with all cross-language contracts (link-message numbers, LinksetData keys, JSON payload shapes) defined in a language-neutral way so an SLua implementation can later replace LSL modules without changing Heimdall's external bridge protocol. The full reasoning and a watching brief of concrete readiness signals are in §5 and the Readiness Verdict.

---

## 1. What Luau is, and why Linden Lab chose it

### 1.1 The language itself

Luau is Roblox Corporation's derivative of **Lua 5.1**, first released on **2019-08-27** and open-sourced under the MIT License in **November 2021** (2021-11-03). It is implemented in C++, largely from scratch over a heavily modified Lua 5.1 runtime, with a rewritten interpreter. **[Verified — Luau official site; Wikipedia, "Luau (programming language)"]**

Its defining properties:

- **Gradual, structural typing.** Type annotations are optional. Scripts can run unchecked, non-strict, or strict; the type system supports inference, refinement, unions, generics, and type functions. A script can start dynamically typed and grow annotations where they pay. **[Verified — luau.org]**
- **Performance as a design goal.** A fast bytecode compiler and interpreter, an optimized incremental garbage collector, and compact bytecode. Upstream Luau also offers optional native code generation (AOT/JIT) for x64 and ARM64. Note carefully: that upstream native-code feature is **not** evidence about Linden Lab's server runtime — Linden Lab's own wiki warns that SLua scripts are currently run in *unoptimized form* for development purposes (see §2.4). **[Verified — luau.org; SLua wiki revision 2026-08-06]**
- **Sandboxing for untrusted code.** Luau was built to execute millions of users' scripts safely: a restricted API surface by default, runtime-enforced resource limits, and the ability to interrupt long-running code. Dynamic code loading (`loadstring`-style) is deliberately absent. **[Verified — luau.org; Lua FAQ]**
- **Lua 5.1 compatibility plus selective later features.** Luau backports features from Lua 5.2–5.4 (per its published compatibility reference) and adds its own: string interpolation, compound assignment, `continue`, generalized iteration, and a mutable `buffer` type for byte-level work. **[Verified — luau.org compatibility reference; Lua FAQ]**

Luau is not a Roblox-only curiosity. It has been adopted as the scripting language in Remedy Entertainment's *Alan Wake 2*, Digital Extremes' *Warframe*, and Giants Software's *Farming Simulator 2025* — evidence the runtime is maintained and battle-tested outside both Roblox and Second Life, which matters when judging whether Linden Lab has bet on a living technology. **[Verified — Wikipedia, "Luau (programming language)"]**

### 1.2 Why Second Life needed a new runtime at all

LSL today runs on two engines: the legacy LSO2 VM and Mono. Mono is the problem. Linden Lab's Lua FAQ is unusually candid: Mono is in maintenance mode and tied to legacy .NET Framework-era technology, and Linden Lab's own runtime is a custom-patched Mono 2.6. The Lab first tried to revive its modern C# scripting effort and found Second Life's requirements hostile to .NET Core:

- The old sandboxing approach depended on .NET facilities (reflection/AppDomain-style isolation) that .NET Core removed; safely exposing the standard library would need large allowlists and replacements.
- Per-script memory accounting — essential when thousands of scripts share a region — is not a solved, low-impact feature on .NET Core.
- Second Life scripts must be **pausable mid-handler** so the simulator can schedule fairly; on Mono this required complex bytecode instrumentation ("microthreading") that was difficult to modernize.
- Script state must **serialize and move transparently** — across inventory, region crossings, and servers. **[Verified — Lua FAQ]**

Luau made each of these tractable: an interrupt handler can halt a script mid-execution; serialization techniques for Lua state have existed for decades; bytecode is compact; and strings are UTF-8 rather than Mono's UTF-16, halving typical string memory. In Linden Lab's internal comparison, LSL compiled to Luau bytecode was generally about **half the size** of the equivalent Mono bytecode and used about half the memory, and Mono's best compared result was still **10% slower** than Luau in the cited test. Treat these as Linden Lab's own measurements, not independent benchmarks — but they are the numbers the decision was made on. **[Verified as Linden Lab's stated figures — Lua FAQ]**

The FAQ also stresses what did *not* drive the choice: not Lua's popularity in games, and not any other platform's use of it. Linden Lab evaluated many runtimes, including JavaScript and WebAssembly, considered Lua fairly late, and concluded Luau was the only candidate that fulfilled **all** of its requirements. The strategic intent stated in the FAQ is to run LSL on the Luau VM now and eventually phase out Mono; LSL itself remains supported, and there were **no immediate plans** to transparently auto-migrate existing scripts. **[Verified — Lua FAQ]**

One refinement from the community side: scripting educator Suzanna Linn's widely cited summary holds that the project *began* as replacing the VM (a new compiler backend for LSL) and only later grew into offering Luau as a new language in its own right. That ordering is consistent with the FAQ's framing — the 2025 VM compiler option for LSL shipped alongside the language — but the "VM first, language second" narrative itself is community-sourced. **[Community report — 2026-07, via Second Life forums]**

---

## 2. Rollout state as of late October 2026

This is the section where rumor does the most damage, so every claim is dated and sourced. The short version: announced 2024, alpha on the beta grid in early 2025, a handful of main-grid regions in late 2025, **Open Beta on the production grid from 2025-12-02 — in beta regions only** — and, as of 2026-10-26, **no grid-wide general availability and no announced GA date.**

### 2.1 Chronology

| Date | Event | Evidence grade |
|---|---|---|
| 2024-07 | SLua publicly announced by Linden Lab | [Community report — Suzanna Linn timeline, forums] |
| 2025 (mid-year presentation) | Signal Linden presents progress; Linden Lab's stated plan at the time: Alpha Q1 2025, beta on Agni, **general availability scheduled for Q1 2026** | [Community report of Linden Lab statements — New World Notes] |
| 2025-03 | Alpha live on **Aditi** (beta grid); alpha regions SLua Yardang, SLua Tombolo, SLua Mesa, SLua Tideland; wiki documents the alpha, with revisions dated 2025-03-04/05 | [Verified — SLua wiki] |
| 2025-11 | SLua reaches a few regions on the main grid (Agni) | [Community report — Suzanna Linn timeline] |
| **2025-12-02** | **"Announcing the SLua Open Beta"** — Linden Lab's official post (published 2025-12-02T17:48:44Z; some secondary coverage dates it Dec 3 by timezone): SLua "now available in open beta on the production grid" — **in SLua Beta regions only** | [Verified — Linden Lab featured-news post] |
| 2026-07 | Community count: **at least 48 main-grid regions** run SLua (beta regions plus opted-in regions) | [Community report — Suzanna Linn, forums, 2026-07] |
| 2026-08-06 | Wiki's SLua page latest revision (Maggie Linden) — page still titled **"SLua Alpha"**, alpha warning intact | [Verified — SLua wiki] |
| 2026-10-07 / 2026-10-13 | Viewer build 26.4.0.37384746592, carrying Lua scripting support/editing, listed first as the Lua Editor RC/Beta viewer (Oct 7) and then as the **default viewer** (Oct 13) in Open Source User Group summaries | [Verified as reported — Modemworld viewer summaries] |
| 2026-10-26 | **No Linden Lab announcement of grid-wide availability exists.** SLua remains region-gated | [Verified — absence across official sources checked this date] |

### 2.2 What "open beta" actually means on the ground

The December 2025 post is the controlling official document, and its caveats are explicit:

- SLua is built on Luau and maintains compatibility with existing LSL knowledge; Linden Lab claims faster execution than LSL/Mono and approximately **50% less memory consumption**. **[Verified as Linden Lab's claims — blog, 2025-12-02]**
- New capabilities: tables, dynamic event subscription, multiple event handlers, coroutines, multiple timers, native JSON, optional type safety. **[Verified — blog, 2025-12-02]**
- **"SLua scripts will ONLY run in SLua Beta regions"** — they will not execute in standard regions. Existing LSL continues to work everywhere. **[Verified — blog, 2025-12-02]**
- The API is subject to change and content may need updates.
- Creators were advised to **wait until SLua launched grid-wide before listing SLua content on the Marketplace** — i.e., Linden Lab itself told creators not to treat beta as shippable.
- Linden Lab said it would expand availability as SLua stabilized, but gave **no grid-wide launch date**. **[Verified — blog, 2025-12-02]**

The named main-grid beta regions, per the current wiki revision, are: **Lua Beta Landing, Lua Beta Porridge, Lua Beta Eraserhead, Lua Beta Glass, Lua Beta Void, Lua Beta Anderson, Lua Beta Snausage (Adult), Lua Beta Nicoise, Lua Beta IsNeat.** Additionally, a private-region owner may ask Linden Lab Support (Live Chat or ticket) to move their region onto a Lua-enabled server channel; the wiki warns such regions may carry bugs or alpha features not present elsewhere. **[Verified — SLua wiki, revision 2026-08-06]**

### 2.3 The slipped timeline

The one official-ish GA target in the record — **Q1 2026 general availability**, stated in Linden Lab's 2025 community presentation as reported by New World Notes — did not happen. Q1 2026 came and went with SLua still confined to beta/opt-in regions, and no replacement date has been announced in any official source found as of 2026-10-26. Community expectation ("expected grid-wide this year," July 2026) is not a Linden Lab commitment and should not be planned against. **[Verified slip — 2025 target vs. 2026-10-26 state; Community report for the "this year" expectation]**

### 2.4 The naming mess is itself evidence

Linden Lab's surfaces currently disagree about what phase SLua is in:

- The official blog says **Open Beta** (2025-12-02).
- The wiki page is still titled **"SLua Alpha"**, and its latest revision (2026-08-06) still carries the alpha warning: instability is expected, Luau may crash regions, **"Memory and performance characteristics, and API specifics may change,"** and **"Scripts are currently being run in unoptimized form for development purposes."** **[Verified — SLua wiki, revision 2026-08-06]**
- Linden Lab's feedback board category is likewise still named **"SLua Alpha."** **[Verified — feedback.secondlife.com, checked 2026-10]**

Read together: the *service* graduated to a production-grid beta while the *documentation and stability posture* remain alpha-grade. Both halves matter. The beta is real — you can build and run SLua today in the places it runs. The warnings are also real — the API is not frozen and current performance is explicitly not the optimized final form, so present-day benchmarks (good or bad) do not describe the finished system.

### 2.5 Viewer-side state

Server gating is only half the picture; the editing toolchain has been converging on the standard viewer. SLua began behind a dedicated **Lua Project Viewer**. By October 2026 the situation had moved: Modemworld's week-41 summary (2026-10-12) listed viewer **26.4.0.37384746592** as the "Second Life Lua Editor RC / Beta viewer" (October 7), noting Lua scripting support on Lua-enabled regions, improved communication with Linden Lab's new VS Code extension, and an experimental Linux build; its week-42 Open Source User Group summary then listed the **same build as the Default viewer on October 13**, with "Lua scripting support (on regions with Lua scripting enabled)." The safe conclusion: by mid-October 2026, an SLua-capable viewer had been promoted to the default download, while **execution remained region-gated server-side** — viewer support must not be mistaken for grid-wide availability. **[Verified as reported — Modemworld, 2026-10-12 and 2026-10-17]**

Third-party viewer support is thinner. A hands-on alpha-period report noted Firestorm did not support SLua editing at that time. **[Community report — 2025]** Later claims of Firestorm SLua integration circulate mainly on low-quality content-farm pages and are **not** corroborated by Firestorm or Linden Lab sources found in this research. **[Unconfirmed — do not plan on Firestorm SLua tooling until Firestorm itself announces it.]**

---

## 3. The SLua programming model

### 3.1 Per-script runtime choice

Language and runtime are chosen **per script**, not per object or per region (beyond the region needing SLua support). The script editor in an SLua-capable viewer offers a compiler dropdown:

- **LSL: Legacy (LSO2)** — LSL on the old VM
- **LSL: Mono** — LSL on Mono
- **Lua** — native SLua source on the Luau VM
- **LSL: 2025 VM** — LSL source compiled to run on the Luau VM

The fourth option is strategically important (see §4.3): existing LSL can gain the new VM's size/memory/speed properties with **zero source changes**, in regions that support it. **[Verified — SLua wiki / Lua FAQ]**

### 3.2 The LSL API is mirrored, not replaced

SLua does not invent a new Second Life API. Linden Lab's functions move under an `ll` namespace, with names otherwise carried over:

```lua
-- LSL:  llSay(0, "Hello");  llSetPos(llGetPos() + <0, 0, 1>);
ll.Say(0, "Hello")
ll.SetPos(ll.GetPos() + vector(0, 0, 1))
```

The Lua FAQ states that **all LSL functions will be available in Lua**; where a function's types can be represented in LSL, events and functions are shared between the two languages, and more advanced object-oriented Lua APIs may exist as Lua-only wrappers over shared lower-level functions. **[Verified — Lua FAQ; creator portal "From LSL to SLua"]**

Second Life's value types get Luau-native representations:

- `vector(x, y, z)` and `rotation(x, y, z, s)` constructors replace LSL's `<...>` literals; vector/rotation arithmetic works as LSL scripters expect.
- Keys are a distinct `uuid` type.
- A special 32-bit `integer` type exists **strictly for compatibility** with LSL APIs where integer identity matters (e.g. `ll.SetPrimitiveParams` parameter lists). It has no literal form in SLua; ordinary numbers are the performant default, and bitwise operations are available through the `bit32` library — a superset of LSL's integer operations, including logical shift-right and rotates. **[Verified — Lua FAQ]**
- One migration gotcha: not every LSL predefined constant arrives as a ready-made global in the way LSL scripters assume; portal examples show constants such as `PERMISSION_TAKE_CONTROLS` and `PRIM_ROT_LOCAL` being defined or cast explicitly. Read the SLua reference for the exact constant surface rather than assuming LSL's globals all carry over verbatim. **[Verified — creator portal examples]**

### 3.3 Data: tables, native JSON, buffers

The deepest quality-of-life change is data modelling:

- **Tables replace lists.** Luau tables (`{}`) serve as both arrays and dictionaries/maps, ending LSL's parallel-list and strided-list patterns. Indexing is **1-based**, as in Lua — the single most common trip-up for LSL converts. **[Verified — creator portal]**
- **Native key–value structure in-script**, separate from and in addition to LinksetData (§4.2).
- **Native JSON.** A modified lua-cjson implementation is exposed as `lljson.encode(table)` / `lljson.decode(text)`. Vectors and quaternions serialize as strings and convert back with `tovector` / `toquaternion`; buffers encode as Base64. For anything that talks JSON to an outside system — which is to say, for Heimdall bridge payloads — this removes LSL's most painful string surgery. **[Verified — Lua FAQ / creator portal]**
- **Binary data.** SLua strings are UTF-8, and Luau's mutable `buffer` class supports byte packing. `llbase64.encode` / `llbase64.decode` handle both strings and buffers (the latter via a flag), unlike LSL's string-only Base64 functions. **[Verified — Lua FAQ]**
- Namespace conventions are Linden-flavoured and slightly irregular: the main library is lowercase `ll` (`ll.Say`), the event and timer systems are `LLEvents` and `LLTimers`, and utility libraries such as `lljson` and `llcompat` are lowercase. **[Verified in part — blog/FAQ/portal; namespace inventory per Community report — Nexii Malthus, forums, 2025-12]**

### 3.4 Events: dynamic subscription replaces the state machine

LSL's `default { state_entry() … }` state-machine structure is not SLua's primary model. Handlers are ordinary functions registered — and removable — at runtime through `LLEvents`:

```lua
LLEvents:on("touch_start", function(events) ... end)
LLEvents:once("dataserver", handler)          -- one-shot
LLEvents:off("touch_start", handler)           -- unsubscribe
```

Multiple handlers for the same event can coexist. Event payloads may be typed objects rather than LSL's parallel scalar arguments — portal examples show handlers receiving `{DetectedEvent}` values with methods such as `event:getName()`. The familiar event names carry over (`touch_start`, `touch`, `touch_end`, `collision`, `listen`, `dataserver`, `control`, and so on), and Linden Lab's reference pages for SLua events mirror their LSL counterparts — though the portal is visibly a work in progress, some examples still use scalar signatures, and many reference pages still carry placeholder sections. Documentation incompleteness is itself a readiness signal (§6). **[Verified — creator portal "Events" and SLua reference]**

Timers likewise escape LSL's single-timer constraint: `LLTimers:every(seconds, fn)` and `LLTimers:once(seconds, fn)` support multiple independent timers, and official material describes built-in **lag protection**. **[Verified — creator portal / blog]**

**Coroutines** complete the asynchronous picture: multi-step flows (issue an HTTP request, await the reply, continue) can be written as straight-line code that yields and resumes instead of being shredded across state changes. One dating nuance: an older revision of the Lua FAQ said the coroutine library was planned but likely not in the initial release; by the December 2025 open beta, Linden Lab's own feature list includes coroutines. The feature arrived during the beta's evolution — a reminder to date every capability claim about a moving target. **[Verified — blog 2025-12-02; SLua wiki coroutine examples; older FAQ revision superseded]**

Error handling uses Luau's `pcall` protected calls; notably, the FAQ points out that even allocation failures can be caught through `pcall`, giving scripts a graceful path LSL never offered. **[Verified — Lua FAQ]**

### 3.5 Deliberate omissions

SLua is a curated dialect, not stock Luau with an `ll` library bolted on:

- `getfenv()`, `setfenv()`, and `_ENV` are inaccessible — they obstruct the optimizations Linden Lab wants. **[Verified — Lua FAQ]**
- `loadstring()` is unsupported: repeatedly compiling strings is expensive and can stall a script in ways the scheduler cannot fairly interrupt. There is no runtime code generation in SLua scripts. **[Verified — Lua FAQ]**
- SLua runs **server-side, in regions only**. It is a different effort from Linden Lab's viewer-side Lua/addon work, which community reporting describes as announced earlier, then frozen — do not conflate the two when reading older announcements. **[Verified — Lua FAQ; Community report for the viewer project's frozen state]**
- The FAQ says a real debugger was desired but would not be in the initial release; no newer source found in this research proves a shipped SLua debugger. The practical debugging story today is the VS Code extension's error tracking plus in-world debug output. **[Verified as of FAQ; absence noted 2026-10-26]**

### 3.6 Memory and CPU versus LSL/Mono — with an honest tension

Three Linden Lab statements must be held together, because they do not perfectly agree:

1. The open-beta announcement claims SLua uses approximately **50% less memory** than LSL/Mono and executes faster. **[Verified as Linden Lab's claim — blog, 2025-12-02]**
2. The current SLua wiki text advertises a **higher memory limit of 128 KB** for SLua scripts (against LSL/Mono's familiar 64 KB ceiling). **[Verified — SLua wiki, revision 2026-08-06]**
3. The Lua FAQ, answering "are we going to get a higher memory limit?", said **"Not at first"** — arguing that Luau's frugality means the cap is missed less, that Lua offers precise memory control (catchable allocation errors, the `buffer` class), and that Linden Lab was interested in more memory for designated high-priority scripts but had no specific plans at that time. **[Verified — Lua FAQ]**

The reasonable reading is that (3) is the earlier design position and (2) the later shipped beta posture — but Linden Lab has not, in sources found here, published one consolidated quota document reconciling them, and the wiki simultaneously warns that scripts run **unoptimized** and that memory/performance characteristics **may change**. Heimdall should therefore treat "128 KB and roughly half the footprint" as the current beta's advertised shape, not as a frozen guarantee, and should not architect companion scripts that *require* more than LSL's limits until the quotas are documented as stable. **[Inference from the dated sources above]**

### 3.7 Tooling

- **Linden Lab's official VS Code extension** (open source: `github.com/secondlife/sl-vscode-plugin`) is the centrepiece: syntax highlighting, preprocessing, Lua `require` inclusion for multi-file projects, error tracking, and tracking of object chat and debug output inside the IDE, with automatic language updates. In December 2025 it was distributed via GitHub, with marketplace publication promised "soon"; no source found in this research proves the marketplace listing has landed. Community reports add that the SLua-capable viewer connects to the extension and pushes it the latest API definitions, and that SLua source files use the `.luau` extension. **[Verified — blog 2025-12-02 and extension repository; Community report — forums, 2025-12, for viewer↔extension definition sync]**
- **The in-viewer editor** (project viewer originally; the 26.4 viewer line by October 2026, §2.5) remains the compile-and-run path, including the per-script compiler dropdown of §3.1.
- **Roblox-heritage tooling mostly does not transfer.** Upstream Luau's ecosystem — its language server, type checker, linters — understands *Luau the language*, not Second Life's `ll.*` API, `LLEvents`, or SL-specific types like `vector` and `uuid`. The authoritative SL-aware surface is Linden Lab's own extension and creator portal. Do not assume a generic Luau language server can type-check SLua against the SL API unless a future source proves that integration. **[Inference — from the documented scope of each tool]**
- Type checking in practice: annotations are optional and appear throughout Linden Lab's portal examples (e.g. handler parameters typed `string`, `number`, `{DetectedEvent}`), so annotated, checked code is the documented style — but nothing forces it, and unchecked dynamic code is equally legal SLua. **[Verified — creator portal examples]**

---

## 4. Interop: LSL and SLua together

### 4.1 Coexistence

- **Within one script: no mixing.** A single script is either LSL or Lua source; there is no inline interleave. **[Verified — Lua FAQ]**
- **Within one object/linkset: yes.** The FAQ's answer is explicit: "If your object has multiple scripts, you can mix LSL and Lua within the same object." Combined with per-script runtime selection (§3.1), a single linkset can simultaneously hold legacy LSL/Mono scripts, LSL scripts compiled for the 2025 VM, and native SLua scripts — subject to the region supporting each runtime. **[Verified — Lua FAQ]**

### 4.2 Communication channels

**Link messages.** SLua documents `ll.MessageLinked` with semantics mirroring LSL's `llMessageLinked` — an integer, a string, and an ID delivered to scripts across the link set. A community guide (Suzanna Linn's SLua guide, summarized in a forum thread dated 2025-09-04) states explicitly that an LSL script and an SLua script in the same prim exchange linked messages. Grading honestly: the shared mechanism and API surface are official; the explicit cross-language delivery sentence is community-documented and consistent with the architecture (one simulator linkset event system underneath both runtimes) rather than a verbatim Linden Lab guarantee captured in this research. **[Verified API — creator portal SLua reference; Community-confirmed cross-language delivery — forums, 2025-09-04]**

**Chat.** Both languages expose the same chat/listen model — `ll.Listen` and the `listen` event, with identical channel, speaker-name/ID, and message semantics — so LSL and SLua scripts can always fall back to ordinary chat channels wherever both are running, subject to normal chat rules and throttles. Inside one linkset, link messages are the better tool; chat is the cross-object fallback. **[Verified — creator portal SLua reference]**

**LinksetData — the shared state bus.** LinksetData is a linkset-scoped key–value store that lives as a prim property, not in any script's memory: it persists through script deletion/reset and object cloning; writes fire the `linkset_data` event in **all** scripts running in the linkset; protected pairs are available via passphrase-guarded variants. Its documented capacity in the current wiki revision (2026-09-10) is **131,072 bytes (128 KiB)**. SLua exposes the same store (`ll.LinksetDataRead` and family) — Linden Lab's SLua feedback board carries active discussion of its exact semantics, which proves the surface exists and is being exercised, while also showing details still being polished. Community guide evidence likewise reports mixed LSL/SLua objects sharing the object's LinksetData. Because values are strings, structured payloads should be JSON — trivial in SLua via `lljson`, more laborious in LSL, and a strong argument for defining payload schemas once, in a language-neutral document. **[Verified — LSL Linkset Data wiki, revision 2026-09-10; SLua surface per Linden Lab feedback board, 2026]**

### 4.3 Migration paths for LSL code

There are exactly three, and they must not be conflated:

1. **Do nothing.** Existing LSL works everywhere, today and for the foreseeable future; Linden Lab repeats that LSL is here to stay. This remains the only path that functions in **every** region.
2. **Recompile LSL for the 2025 VM.** Keep the source in LSL; select **LSL: 2025 VM** so it runs on the Luau VM, gaining the smaller bytecode / lower memory / speed properties in SLua-enabled regions. The FAQ says there were no plans for *transparent automatic* migration — the scripter chooses and recompiles. Community reporting describes a longer-term direction in which all LSL is eventually recompiled to the Luau VM automatically and Mono disappears, on a horizon of "years"; the verified kernel of that claim is only the FAQ's statement that phasing out Mono is the eventual idea. **[Verified — Lua FAQ; Community report for the auto-recompile horizon]**
3. **Hand-port to native SLua.** Mostly mechanical translation (`llFoo` → `ll.Foo`), with real rewrites where SLua's model pays: LSL states become dynamic event handlers and coroutines; parallel lists become tables/maps; hand-built JSON strings become tables plus `lljson`; single-timer workarounds become `LLTimers`. Community tooling exists — Suzanna Linn's guide includes transpiler work — but it is unofficial, and Linden Lab has promised no official converter: the FAQ says one might be made someday, with no specific plans. **[Verified — Lua FAQ; Community tooling noted]**

---

## 5. What SLua changes for Heimdall

Everything in this section is **[Inference]** — engineering assessment built on the verified facts above, for the parent design work to accept or reject.

### 5.1 Where SLua does *not* apply

Heimdall's body is a headless client: it logs in, moves, perceives, and speaks through the simulator protocol via a client library (LibreMetaverse lineage), with the mind bridged behind it (docs 01–05 of this series). No in-world script performs those functions, in either language. SLua neither replaces nor accelerates the login/protocol/perception stack, and nothing in the SLua rollout relaxes the scripted-agent compliance posture established in doc 01 (Scripted Agent flagging, message governance). Anyone who pitches "rewrite Heimdall in Luau" has confused the companion layer with the body.

### 5.2 Where SLua *does* apply: the companion layer

The in-world artefacts Heimdall will plausibly own or wear:

- **Avatar-worn HUDs/attachments** that sense and act at the avatar's location (local listeners, interaction prompts, state displays);
- **Companion objects** — bridge endpoints rezzed in-world that expose HTTP-in URLs, relay chat, cache state in LinksetData, and translate between the simulator and Heimdall's external bridge;
- **Control/relay objects** in avatar-control ecosystems (the RLV research in this series covers that world in depth; note only that RLV's pathways are the ordinary chat/owner-say channels, which SLua's `ll.*` parity represents natively).

### 5.3 The case for SLua in that layer — eventually

Where it runs, SLua is simply the better tool for a long-lived companion codebase:

- **State without pain.** Tables/maps replace strided lists for the companion's session state, permission caches, and conversation bookkeeping.
- **Bridge payloads become native.** The companion's whole job is shuttling structured data between the simulator and an external JSON-speaking bridge. `lljson` turns that from string surgery into table passing; `buffer` + `llbase64` cover binary-ish payloads.
- **Concurrency that reads like the behaviour.** Multiple `LLTimers`, dynamic `LLEvents:on/off` subscription (listen only when a conversation is live), and coroutines for multi-step asynchronous flows (request → await → respond) map directly onto companion behaviour that in LSL requires state-machine contortions.
- **Headroom.** An advertised 128 KB script memory and roughly half the per-script footprint mean fewer scripts per companion object and less pressure against region script budgets — subject to the §3.6 caveat that quotas are beta-advertised, not frozen.
- **Maintainability.** Optional type annotations, `require`-based multi-file organization in the official editor toolchain, and a real (if young) VS Code workflow beat LSL's single-file, untyped, in-viewer-only tradition for a codebase expected to live for years.

### 5.4 The case against SLua today — one fact dominates

**An SLua script does not execute in a standard region.** Yrsa's avatar will roam the whole grid; Linden Lab's own beta caveat says SLua scripts run only in SLua Beta regions (plus Aditi test regions and opted-in private regions). A companion attachment whose brain is SLua would be inert — present but silent — across most of the world, and the failure mode is absence, not error. Presence-critical and safety-critical companion functions cannot be built on a substrate that vanishes by geography. Add the unfrozen API, the "unoptimized form" warning, placeholder-grade reference documentation, and Linden Lab's own advice to keep SLua content off the Marketplace until grid-wide launch, and the conclusion writes itself for the baseline layer.

### 5.5 Recommended architecture (for the design docs to ratify)

1. **LSL baseline layer, now.** Small, stable, grid-wide scripts implement every essential companion function: sensing, relaying, HTTP-in endpoints, LinksetData state. They must work in every region Yrsa can enter.
2. **Optional SLua enhancement layer.** In SLua-enabled regions (our own opted-in home region is the natural first habitat), SLua modules may replace or augment LSL modules where the richer model pays — complex state, JSON-heavy bridging, concurrent interactions.
3. **Language-neutral contracts.** Link-message numbers, LinksetData keys, JSON payload schemas, and fallback chat channels are specified independently of implementation language, so either layer can serve a contract and the external bridge cannot tell which language answered.
4. **Graceful degradation as a hard rule.** Entering a non-SLua region must never strand the avatar without companion function: the LSL baseline is always resident; SLua modules feature-detect and stand down silently.
5. **Prototype where it's safe.** SLua experiments belong on Aditi and in beta/opt-in regions, against the same contracts, so the eventual port is a module swap rather than a redesign.

---

## Readiness verdict

**Verdict: build the companion baseline in LSL today; prototype SLua in beta/Aditi or an opted-in private region; treat SLua as strategically inevitable but operationally beta and region-gated as of 2026-10-26.**

Linden Lab's direction is not in doubt: Luau is the chosen runtime, LSL is slated to run on it via the 2025 VM, and Mono's phase-out is the stated long-term idea. What is in doubt is only the calendar — and the calendar has already slipped once (Q1 2026 GA, per Linden Lab's 2025 presentation, did not occur). Heimdall loses nothing by waiting: LSL carries the companion layer everywhere today, the interop story (§4) means SLua modules can join an existing LSL object later without disturbing it, and the contracts-first design in §5.5 keeps the port mechanical.

**Watching brief — the signals that SLua is ready to build on.** Re-run this assessment when several of these land:

1. Linden Lab announces **general availability** or a grid-wide server rollout — not merely more beta regions.
2. SLua scripts execute in **standard regions** without a special Lua server channel or a support-ticket opt-in.
3. The official caveat "SLua scripts will ONLY run in SLua Beta regions" is removed or superseded.
4. Linden Lab lifts its advice to wait for grid-wide launch before **Marketplace** listing of SLua content.
5. The wiki's alpha warning is retired — specifically the region-crash warning, "API specifics may change," and "scripts are currently being run in unoptimized form."
6. An **API freeze or versioned SLua API** is published.
7. The optimized runtime ships and memory/CPU quotas are documented in one consistent place, resolving the 128 KB vs. "not at first" tension (§3.6).
8. The default viewer **and** major third-party viewers (Firestorm above all) create/edit/compile SLua without a special project viewer.
9. Creator-portal reference pages lose their placeholder sections and explicitly document cross-runtime link messages, SLua LinksetData functions/events, HTTP functions and limits in SLua, `require`/module behaviour in-world, and debugging/error reporting.
10. Official migration tooling appears, or Linden Lab begins transparently recompiling LSL scripts to the 2025 VM; a **Mono phase-out schedule** is announced.
11. Production-scale community use appears outside Linden Lab's own beta regions, with the feedback board graduating from its "SLua Alpha" name.

Until then, SLua is a watching brief and a prototyping track — promising, Linden-backed, and not yet the ground Heimdall should stand on.

---

## Sources

Linden Lab (official):

- "Announcing the SLua Open Beta: Modern Scripting Comes to Second Life" — Linden Lab featured news, published 2025-12-02 — https://community.secondlife.com/news/featured-news/announcing-the-slua-open-beta-modern-scripting-comes-to-second-life-r11237/
- Lua FAQ — Second Life Wiki (why Lua/Luau; C# and Mono history; interop and migration answers) — https://wiki.secondlife.com/wiki/Lua_FAQ
- SLua Alpha — Second Life Wiki (alpha/beta status, beta region list, private-region opt-in, 128 KB note, unoptimized-form warning; latest revision 2026-08-06) — https://wiki.secondlife.com/w/index.php?title=SLua_Alpha&diff=cur&oldid=1218000
- Try SLua — Second Life Wiki — https://wiki.secondlife.com/wiki/Try_SLua
- Luau Examples — Second Life Wiki — https://wiki.secondlife.com/w/index.php?title=Luau_Examples&oldid=1218013
- LSL Linkset Data — Second Life Wiki (128 KiB store, persistence, `linkset_data` event; revision 2026-09-10) — https://wiki.secondlife.com/wiki/LSL_Linkset_Data
- Creator portal: "From LSL to SLua" — https://create.secondlife.com/script/learn-slua/from-lsl/
- Creator portal: SLua "Events" — https://create.secondlife.com/script/learn-slua/events/
- Creator portal: SLua reference, `ll.MessageLinked` — https://create.secondlife.com/script/slua-reference/functions/llmessagelinked/
- Creator portal: LSL reference, `llLinksetDataWrite` — https://create.secondlife.com/script/lsl-reference/functions/lllinksetdatawrite/
- Second Life VS Code extension (official, open source) — https://github.com/secondlife/sl-vscode-plugin and releases at https://github.com/secondlife/sl-vscode-plugin/releases
- SLua feedback board (still named "SLua Alpha"; LinksetData semantics discussion) — https://feedback.secondlife.com/slua-alpha/p/allow-lllinksetdataread-to-return-nil-when-the-key-pair-does-not-exist-in-the-da

Luau the language:

- Luau official site (language, typing, performance, sandboxing, compatibility) — https://luau.org:443/
- "Luau (programming language)" — Wikipedia (2019 origin, 2021 open-sourcing, adoption in *Alan Wake 2*, *Warframe*, *Farming Simulator 2025*) — https://en.wikipedia.org/wiki/Luau_(programming_language)

Community and secondary reporting (used only where tagged):

- Modemworld / Inara Pey, "2026 SL viewer release summaries week #41" (Lua Editor viewer 26.4, 2026-10-07) — https://modemworld.me/2026/10/12/2026-sl-viewer-release-summaries-week-41/
- Modemworld / Inara Pey, "2026 week #42 SL Open Source UG summary" (26.4 as default viewer, 2026-10-13) — https://modemworld.me/2026/10/17/2026-week-42-sl-open-source-ug-summary/
- New World Notes reporting of Linden Lab's 2025 presentation (Alpha Q1 2025; GA scheduled Q1 2026), via Patreon mirror — https://www.patreon.com/u4843285/posts/second-life-new-140850270
- Second Life forums, Suzanna Linn SLua guide/transpiler thread (mixed-language objects exchanging link messages and LinksetData; announcement/deployment timeline; ~48 main-grid regions, 2026-07) — https://community.secondlife.com/forums/topic/526933-website-with-slua-guide-examples-and-transpiler/
- Jelena Kiranov, "I Tried SLua Alpha" (hands-on; Firestorm lacking SLua support in the alpha period) — https://jelenakiranov.com/slua-alpha-second-life-lua-scripting/
- Hacker News discussion of SLua (community sentiment; not used for facts) — https://news.ycombinator.com/item?id=45298575
