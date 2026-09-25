---
name: ai-agent-monitor
description: Use AI Agent Monitor to report meaningful task state, progress, human questions, reviewable artifacts, and project metrics during development work in this repository.
---

# AI Agent Monitor

Use this skill for development work in this repository when AI Agent Monitor is enabled.

Read `AI_AGENT_MONITOR.md` for the agent-neutral command contract.

At the start of a new run or when resuming work, run:

```text
monitor status
```

Use the returned state and the repository's own instructions before deciding whether to start or continue a task.

Follow the contract in `AI_AGENT_MONITOR.md` for:

- `monitor task start|done`
- `monitor progress`
- `monitor ask`
- `monitor status`
- `monitor answers`
- `monitor artifact`
- `monitor metric set|delete`

Do not turn the progress feed into a command log. Ask the human only when a real decision is required. Do not mark blocked or partial work complete.
