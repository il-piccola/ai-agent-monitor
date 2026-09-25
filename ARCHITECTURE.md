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

## Local access first

The first server should bind only for local use.

A browser on the same computer can view it.

Smartphone or remote access requires additional networking and security decisions and is postponed until the local workflow proves useful.

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
