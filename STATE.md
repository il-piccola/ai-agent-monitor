# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`

## Current milestone

Phase 3: track the current task.

## Completed

Phase 1 is implemented and verified.

The repository now has:

- `monitor.py`: a dependency-free local HTTP server
- `dashboard.html`: the initial dashboard
- `.gitignore`: ignores Python and future local runtime files

The server listens on `127.0.0.1` and uses port `8765` by default.

Phase 1 verification completed successfully:

- Python syntax check passed
- `GET /` returned HTTP 200
- the dashboard title was present
- the current-task placeholder was present
- the recent-progress placeholder was present
- the unanswered-question count was present

## Tailscale deployment helper

A Tailscale Serve deployment helper has been added at `deploy/tailscale-serve.sh`.

It automatically avoids ports 443 and 8443, checks existing Serve configuration, chooses free ports, starts the Phase 1 monitor, verifies the backend, and configures Tailscale Serve.

A matching stop helper exists at `deploy/tailscale-stop.sh`. Windows PowerShell start and stop helpers are also available at `deploy/tailscale-serve.ps1` and `deploy/tailscale-stop.ps1`.

The helper scripts passed shell syntax checks and a local simulation in which occupied backend and Tailscale ports were skipped correctly.

The Windows helper has been executed on the target N100 Windows machine. The backend and Tailscale HTTPS endpoint returned HTTP 200 with all Phase 1 placeholders present. The stop helper removed only its own Serve port and backend process; restarting the helper restored the deployment. The other Serve ports stayed in place. The backend does not start automatically after a Windows reboot.

Current verified deployment:

- backend: `127.0.0.1:8765`
- Tailscale HTTPS port: `9443`
- tailnet URL: `https://leto.taile04360.ts.net:9443/`
- backend process at verification time: `monitor.py` PID `26312`
- N100-local HTTP check: 200
- N100-to-Tailscale HTTPS check: 200
- direct iPhone browser check: verified successfully

The Windows deployment changes were pushed to `main`, including commits `f6bf81a9` and `a76161241f1070f00d13bd9acb66d6c617c0d342`.

## Phase 2 implementation

Phase 2 is complete and verified end to end.

Implemented behavior:

- `python monitor.py progress "<message>"` records a progress event
- progress is stored in `.agent-monitor/monitor.db` using SQLite
- SQLite uses WAL mode and a 5-second busy timeout
- `GET /api/progress` returns the latest progress events as JSON
- the dashboard reloads progress from that endpoint every three seconds
- progress text is inserted into the page with `textContent`
- the existing server invocation and Tailscale deployment scripts remain compatible

Local verification completed successfully:

- Python syntax check passed
- progress command wrote a test event
- SQLite retained the event
- dashboard endpoint returned HTTP 200
- progress API returned HTTP 200 and the expected JSON
- all 3 standard-library unit tests passed

## Phase 2 end-to-end verification

Phase 2 was verified on the N100 deployment:

- `main` was fast-forwarded to `2f97f5595f8dc19f3369b4cb042d7b3543f62300`
- backend restarted successfully on `127.0.0.1:8765`
- Tailscale Serve remained on HTTPS port `9443`
- `Phase 2 iPhone test` was written to SQLite
- local dashboard and `/api/progress` returned HTTP 200
- Tailscale dashboard and `/api/progress` returned HTTP 200
- the API response contained the recorded progress message
- the iPhone dashboard displayed `Phase 2 iPhone test`

Phase 2 therefore meets its success condition.

## Phase 3 implementation

Phase 3 is implemented in `main` but still needs verification on the N100 deployment.

Implemented behavior:

- `python monitor.py task start "<title>"` starts or replaces the current task
- `python monitor.py task done` clears the current task
- the current task is stored in SQLite in a singleton `current_task` row
- `GET /api/task` returns the active task or `null`
- the dashboard reloads the current task every three seconds
- the task title is inserted with `textContent`
- progress recording from Phase 2 remains unchanged

Local verification completed successfully:

- Python syntax and storage behavior checked
- all 7 standard-library unit tests passed
- starting a task persisted it
- starting a second task replaced the first
- completing a task cleared it
- completing with no active task was handled
- `GET /api/task` returned HTTP 200 with the expected task
- `GET /api/progress` still returned HTTP 200 with progress data
- dashboard markup contains the task API integration

## Next task

On the N100 machine:

1. pull the latest `main`
2. restart the existing ai-agent-monitor deployment
3. run `python monitor.py task start "Phase 3 iPhone test"` using the same Python interpreter used for the app
4. verify that `Current task` on the iPhone shows `Phase 3 iPhone test`
5. run `python monitor.py task done`
6. verify that the iPhone returns to `No active task.`

Do not begin Phase 4 until both iPhone checks succeed.

## Not implemented yet

- Phase 3 current-task display verification from the iPhone
- questions
- answers
- artifact registration
- metrics
- packaging as a reusable CLI
- use from other projects
- Slack, Discord, or Telegram integration
- automatic agent resume
- automatic LLM cost calculation

## Handoff instruction

A new assistant or developer should read, in this order:

1. `README.md`
2. `IMPLEMENTATION_PLAN.md`
3. `ARCHITECTURE.md`
4. `STATE.md`

Then continue with the task listed under **Next task**. Do not add later-phase features before the current phase works.
