# ai-agent-monitor

A small local monitor for long-running AI agent work.

The goal is to let a human see, at a glance, what an AI agent is doing without reading its full logs. The monitor will grow gradually from a simple local dashboard into a reusable tool that other projects can call.

## Initial goals

The monitor should eventually show:

- the agent's current task
- recent meaningful progress
- questions that need a human answer
- the latest generated artifacts
- optional project-specific metrics such as cost or error counts

The dashboard is intentionally project-specific. The shared tool provides the recording and retrieval mechanism; each project may decide what information to display.

## Development approach

We are starting small.

The first milestone is only:

1. open a local monitoring page in a browser
2. let an agent add simple progress messages
3. display those messages on the page

Questions, answers, artifacts, smartphone access, notifications, automatic cost tracking, and automatic agent resumption come later.

See:

- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the staged development plan
- [ARCHITECTURE.md](ARCHITECTURE.md) for design decisions
- [STATE.md](STATE.md) for the current project state and next task

## Repository policy

This repository is public. Do not commit API keys, access tokens, private project data, personal information, or runtime databases.
