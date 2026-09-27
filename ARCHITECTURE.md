# Architecture

## Core idea

The monitor is a reusable tool that other projects call.

Other projects should not need to copy this repository or read its source code during normal use.

Conceptually:

```text
AI agent
   |
   | monitor ...
   v
shared monitor CLI
   |
   v
local project data
   |
   v
local web dashboard
   |
   v
human
```

## Repository boundary

`ai-agent-monitor` contains the reusable monitoring tool.

A project that uses the tool will eventually have a small project-local area, for example:

```text
some-project/
├── src/
├── AGENTS.md
├── STATE.md
└── .agent-monitor/
    ├── config.toml
    ├── dashboard.html
    └── monitor.db
```

The exact layout may change during implementation.

## Shared mechanism, project-specific display

The shared tool should handle things such as:

- recording progress
- storing task state
- storing questions and answers
- registering artifacts
- exposing data to the dashboard

The dashboard may differ by project.

A research project may care about experiments and benchmark results. A software project may care about tests and builds. A document project may care about sections, sources, and unresolved claims.

Do not force every project into one fixed dashboard.

## Data storage

Local runtime storage uses SQLite.

Reasons:

- one local file
- no separate database server
- suitable for a small local tool
- supports structured records better than several independently updated JSON files

The agent should interact through the monitor CLI rather than writing SQL directly.

Runtime database files must not be committed to Git.

Phase 2 configures SQLite in WAL mode with a 5-second busy timeout so the server can read while progress events are recorded.

## Agent state and monitor history are different

Project files such as `STATE.md` are for the next agent to understand the current work and what to do next.

The monitor database is for operational history that a human may want to inspect.

Do not turn `STATE.md` into an ever-growing activity log.

## Human answers

The database should be the source of truth for questions and answers.

A file such as `HUMAN.md` may later be generated as a convenience, but it should not become the authoritative store if both humans and agents can modify it.

## Blocking questions

Recording a blocking question does not automatically stop or resume an agent.

For the early versions, the intended behavior is:

1. the agent records a blocking question
2. the current agent run may end
3. the human answers later
4. a later agent run reads the answer and continues

Automatic resumption requires runner-specific integration and is postponed.

## Local backend boundary

The application backend remains bound to `127.0.0.1`. Local browser access therefore works without exposing the Python server directly to the LAN.

Phase 9 adds remote smartphone access through Tailscale Serve while keeping this localhost backend boundary.

## Security and public repository policy

The repository is public.

Never commit:

- API keys
- authentication tokens
- credentials
- private customer or project data
- personal information
- runtime SQLite databases
- secrets copied from monitored projects

The tool should be designed so runtime project data stays local by default.


## Current task storage

Phase 3 stores exactly one active task in SQLite. The `current_task` table uses a fixed row ID of 1. Starting another task replaces that row; completing the task deletes it. This keeps the Phase 3 model deliberately small and leaves task history for a later phase if it becomes necessary.


## Question storage

Phase 4 stores human questions in SQLite with an `open` status. The dashboard only retrieves open questions. Phase 5 may add answers and change a question out of the open state, but Phase 4 does not provide any answer-writing endpoint or browser form.


## Answer storage

Phase 5 keeps answers in the existing `questions` table. An answered question stores `answer` and `answered_at`, and its status changes from `open` to `answered`.

Existing Phase 4 databases are migrated in place by adding those two nullable columns if they are missing. The browser writes an answer through a same-origin JSON POST endpoint. Answered questions are removed from the unanswered list, while `/api/answers` and `python monitor.py answers` let an agent read them later.

Phase 5 still does not restart or resume an agent automatically.


## Artifact snapshots

Phase 6 does not expose arbitrary filesystem paths. Registration accepts a regular file under the command's current working directory, copies it into `.agent-monitor/artifacts/`, and stores only metadata plus the snapshot storage name in SQLite.

Each artifact record includes the display name, original filename, byte size, SHA-256, MIME type, registration time, and Git commit when it can be detected. The dashboard links to the stored snapshot, not the original source file, so later changes to the source do not change what the human reviewed.

HTML artifacts are served with `Content-Security-Policy: sandbox allow-scripts` and without `allow-same-origin`, preventing a generated HTML artifact from inheriting the monitor application's origin privileges.


## Project-specific metrics

Phase 7 uses a generic `metrics` table rather than predefined columns for cost, errors, task counts, or benchmark results.

Each metric has:

- a stable key
- a human-readable label
- a display value
- an optional unit
- an update timestamp

Values are stored as text because the shared monitor does not interpret or calculate project-specific metrics. A project may store `90.71`, `6 / 12`, `98.3`, or another display value according to its own needs.

Updating an existing metric without a new label or unit preserves its existing display metadata. Passing an empty unit explicitly removes the unit. Metric keys are limited to letters, numbers, dots, underscores, and hyphens.


## Installed CLI and project boundary

Phase 8 packages the application as the `ai-agent-monitor` Python package and exposes two equivalent console commands: `monitor` and `ai-agent-monitor`.

