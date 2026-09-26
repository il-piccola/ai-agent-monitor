# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Formal release: `v1.0.0` published
- Development package version: `1.1.0`
- Completed phases: 1 through 13
- Current phase: 14 implementation complete; N100 verification pending

## v1.0 baseline

The formal `v1.0.0` tag and GitHub Release are published from the verified Phase 13 baseline.

The v1.0 gate includes the real Codex workflow with iPhone answering and transcript-independent continuation. See `PHASE13_VERIFICATION.md` and `RELEASE_NOTES_v1.0.0.md`.

## Phase 14 implementation

Version `1.1.0` adds read-only project diagnostics through:

```text
monitor doctor
monitor doctor --json
```

Implemented checks:

- package version
- project identity and runtime paths
- SQLite readability through a read-only connection
- SQLite `PRAGMA quick_check`
- required monitor tables and columns
- current-task/open-question summary without modifying the DB
- Codex/generic onboarding consistency
- malformed `AGENTS.md` managed markers
- modified generated contract/skill warnings
- saved remote state and project ownership
- saved remote PID liveness
- live backend project identity
- Tailscale Serve mapping consistency
- saved Windows startup state and startup files
- runtime logs over 10 MiB
- temporary runtime files left after interrupted writes

Safety properties:

- doctor does not initialize an absent database
- doctor does not run monitor schema migrations
- doctor does not stop processes
- doctor does not change Tailscale Serve
- doctor does not delete stale state
- doctor does not rewrite agent onboarding files
- errors return exit code 1; warnings remain exit code 0
- `--json` provides machine-readable output

The standard-library test suite now contains 84 tests. CI passes on Windows and Ubuntu with Python 3.10 and 3.12.

During CI, the first new Windows run exposed a test-only SQLite handle leak. The test now closes that connection explicitly; the corrected 4-environment matrix passes.

## Next task

Verify Phase 14 on the N100 before beginning Phase 15.

1. pull the latest `main`
2. run the full test suite and confirm all 84 tests pass
3. reinstall with `uv tool install --force .`
4. confirm installed version `1.1.0`
5. run `monitor doctor` in `ai-agent-monitor` and confirm the report is understandable and non-destructive
6. run `monitor doctor --json` and confirm valid JSON with the same overall result
7. run doctor in a disposable empty project and confirm it does not create `.agent-monitor/monitor.db`
8. create at least one disposable inconsistency, such as an incomplete SQLite schema or stale remote-state PID, and confirm doctor reports an error without repairing/deleting it
9. confirm the production backend 8765 and HTTPS 9443 remain HTTP 200 and existing Serve mappings are unchanged

Do not use the production project's real database as the deliberately broken test case.

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
