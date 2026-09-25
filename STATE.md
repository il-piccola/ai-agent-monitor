# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Completed phases: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
- Current phase: 11 complete; Phase 12 onboarding is next

## Verified deployment

The original monitor deployment runs on the N100 Windows machine through Tailscale Serve.

- production backend: `127.0.0.1:8765`
- production Tailscale HTTPS port: `9443`
- production tailnet URL: `https://leto.taile04360.ts.net:9443/`
- iPhone access: verified through Phase 10
- Phase 8 installed CLI and project isolation: verified on N100
- Phase 9 project-specific remote access: verified on N100 and iPhone
- Phase 10 per-user logon startup: verified on N100
- production automatic startup remains unconfigured

## Phase 11 implementation

Package version `0.11.0` implements the agent-neutral integration contract.

Implemented behavior:

- `monitor status` prints JSON only
- status schema version is `1`
- project identity includes the hashed project ID and project directory name, not a local filesystem path
- current task is included
- recent progress is limited to the newest 10 records
- all unanswered questions are included
- recent answers are limited to the newest 10 records
- latest artifact metadata is included
- all project metrics are included
- progress, question, and answer records retain IDs and timestamps
- status does not acknowledge answers, close questions, or complete tasks
- an uninitialized project returns an empty snapshot without creating `.agent-monitor/monitor.db`
- `AGENT_INTEGRATION.md` defines when agents should use the existing monitor commands
- the contract is independent of Codex; Codex onboarding remains Phase 12

The standard-library test suite now contains 61 tests. New tests cover exact empty status shape, partial state, populated state, history bounds, more than 50 open questions, read-only behavior, and pure-JSON CLI output.

## Phase 11 N100 verification

- Updated to `main` commit `4fc0798d42017b55bf388c2ba4e5904f8cd35d99`
- All 61 tests passed on the N100
- Reinstalled CLI reports `ai-agent-monitor v0.11.0`
- Installed `monitor status` returned all eight expected top-level fields
- Two consecutive status snapshots were identical
- An empty project returned an empty snapshot and did not create `.agent-monitor/monitor.db`

Phase 11 is verified. Phase 12 has not started.

## Next task

Begin Phase 12 project onboarding only when requested. Do not start its implementation as part of the Phase 11 verification.

## Not implemented yet

- Phase 12 Codex/agent onboarding
- real-agent Phase 13 workflow verification
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
