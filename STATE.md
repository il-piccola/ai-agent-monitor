# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Formal release: `v1.0.0` published
- Development package version: `1.3.1`
- Completed phases: 1 through 15
- Current phase: Phase 16 implementation complete; N100 real-runner verification pending

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

Version `1.3.0` adds an opt-in Codex runner lifecycle and automatic-resume path.

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
- automatic Codex resume uses one-run config overrides `sandbox_mode="workspace-write"` and `approval_policy="never"`; it does not use `danger-full-access`
- runner config is project-local and ignored by Git
- doctor remains read-only and reports runner configuration/recovery warnings

The standard-library suite contains 131 tests.

## Current Codex integration basis

Current Codex CLI exposes `CODEX_THREAD_ID` to shell tool executions and supports non-interactive `codex exec resume <thread-id> <prompt>`. The Phase 16 adapter relies on those public CLI behaviors rather than scraping Codex rollout files.

## Phase 16 N100 preflight finding

The first N100 preflight stopped before any real Codex task was launched. The installed Codex CLI accepted `exec resume` but rejected `--ask-for-approval` as an unknown argument.

No Phase 16 Codex task, human question, resume request, or automatic resume was executed during that failed preflight. The disposable 8767/9445 remote was stopped, production 8765/9443 and `nashiri-core` 8766/9444 remained HTTP 200, existing Serve mappings were restored, and the repository remained clean.

Version `1.3.1` changes the worker permission selection to Codex config overrides instead of the unsupported CLI flag:

```text
codex -c sandbox_mode="workspace-write" -c approval_policy="never" exec ...
```

The sandbox remains workspace-scoped. Because automatic resume is non-interactive, the approval policy is `never`: the worker cannot pause for an approval UI, and it does not receive danger-full-access.

## Next task

Verify Phase 16 on the N100 before beginning Phase 17.

Use a disposable real Codex project, not `ai-agent-monitor` production state and not the production `nashiri-core` task.

Verification must establish:

1. current N100 Codex exposes `CODEX_THREAD_ID` inside its shell tool execution
2. `codex exec resume <thread-id>` is available and the saved login works non-interactively
3. installing/updating the Codex monitor integration causes a fresh Codex run to register its thread
4. a real task remains active when Codex asks a genuine blocking question
5. the iPhone receives the Telegram notification
6. answering from the iPhone dashboard creates exactly one pending resume request
7. with auto-resume enabled, the server resumes the same registered thread without a human starting another Codex run
8. the resumed Codex reads `monitor status`, uses the stored answer, continues the task, and completes it
9. task completion and runner completion are recorded
10. no second Codex process is launched for the same answer
11. a deliberately simulated missing worker becomes `uncertain` and is not automatically retried
12. production 8765/9443, `nashiri-core` 8766/9444, notification settings, and existing Serve mappings remain unchanged

Phase 17 has not started.
