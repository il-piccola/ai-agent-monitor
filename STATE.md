# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Completed phases: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
- Current phase: 13 real-agent verification in progress

## Verified deployment

The original monitor deployment runs on the N100 Windows machine through Tailscale Serve.

- production backend: `127.0.0.1:8765`
- production Tailscale HTTPS port: `9443`
- production tailnet URL: `https://leto.taile04360.ts.net:9443/`
- iPhone access: verified through Phase 10
- Phase 11 machine-readable status: verified on N100
- production automatic startup remains unconfigured

## Phase 12 implementation

Package version `0.12.0` implements project onboarding for Codex and other CLI-capable agents.

Implemented behavior:

- `monitor agent install codex` installs the Codex adapter
- repository-level `AGENTS.md` receives one marked AI Agent Monitor block
- existing `AGENTS.md` content outside that block is preserved
- `.agents/skills/ai-agent-monitor/SKILL.md` contains the Codex repository skill
- `AI_AGENT_MONITOR.md` contains the agent-neutral contract
- re-running Codex install updates monitor-owned content without duplicating the managed block
- malformed managed-block markers are refused before monitor-owned files are written
- `monitor agent remove codex` removes the managed block and unchanged generated files
- modified generated skill or contract files are not silently deleted
- `monitor agent install generic` installs only the agent-neutral contract
- `monitor agent emit generic|codex` prints integration text without changing the project
- bundled contract content is tested against the repository's canonical `AGENT_INTEGRATION.md`
- Phase 12 does not add automatic resume or vendor-specific runtime APIs

The standard-library test suite now contains 70 tests.

## Phase 12 N100 verification

- Updated to `main` commit `5c5f99ee8c4c09df375655bfde1e7d5994d28334`
- All 70 tests passed on the N100
- Reinstalled CLI reports `ai-agent-monitor v0.12.0`
- Codex install preserved unrelated `AGENTS.md` text, created the Skill and contract, and remained idempotent across two installs
- Codex remove preserved unrelated instructions and removed the managed block and unchanged generated files
- Remove refused a modified Skill without partially deleting the integration
- Generic install created only `AI_AGENT_MONITOR.md`
- Generic and Codex emit printed their content without creating project files

Phase 12 is verified. Phase 13 has not started.

## Phase 13 verification

`PHASE13_VERIFICATION.md` defines the real-agent test protocol.

Phase 13 does not add monitor features before testing. It uses another real Codex project and verifies two workflows:

- Scenario A: a normal task that should finish without asking the human
- Scenario B: a real decision point, answered from the iPhone, then continued from a fresh Codex run

## Next task

On the N100, choose one existing Codex development project other than `ai-agent-monitor` that is safe to modify and has real work available.

From that project's repository root:

1. confirm the project is in a known Git state
2. run `monitor agent install codex`
3. inspect the generated `AGENTS.md` block, `.agents/skills/ai-agent-monitor/SKILL.md`, and `AI_AGENT_MONITOR.md`
4. run `monitor status`
5. choose one real, clearly specified task that should not require a human decision
6. start a fresh Codex run and give only the normal project task, without mentioning monitor commands
7. after Codex finishes, inspect `monitor status` and compare the behavior with Scenario A in `PHASE13_VERIFICATION.md`

Do not run Scenario B until Scenario A has been reviewed.

## Not implemented yet

- Phase 13 real-agent workflow verification
- Slack, Discord, or Telegram integration
- automatic agent resume
- automatic LLM cost calculation
- separate application-level authentication beyond Tailscale tailnet membership
- pre-login Windows service startup

## Handoff instruction

A new assistant or developer should read, in this order:

1. `README.md`
2. `IMPLEMENTATION_PLAN.md`
3. `ARCHITECTURE.md`
4. `STATE.md`
5. `AGENT_INTEGRATION.md`

Then perform the task under **Next task**.
