---
name: ai-agent-monitor
description: Use AI Agent Monitor to report meaningful task state, progress, human questions, reviewable artifacts, and project metrics during development work in this repository.
---

# AI Agent Monitor

Use this skill for development work in this repository when AI Agent Monitor is enabled.

Read `AI_AGENT_MONITOR.md` for the agent-neutral command contract.

If PowerShell cannot find `monitor`, resolve the existing installed executable
with `$monitor = Join-Path ((& uv tool dir --bin).Trim()) "monitor.exe"` and
invoke commands as `& $monitor ...`. Do not skip monitor updates or reinstall
the package only because its bin directory is missing from `PATH`.

At the start of a new run or when resuming work, register the current Codex
thread before reading monitor state:

```text
monitor runner register codex
monitor status
```

Codex exposes the active thread ID to shell tools through `CODEX_THREAD_ID`;
the monitor stores that ID so a later human answer can target the same thread.
Do not substitute `--last` or another guessed session ID.

If runner registration reports that `CODEX_THREAD_ID` is unavailable, continue
with `monitor status` and normal monitor reporting, but do not claim that
automatic resume is available for that run.

Use the returned state and the repository's own instructions before deciding whether to start or continue a task.

Follow the contract in `AI_AGENT_MONITOR.md` for:

- `monitor task start|done`
- `monitor progress`
- `monitor ask`
- `monitor status`
- `monitor answers`
- `monitor artifact`
- `monitor metric set|delete`
- `monitor runner register codex`
- `monitor runner complete`

When a task is genuinely complete, run `monitor task done` and then
`monitor runner complete`. A question created by a registered Codex thread
automatically marks that runner as waiting for human input.

Do not turn the progress feed into a command log. Ask the human only when a real decision is required. Do not mark blocked or partial work complete.