The current working directory at process start is the project root. Each project stores its own database, artifact snapshots, and optional dashboard override under its own `.agent-monitor/` directory. The installed package contains the default dashboard and executable code; project runtime data is never stored inside the tool installation environment.

The root-level `monitor.py` remains as a compatibility wrapper for the existing N100 deployment scripts.

`monitor init` creates the project-local runtime directory and a nested `.gitignore`. `monitor init --dashboard` additionally copies the bundled default dashboard into the project so that project-specific UI changes can be version-controlled independently of the shared tool.


## Tailnet-only remote access

Phase 9 adds `monitor remote start|status|stop`.

Each monitored project keeps remote state under its own `.agent-monitor/runtime/` directory. Starting remote access launches that project's monitor backend on a free localhost port, then asks Tailscale Serve for a tailnet-only HTTPS listener on an unused port.

The implementation deliberately does not use Tailscale Funnel. The access boundary is Tailscale tailnet membership; Phase 9 does not add a second application username/password system.

Port selection preserves unrelated services. Existing Tailscale Serve HTTPS ports and the local ports used by its proxy targets are read before choosing ports. The monitor selects HTTPS from `9443-9499` or `10443-10499` and avoids backend ports in `8765-8799` that are already listening or still referenced by another Serve proxy.

Stopping is conservative:

- the remote state must belong to the current project
- a live saved PID must answer with the current project's hashed project identity before it can be terminated
- the saved Tailscale HTTPS port is changed only when its current root proxy still matches the saved backend URL
- if a live PID or Serve mapping cannot be verified, the command refuses the destructive action

The health endpoint exposes only a short project identifier and project directory name, not the full local filesystem path.

The older repository-specific Tailscale deployment scripts remain for backward compatibility with the original N100 deployment.


## Windows logon persistence

Phase 10 uses the current Windows user's Startup folder for optional per-project startup. This avoids the administrator requirement encountered with the first Task Scheduler implementation on the N100.

`monitor startup install` writes two ignored runtime files for the current project:

- `.agent-monitor/runtime/startup.ps1`, which contains the remote health check and retry logic
- a project-specific `.cmd` launcher in the current user's Windows Startup folder

The launcher name contains the project's hashed project ID, which keeps projects independent. It uses the Python executable from the installed `ai-agent-monitor` tool environment rather than depending on the user's PATH, and it changes to the saved project root before running the package.

At logon, the PowerShell script first asks `monitor remote status` whether both the backend and Tailscale Serve mapping are already healthy. Otherwise it retries `remote start` up to 12 times with a five-second delay, allowing time for Tailscale to initialize.

Removal is conservative: the local startup state must belong to the current project, the stored project ID must match, and the saved launcher path must equal the launcher path derived for the current user and project before deletion is attempted.

This is per-user post-login persistence. It is not a pre-login Windows service and does not require switching to an administrator account.


## Agent integration contract

Phase 11 adds a read-only machine interface for CLI-capable agents.

`monitor status` returns schema version 1 with:

- project identity without local filesystem paths
- the current task
- the newest 10 progress events
- all unanswered questions
- the newest 10 answered questions
- latest artifact metadata
- all project metrics

Progress, question, and answer records retain their stable IDs and timestamps. History arrays are newest first.

Status is a snapshot, not an event queue. Reading it does not acknowledge answers, close questions, complete tasks, or otherwise change operational records. If a project has no monitor database, status returns an empty snapshot without creating the database.

`AGENT_INTEGRATION.md` defines when an agent should use task, progress, ask, status, answers, artifact, and metric commands. The contract is agent-neutral; Codex-specific onboarding belongs to Phase 12.


## Agent onboarding files

Phase 12 keeps the monitor core agent-neutral and installs small adapters into a target project.

`monitor agent install codex` manages:

- a marked block in repository-level `AGENTS.md`
- `.agents/skills/ai-agent-monitor/SKILL.md`
- `AI_AGENT_MONITOR.md`

The `AGENTS.md` block is deliberately short. It tells Codex when to use the repository skill and to read monitor state at the beginning of a new or resumed run. The skill points to `AI_AGENT_MONITOR.md` for the actual monitor contract.

The contract is bundled with the Python package and is also usable independently through `monitor agent install generic` or `monitor agent emit generic`.

Install/update is idempotent. Existing `AGENTS.md` content outside the marked monitor block is preserved. Removal verifies generated files before deleting them; modified generated files are left in place rather than being destroyed.

The monitor does not assume that non-Codex agents discover `.agents/skills/`. Their own instruction mechanism can reference the generic contract.


## Read-only diagnostics

Phase 14 adds `monitor doctor` as a read-only operational diagnostic boundary.

The doctor does not call the normal SQLite initialization path because that path may create tables or migrate old question columns. Database diagnosis instead opens an existing SQLite file in read-only/query-only mode, runs `PRAGMA quick_check`, and verifies the required tables and columns.

