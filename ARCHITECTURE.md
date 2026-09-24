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
