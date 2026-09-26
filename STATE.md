# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Completed phases: 1 through 13
- Package version: `1.0.0`
- Phase 13 real-agent verification: complete
- Formal Git tag / GitHub Release: `v1.0.0` published

## v1.0 verification

The v1.0 gate passed in a real Codex project on the N100.

Scenario A verified a normal task with no human decision:

- Codex discovered the monitor integration without monitor commands in the user prompt
- Codex read monitor status, started and completed the task, recorded one meaningful progress event, ran project validation, and registered a reviewable artifact
- no unnecessary human question was created
- the human did not issue task/progress/ask commands on the agent's behalf

Scenario A initially exposed a real onboarding defect: the Codex PowerShell did not have the uv tool bin directory on PATH. Version 0.12.1 added a documented executable-path fallback and regression tests; the repeated scenario passed.

Scenario B verified a genuine human decision and independent continuation:

- the human answered from the monitor dashboard
- an independent Codex execution with no inherited conversation history recovered the stored answer from monitor state
- the selected answer was `A（短いエラーを保つ）`
- Codex inspected the relevant script/tests, found that the selected behavior was already implemented, preserved it, ran the relevant verification successfully, recorded progress, and completed the task
- the final monitor state had no current task and no unanswered question
- no manual database edit or human-issued monitor task/progress/ask command was required

The detailed evidence is in `PHASE13_VERIFICATION.md`.

## Verified capabilities

- local dashboard and SQLite state
- progress, current task, human questions and browser answers
- artifact snapshots and project-specific metrics
- reusable installed CLI with per-project isolation
- Tailscale tailnet-only smartphone access
- per-user Windows logon startup
- machine-readable read-only `monitor status`
- Codex and generic agent onboarding
- real Codex workflow with iPhone human answer and transcript-independent continuation

The standard-library test suite contains 72 tests. Cross-platform CI runs on Windows and Ubuntu with Python 3.10 and 3.12.

## Production deployment note

The original monitor deployment remains separate from project-specific remotes.

- production backend: `127.0.0.1:8765`
- production Tailscale HTTPS port: `9443`
- production tailnet URL: `https://leto.taile04360.ts.net:9443/`
- production automatic startup is not configured

## Formal v1.0.0 release

The annotated `v1.0.0` tag and GitHub Release are published. The tagged release commit passed the release gate, and the working tree was confirmed clean during publication.

`RELEASE_NOTES_v1.0.0.md` contains the release notes.

Phase 14 has not started.

## Post-v1 roadmap

After the formal v1.0.0 release:

- Phase 14: diagnostics and recovery
- Phase 15: durable notifications
- Phase 16: runner lifecycle and automatic resume
- Phase 17: multi-project registry
- Phase 18: automatic telemetry
- Phase 19: second-agent portability verification

## Handoff instruction

A new assistant or developer should read, in this order:

1. `README.md`
2. `IMPLEMENTATION_PLAN.md`
3. `ARCHITECTURE.md`
4. `STATE.md`
5. `AGENT_INTEGRATION.md`
6. `PHASE13_VERIFICATION.md`

Do not add post-v1 features unless their scope is requested.