Other checks inspect agent onboarding consistency, saved remote state, live backend identity, Tailscale Serve mapping, Windows startup files, large runtime logs, and interrupted-write temporary files.

Diagnostic results use three severities:

- `ok`: no action required
- `warning`: usable state with something the operator should inspect
- `error`: an inconsistency that prevents the diagnosed feature from being trusted

The first implementation does not repair findings. This keeps diagnosis safe to run from agents and automation without granting it destructive behavior.


## Durable notification outbox

Phase 15 keeps notification delivery separate from the authoritative question/answer state.

When `monitor ask` creates a question, the same SQLite transaction creates one `question.created` outbox event. The logical event is unique by `event_type + entity_type + entity_id`, preventing repeated application logic from creating multiple logical notifications for the same question.

Outbox records store the logical event but no external-service secret. A separate `notification_deliveries` table stores one row for each channel that was enabled when the event was created. Telegram and optional email therefore have independent pending/delivered/cancelled state, attempt count, last-attempt time, delivered time, and last error.

Telegram uses the Bot API `sendMessage` method. The bot token comes from `AI_AGENT_MONITOR_TELEGRAM_BOT_TOKEN`; the project-local config stores only the chat ID and enabled state.

Email uses Python's SMTP client. Recipient, sender, SMTP host/port, username, security mode, and enabled state are project-local non-secret configuration. An SMTP password, when needed, comes from `AI_AGENT_MONITOR_SMTP_PASSWORD`. Email defaults to off and can be enabled or disabled independently.

If the human answers a question while channel deliveries are still pending, the same answer transaction cancels only those pending channel rows. If no channel delivered, the logical event becomes cancelled. If Telegram already delivered while email is still pending, the email row is cancelled and the logical event remains delivered. This prevents stale delayed notifications without erasing successful delivery history.

The outbox is durable across process restarts. It provides at-least-once delivery attempts at the transport boundary; a process failure after a remote service accepts a message but before the local delivered update may still produce a duplicate transport message unless that service provides an idempotency mechanism. The database uniqueness rule prevents duplicate logical outbox events but does not claim network-level exactly-once delivery.

External adapter credentials must stay outside committed files and outbox payloads.


The monitor server polls pending notification deliveries every five seconds. A failed automatic delivery is eligible for retry after one minute; `monitor notify send` can force an immediate retry. This avoids hammering a broken network or missing credential while preserving manual recovery.

`.agent-monitor/notifications.json` is project-local and ignored by Git. It contains no bot token or SMTP password.


### Email recipient fan-out

Version 1.2.1 extends the Phase 15 email adapter from one recipient to an arbitrary recipient list.

The project-local email configuration stores `recipients` as a list. The previous single `to` field is read as a one-item recipient list for backward compatibility.

Each email recipient becomes its own `notification_deliveries` row using the delivery `target` field. A message is sent separately to each target rather than exposing all addresses in one `To` header. If one recipient fails after another succeeds, retry targets only the failed address.

The delivery-table migration converts the previous `UNIQUE(notification_id, channel)` layout to `UNIQUE(notification_id, channel, target)`. Existing single-recipient email deliveries inherit the configured legacy address when it can be determined.

Removing a recipient cancels only that recipient's pending deliveries. Removing the last recipient also disables email notifications. Adding or removing recipients affects future notification events; already-created events retain their snapshotted delivery targets except that explicitly removed pending targets are cancelled.


## Runner lifecycle and automatic resume

Phase 16 separates four durable concepts:

- `answer_events`: immutable records of human answers
- `resume_requests`: one decision about whether a particular answer should resume work
- `resume_attempts`: each actual launch attempt and its process/result state
- `agent_runners`: external agent identity and lifecycle state

The questions table keeps its existing answer columns for API compatibility and adds a nullable `runner_id`. Existing answered questions are backfilled into `answer_events` during normal database initialization.

The first adapter is Codex. Repository onboarding tells Codex to register the current `CODEX_THREAD_ID`. The generic state model does not infer a runner from the newest session.

Automatic resume is project-local and disabled by default in `.agent-monitor/runner.json`, which is ignored by Git. The adapter command may be configured without storing credentials.

A pending request is claimed with a conditional SQLite update. Before claim, the monitor rechecks that a current task still exists, the runner still exists, and its state is still `waiting_for_human`. A second active request for the same runner blocks later requests until the first leaves the active state.

The server launches a detached monitor worker rather than running Codex inside the HTTP thread. The worker invokes the exact stored Codex thread through `codex exec resume`. The argv is constructed as a list; the human answer is not interpolated into a shell command. The resumed Codex process uses an explicit project cwd, `workspace-write`, and `on-request` approvals.

Crash recovery is conservative. A request whose worker PID is still alive remains active. If a request was claimed/running but its worker can no longer be proven alive, it becomes `uncertain`; the dispatcher does not automatically retry it. A human can inspect it and explicitly return it to pending with `monitor runner retry <id>`.

This trades a possible manual recovery step for protection against launching two Codex processes after an ambiguous crash boundary.
