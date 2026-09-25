# AI Agent Monitor v1.0.0

AI Agent Monitor v1.0.0 is the first release verified in a real Codex development workflow.

## What v1.0.0 provides

- local SQLite-backed project state
- browser dashboard for current task, progress, questions, answers, artifacts, and metrics
- human answers from the browser
- immutable artifact snapshots
- project-specific metrics
- reusable `monitor` CLI with per-project isolation
- Tailscale Serve support for tailnet-only iPhone access
- optional per-user Windows logon startup
- read-only machine-readable `monitor status`
- agent-neutral integration contract
- Codex onboarding through a managed `AGENTS.md` block and repository skill
- generic onboarding contract for other CLI-capable agents

## Real-agent verification

The v1.0 gate was tested on an N100 Windows machine in a separate real Codex project.

Scenario A verified that Codex could discover the monitor integration without monitor commands in the user prompt, read status, manage the task, record meaningful progress, run project validation, register an artifact, and finish without asking an unnecessary human question.

Scenario B verified the asynchronous human-decision loop. The human answered from the iPhone dashboard, then a new Codex execution with no inherited conversation history recovered the stored answer from monitor state, validated the selected behavior, recorded progress, and completed the task.

The first Scenario A attempt exposed a PowerShell PATH assumption for uv-installed tools. Version 0.12.1 added an executable-path fallback and regression coverage; the repeated scenario passed.

## Validation

- 72 standard-library tests pass on the N100
- GitHub Actions passes on Windows and Ubuntu
- CI covers Python 3.10 and 3.12
- Phase 1 through Phase 13 verification is recorded in the repository

## Known limits

v1.0.0 does not provide:

- outbound Slack, Discord, or Telegram notifications
- automatic agent resume after a human answer
- automatic LLM cost calculation
- multi-agent orchestration
- a central dashboard for multiple projects
- separate application authentication beyond the Tailscale tailnet boundary
- pre-login Windows service startup

The monitor database remains the source of truth. Agent runners and notification services are intentionally kept outside the core state model.

## Upgrade and installation

Install from the repository checkout:

```text
uv tool install --force .
```

After the `v1.0.0` tag is published, installations can pin the Git tag:

```text
uv tool install --force git+https://github.com/il-piccola/ai-agent-monitor.git@v1.0.0
```

For Codex onboarding in another project:

```text
monitor agent install codex
```

Then use the project normally. Codex receives the monitor workflow through the repository instructions and skill.
