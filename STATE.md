# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Formal release: `v1.0.0` published
- Development package version: `1.5.0`
- Completed phases: 1 through 17
- Current phase: Phase 18 implemented and running on N100; iPhone visual verification pending

## Verified baseline

Phase 14 read-only diagnostics and Phase 15 durable notifications are verified on the N100/iPhone.

Phase 15 final runtime configuration after verification:

- Telegram ON
- Email OFF
- one intended email recipient remains configured
- no pending notification remains
- production 8765/9443 and `nashiri-core` 8766/9444 remained HTTP 200
- Tailscale Serve mappings were unchanged

Version 1.2.1 multi-recipient email delivery passed the real two-recipient fan-out and recipient-removal tests.

## Phase 16 implementation

Version `1.3.1` includes an opt-in Codex runner lifecycle and automatic-resume path.

Durable records are separate:

- `answer_events`: human answers
- `agent_runners`: runner adapter, external identity, and lifecycle state
- `resume_requests`: whether one answer should resume a linked runner
- `resume_attempts`: each worker/Codex launch attempt

Runner states include `running`, `waiting_for_human`, `stopped`, `completed`, and `failed`.

Codex integration:

- the Codex repository skill now runs `monitor runner register codex` before `monitor status`
- registration reads the exact `CODEX_THREAD_ID` supplied to Codex shell tools
- `monitor ask` links the question to the registered runner and marks it waiting
- answering a linked question creates one durable resume request only when an active task exists and the runner is waiting
- automatic resume is OFF by default
- `monitor runner auto-resume on|off` controls the current project
- `monitor runner codex set --command ...` configures the executable when needed
- `monitor runner status` exposes runner/request/attempt state
- `monitor runner dispatch` forces one pending dispatch for verification/recovery
- `monitor runner retry <id>` is required to retry an ambiguous `uncertain` request

Safety behavior covered by tests:

- an answer cannot create duplicate resume requests
- only one active resume request is allowed per runner
- task completion between answer and dispatch cancels the resume
- runner completion between answer and dispatch cancels the resume
- a missing worker after restart becomes `uncertain` and is not silently relaunched
- worker/Codex failures remain inspectable
- auto-resume disabled leaves requests pending
- the worker resumes the exact stored Codex thread, never `--last`
- automatic Codex resume uses one-run config overrides `sandbox_mode="workspace-write"` and `approval_policy="on-request"`; it does not use `danger-full-access`
- runner config is project-local and ignored by Git
- doctor remains read-only and reports runner configuration/recovery warnings

The standard-library suite contains 133 tests.

## Current Codex integration basis

Current Codex CLI exposes `CODEX_THREAD_ID` to shell tool executions and supports non-interactive `codex exec resume <thread-id> <prompt>`. The Phase 16 adapter relies on those public CLI behaviors rather than scraping Codex rollout files.

## Phase 16 N100 verification (2026-09-27)

The first preflight found that N100 Codex CLI `0.155.0-alpha.16.4` rejects `--ask-for-approval`. The supported one-run configuration is:

```text
codex -c sandbox_mode="workspace-write" -c approval_policy="on-request" exec ...
```

In the disposable `phase16-runner-test` project, a fresh Codex run registered its exact thread ID, started a task, and asked which value to write. The initial run stopped with no result file and one open question. Telegram delivered the question; the human answered `ALPHA` on the iPhone dashboard. Without a manual Codex launch, the registered thread resumed, wrote exactly one `ALPHA` line, completed the task and runner, and left one completed request and attempt with exit code 0. A subsequent dispatch claimed and launched zero requests. The full iPhone path used the same `on-request` policy with the config override placed after `exec`.

The `1.3.1` shared argv builder places both config overrides before `exec`. Its N100 preflight and a separate exact `exec resume` invocation passed. Preflight now decodes Codex output as UTF-8 on Windows, avoiding the observed CP932 decode error. The standard-library suite has 133 passing tests.

In a separate disposable project, a synthetic missing worker PID changed one running request and attempt to `uncertain`; dispatch claimed and launched zero requests, and no worker process was started. The test project's auto-resume setting was returned to OFF and its 8767/9445 remote was stopped. Production 8765/9443 and `nashiri-core` 8766/9444 returned HTTP 200; the original Serve mappings remained in place.

## Phase 17 implementation

Version `1.4.0` adds `monitor registry add|list|remove`, a read-only `/api/status` snapshot, a registry aggregation API, and a Japanese `/registry` page. Registration verifies identity through each dashboard's HTTP API. The registry reads no other project's SQLite database and keeps unavailable projects visible. Older Monitor servers are supported through existing APIs.

The intended N100 deployment reuses the existing 8765/9443 server for the registry and registers both that dashboard and `nashiri-core` at 8766/9444. It must not create a new Serve port.

The standard-library suite passes all 140 tests on N100 for the Phase 17 implementation.

N100 runtime verification: the existing 8765/9443 deployment was restarted on the same ports. `/registry`, `/api/registry`, and `/api/status` returned HTTP 200. The registry reported `ai-agent-monitor` and `nashiri-core` as available; the latter reported its active task and one unanswered question. Backend 8766 and HTTPS 9444 remained HTTP 200. The Serve mappings for 443, 8443, 8444, 9443, and 9444 remained unchanged. On 2026-09-27, the human confirmed that both project cards are displayed on the iPhone and that both registry links open their respective dashboards. Phase 17 success conditions are met.

## Phase 18 implementation

Version `1.5.0` adds read-only `monitor telemetry`, `/api/telemetry`, and a Japanese automatic-telemetry dashboard section. Each numeric measurement includes its source and observation time. The measurements derive from task start/end records, question/answer timestamps, and automatic-resume attempt records. Manual metrics remain separate. Tool failures, provider tokens/cost/cache, quality, and error rates are not inferred without authoritative data.

`task_runs` begins recording completed or replaced tasks as version 1.5 handles them. Older completed tasks cannot be reconstructed. Reading telemetry on an empty project does not create a database. The standard-library suite passes 148 tests on N100.

N100 runtime verification: the existing 8765/9443 server was restarted on those same ports. The dashboard HTML contains the Japanese automatic-telemetry section and returned HTTP 200. `/api/telemetry` returned schema 1, project identity, six measurements from the existing project database, source metadata, and observation timestamps. `/api/registry` still listed both projects. Backend 8766 and HTTPS 9444 returned HTTP 200. Serve mappings for 443, 8443, 8444, 9443, and 9444 were unchanged. iPhone visual confirmation is pending.

## Next task

Confirm the automatic-telemetry section on iPhone. The N100's installed `uv tool` CLI remains at 1.3.1 because the active `nashiri-core` server was left running; the repository source CLI contains version 1.5.0. Phase 19 has not started.
