# Agent Integration Contract

This document defines how a CLI-capable AI agent should use AI Agent Monitor.

The contract is agent-neutral. Codex may consume it first, but the monitor does not require Codex or any other specific runner.

Write dashboard-facing task titles, progress, questions, and artifact labels in
the reader's language. When an older English task, question, or artifact label
must remain as the original record, add a Japanese display translation without
changing that record:

```text
monitor translate task "現在のタスク名"
monitor translate question 3 "質問の日本語訳"
monitor translate artifact 20 "成果物名の日本語訳"
```

## Start or resume work

Use the installed `monitor` command for the operations below. If the shell says
`monitor` is not recognized, locate the existing uv tool executable instead of
skipping monitor updates or reinstalling the package. In PowerShell on Windows:

```powershell
$monitor = Join-Path ((& uv tool dir --bin).Trim()) "monitor.exe"
& $monitor status
```

Use `& $monitor ...` for the other monitor commands in that PowerShell session.
On other platforms, resolve the installed `monitor` executable under the bin
directory reported by `uv tool dir --bin` and invoke it by its full path.

Run:

```text
monitor status
```

before reporting new work to the monitor.

`monitor status` is a read-only JSON snapshot. Do not read the SQLite database or scrape the dashboard HTML to reconstruct monitor state.

The status payload contains:

- `schema_version`
- `project`
- `current_task`
- `recent_progress`
- `open_questions`
- `recent_answers`
- `latest_artifact`
- `metrics`

Recent progress and recent answers are bounded. Open questions are returned without that history limit so an unresolved human decision is not hidden by older activity.

Arrays of progress, questions, and answers are ordered newest first. Records keep their IDs and timestamps.

Reading status does not acknowledge an answer, close a question, complete a task, or otherwise change stored project state.

## Current task

When beginning a monitored unit of work and no matching task is active:

```text
monitor task start "Describe the work"
```

There is exactly one current task. Starting another task replaces it.

Do not replace an unrelated active task merely to report what you are doing. First determine from the project instructions and repository state whether the existing task is stale, complete, or belongs to the work being continued.

When the monitored task is actually complete:

```text
monitor task done
```

Do not mark a blocked or partially completed task done.

## Progress

Record a progress event when something happened that would help a human understand the work without reading the agent transcript:

```text
monitor progress "Implemented the parser and all parser tests pass"
```

Useful progress events include completed milestones, meaningful test results, a change in approach, and a newly discovered constraint.

Do not report every file read, shell command, tool call, small edit, or internal reasoning step. The progress feed is a human-facing operational history, not a debug log.

Write the human-facing progress in the user's language. When an English source
message must remain intact for audit or technical context but the dashboard
reader needs Japanese, save both in one event:

```text
monitor progress --ja "パーサーを実装し、関連テストがすべて成功しました。" "Implemented the parser and all parser tests pass"
```

The dashboard shows the Japanese text while the original message remains in
the record. Existing records can be translated without changing the original:

```text
monitor progress translate 42 "既存の進捗記録の日本語訳"
```

## Human questions

When a human decision is genuinely required:

```text
monitor ask "Should the export preserve the legacy column names or use the new schema?"
```

Ask only when the answer materially affects the work and cannot be determined from existing project instructions, code, tests, or prior human answers.

Write enough context into the question for the human to answer from the dashboard. Do not invent the human's answer.

If other independent work can continue, continue it. If the decision blocks the task, leave the task active and end or pause the agent run as appropriate for the runner.

Projects may configure an adapter-specific automatic resume policy. The agent-neutral contract does not guess runner identity. Codex projects use their repository skill to register the current Codex thread before a question is created.

On a later run, use `monitor status` first. Match answers to questions by their stable question IDs. `monitor answers` remains available when older answered questions need to be inspected.

## Artifacts

When a file is ready for human review:

```text
monitor artifact ./path/to/result.html --name "Benchmark report"
```

Register reviewable outputs, not every intermediate file. The file must be inside the current project directory. Registration stores a snapshot, so later edits to the source file do not change the reviewed copy.

## Metrics

Use project-specific metrics only when the project defines a value that is useful to monitor:

```text
monitor metric set tests.passed 61 --label "Tests passed"
```

Do not invent costs, quality scores, error rates, or other measurements that the project cannot actually provide.

Updating a metric with the same key updates that metric. Use `monitor metric delete <key>` when a metric should no longer appear.

## Completion check

Before ending a completed monitored task:

1. run the relevant project tests or validation required by the project
2. record a final meaningful progress event when it adds information not already present
3. register a reviewable artifact if the task produced one
4. update project-defined metrics if their values changed
5. run `monitor task done`

A task that requires no human decision should normally complete without creating a question.

## Contract boundaries

The monitor stores operational state. Project files such as `STATE.md` remain responsible for durable project handoff instructions when the project uses them.

The monitor currently supports durable Telegram notifications, optional email, and an opt-in Codex automatic-resume adapter.

The monitor does not currently:

- automatically resume an unregistered or generic agent
- calculate the actual billed LLM cost (the optional Codex API-equivalent estimate is only a reference value)
- orchestrate multiple agents

Do not emulate those missing features by writing directly to the monitor database.
