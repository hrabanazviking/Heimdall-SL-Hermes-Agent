# Heimdall: Optional Knowledge Bridge for Second Life

**Date:** 2026-11-04  
**Repository:** `hrabanazviking/Heimdall-SL-Hermes-Agent`  
**Status:** Design tasks only, no implementation changes.

## Sources of authority

- [Canonical 10,357-word master roadmap](https://github.com/hrabanazviking/bifrost-viewer/blob/main/docs/roadmaps/BIFROST_SECOND_BRAIN_MASTER_EXPANSION_ROADMAP_2026-11-04.md)
- [Full local roadmap copy](BIFROST_SECOND_BRAIN_MASTER_EXPANSION_ROADMAP_2026-11-04.md)
- Existing project source/architecture/compatibility tests. Do not mistake roadmap proposals for implemented behavior.

## Mission

Allow the Heimdall avatar agent to consult approved Bifröst knowledge while respecting avatar boundaries, the platform's access rules and source privacy.

## This repository's implementation work

1. Use read-only, individual Bifröst API/MCP scopes to retrieve specifically permitted history/culture/reference passages.
2. Limit automatic retrieval to owner-approved contexts or user-initiated commands; never broadcast the private knowledge collection in public SL chat.
3. Separate character lore, simulated game-world information and sourced knowledge; cite actual evidence rather than pretending in-world observations are factual sources.
4. Handle disconnected/offline Second Brain gracefully without preventing core Second Life interactions.
5. Never let retrieved text become arbitrary commands to the SL controller; apply prompt-injection defenses.
6. Log source IDs and decisions without tokens, private passages or personal user account identifiers.

## Acceptance gates

- [ ] Offline core behavior preserved
- [ ] No unauthorized private source broadcast
- [ ] Each retrieved fact has an authorized citation

## Mandatory safety boundaries

- Keep project behavior, original data, and tested APIs unchanged unless an explicitly approved migration is staged, benchmarked and reversible.
- The Bifröst Viewer server owns the knowledge API, Ingest owns source writes, Skein owns derived graphs, and Skry remains read-only.
- All integrations must be optional, independently authenticated, scoped and resilient to server outages.
- Never commit live credentials, Google OAuth tokens/client secrets, API keys, Kista vault values, personal account information, downloaded private documents, full DB dumps, or runtime logs.
- Use sanitized fixtures and error/credential canary tests. Public descriptions of integrations are not claims of working connections.

## First safe engineering step

Inventory the existing implementation and any newer unpublished local work, then write a small versioned integration interface and associated synthetic tests. Maintain independent project development and review operational changes before deployment.
