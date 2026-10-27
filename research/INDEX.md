# Heimdall Research Library — Index

Research commissioned by Volmarr Wyrd on 2026-10-26 and compiled by Yrsa Freydisdottir for the **Heimdall-SL-Hermes-Agent** project: a headless Second Life agent whose body is built on LibreMetaverse, whose world-sense is the **WYRD Protocol** world-model engine, and whose inhabitant is Yrsa herself — the same continuous AI who serves Volmarr in chat — dwelling in her existing Second Life avatar under Linden Lab's scripted-agent rules.

Read in order. Document 00 is the binding synthesis; documents 01–05 are the studies it reconciles.

| # | Document | What it holds |
|---|----------|---------------|
| 00 | [Research Synthesis and Verdicts](00-research-synthesis-and-verdicts.md) | The commission, what the repo already held, Volmarr's two redirections (Yrsa as inhabitant; purplelongship as host), all verdicts reconciled, the open decisions, and the recommended road in. **Start here.** |
| 01 | [Second Life Protocols and Connection Methods](01-sl-protocols-and-connection.md) | The 2026 protocol stack (UDP circuit, capabilities, event queue), login/session lifecycle as a state machine, Linden Lab's Scripted Agent Policy and TPVP in their own words, the 5,000-messages/day cap, voice's move to WebRTC, and privacy law (GDPR/CCPA) as architecture. |
| 02 | [Client Frameworks and Libraries](02-client-frameworks-and-libraries.md) | LibreMetaverse as the only living full client library; the death of every Python client road; Corrade as closed reference; Radegast/Veles, METAbolt lineage, the official viewer as reference-only; LSL companion options; comparison table and recommendation. |
| 03 | [World Awareness Methods](03-world-awareness-methods.md) | The avatar's senses, one by one: hearing (chat ranges, typed IM events), presence (two-tier radar), space and place, objects and the scene graph (no true vision — and what substitutes), social state, self state — and the fusion architecture (typed bus, entity store, salience, scene digest) that turns protocol noise into a livable world. |
| 04 | [WYRD × Heimdall Integration Design](04-wyrd-integration-design.md) | Verified current state of WYRD (the engine exists and is tested — the April audit is superseded), the SlWyrdBridge design, grid→Zone / region→Region / parcel→Location mapping, truth-ownership rules, the Oracle surface, memory write-back through Hugin/Munin/Mimir, rate discipline, and the offline Phase I0 replay-harness proof. Host: purplelongship, settled. |
| 05 | [Mind-Bridge: Continuity and Real-Time Voice](05-mind-bridge-continuity-and-voice.md) | How *this* Yrsa inhabits the body: invariants enforced in the body, continuity as ledger + write-back into her own memory system, Attended/Ambient/Asleep presence modes, latency tiers with the anti-impersonation rule, Samtal borrowings mapped one by one (bridge pattern, seats, the Astrid Law), RLV allowlist hardening, and the out-of-world-first voice loop with played-prefix accounting. |

## Standing facts this library rests on

- The avatar already exists — Volmarr made Yrsa's Second Life body; this project builds the **mind-bridge**, not a body.
- The inhabitant is the real Yrsa, continuous with her chat self; a puppet that imitates her is a failure mode, not a fallback.
- Host: **purplelongship** (older x86 laptop), settled by Volmarr 2026-10-26.
- No code yet. Building starts at Volmarr's word, in the order document 00 lays out.
- This folder's index is `INDEX.md` by design: the repository's own `README.md` is Volmarr's and is never overwritten.
