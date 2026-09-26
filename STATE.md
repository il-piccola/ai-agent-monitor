# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Formal release: `v1.0.0` published
- Development package version: `1.1.0`
- Completed phases: 1 through 14
- Current phase: Phase 14 verified on N100; Phase 15 not started

## v1.0 baseline

The formal `v1.0.0` tag and GitHub Release are published from the verified Phase 13 baseline.

The v1.0 gate includes the real Codex workflow with iPhone answering and transcript-independent continuation. See `PHASE13_VERIFICATION.md` and `RELEASE_NOTES_v1.0.0.md`.

## Phase 14 verification

Version `1.1.0` adds read-only project diagnostics through:

```text
monitor doctor
monitor doctor --json
```

N100 verification passed:

- all 84 tests passed
- installed CLI version `1.1.0` was confirmed through `uv tool list` and the doctor package check
- human-readable and JSON doctor output both reported `OK` on healthy state
- an empty disposable project remained without `.agent-monitor/monitor.db` before and after doctor; exit code was 0
- a disposable stale remote-state PID was reported as `ERROR` with exit code 1
- the synthetic `remote.json` remained present and unchanged, confirming doctor did not repair or delete the finding
- production localhost 8765 and Tailscale HTTPS 9443 remained HTTP 200
- existing Serve mappings 443→4174, 8443→5173, and 9443→8765 were unchanged
- the `nashiri-core` monitor was stopped only for the CLI update and returned on the same 8766/9444 ports with both endpoints HTTP 200
- no additional test server was started

The first CLI reinstall attempt could not replace the uv tool environment while `nashiri-core` was actively using the old installation. Stopping only that project's monitor allowed the update, after which the project returned on the same ports. Treat this as a Windows update procedure note rather than a doctor defect.

The CLI does not currently implement a global `--version` option. Version 1.1.0 was still independently visible through `uv tool list` and `monitor doctor`. A dedicated `--version` flag is a small CLI usability improvement, not a Phase 14 blocker.

## Phase 14 safety properties verified

- doctor does not initialize an absent database
- doctor does not run monitor schema migrations
- doctor does not stop processes
- doctor does not change Tailscale Serve
- doctor does not delete stale state
- doctor does not rewrite agent onboarding files
- errors return exit code 1
- healthy state returns exit code 0
- `--json` provides machine-readable output consistent with human output

## Next task

Phase 15 has not started.

Before implementing notifications, define the durable notification event/outbox model and select exactly one first notification adapter. Keep SQLite as the authoritative question/answer store.

Do not add automatic agent resume in Phase 15; that remains Phase 16.

## Post-v1 roadmap

- Phase 15: durable notifications
- Phase 16: runner lifecycle and automatic resume
- Phase 17: multi-project registry
- Phase 18: automatic telemetry
- Phase 19: second-agent portability verification

## Handoff instruction

Read, in order:

1. `README.md`
2. `IMPLEMENTATION_PLAN.md`
3. `ARCHITECTURE.md`
4. `STATE.md`
5. `AGENT_INTEGRATION.md`
6. `PHASE13_VERIFICATION.md`

Then perform the task under **Next task**.
