# Phase 13 Real-Agent Verification

Phase 13 verifies that a real Codex project uses AI Agent Monitor without the human manually issuing monitor commands on the agent's behalf.

This phase does not add new monitor features unless the real workflow exposes a blocking defect.

## Select the project

Use one existing development project that:

- already has meaningful source code or documents under active development
- can be worked on safely without risking production data
- has at least one ordinary task that does not need a human decision
- has or can naturally produce one task with a genuine human decision point
- can run Codex from the repository root
- can accept the Phase 12 Codex onboarding files

Do not use the ai-agent-monitor repository itself. The point is to verify portability into another project.

## Prepare the project

From the selected project's repository root:

```text
monitor agent install codex
monitor status
```

Confirm that the repository contains:

- the original project instructions, if any
- one AI Agent Monitor managed block in `AGENTS.md`
- `.agents/skills/ai-agent-monitor/SKILL.md`
- `AI_AGENT_MONITOR.md`

Do not manually call `monitor task`, `monitor progress`, `monitor ask`, `monitor artifact`, or `monitor metric` for the agent during the scenarios below.

The human may use `monitor status` after a run to inspect what the agent recorded.

## Scenario A: ordinary task with no human decision

Choose a real task with a clear expected result and no missing product decision.

Examples include:

- fix a known bug with an existing expected behavior
- add tests for already-specified behavior
- implement a small refactor with an objective acceptance condition
- update a document from existing source material without requiring editorial choice

Give Codex the normal project task. Do not mention AI Agent Monitor in the task prompt.

Expected monitor behavior:

1. Codex reads the repository instructions and monitor skill
2. Codex runs `monitor status`
3. Codex starts or continues the appropriate current task
4. Codex records a small number of meaningful progress checkpoints
5. Codex does not create a human question
6. Codex validates the work using the project's normal tests or checks
7. Codex registers a reviewable artifact only if the task naturally produces one
8. Codex updates only metrics that the project already defines
9. Codex marks the task complete only after validation

Pass criteria:

- `open_questions` remains empty
- the current task is cleared after successful completion
- progress records describe milestones rather than individual shell commands
- no manual monitor command from the human was required

## Scenario B: genuine human decision

Choose a real task where two or more plausible choices would change the implementation and the repository does not already specify which choice is required.

Examples include:

- two backward-compatibility policies are both technically valid
- a user-facing label, format, or workflow requires an owner decision
- two data-migration strategies have materially different tradeoffs
- scope must be chosen before the implementation can finish

Do not manufacture ambiguity that would normally be solved from code, tests, project instructions, or established conventions.

Give Codex the normal project task. Again, do not mention monitor commands in the prompt.

Expected monitor behavior before the human answer:

1. Codex runs `monitor status`
2. Codex starts or continues the task
3. Codex completes independent work that does not depend on the decision
4. Codex records one clear question with enough context to answer from the dashboard
5. the task remains active
6. Codex does not invent an answer and does not mark the task complete

Human step:

- open the project's dashboard on the iPhone
- answer the question in the browser
- do not copy the answer into the Codex chat

## Fresh-run continuation check

After the human answers, start a fresh Codex run/chat for the same repository.

The new run must not depend on the previous chat transcript.

Give a short continuation prompt such as:

```text
Continue the current project task.
```

Expected behavior:

1. the fresh run reads repository instructions
2. it runs `monitor status`
3. it matches the recent answer to the original question ID
4. it continues using the human answer
5. it validates the completed work
6. it records meaningful final progress
7. it registers a reviewable artifact if appropriate
8. it marks the task complete

Pass criteria:

- the human answer is recovered from monitor state rather than the old chat
- the same decision is not asked again
- no database or dashboard HTML is read directly
- no human-issued monitor command is needed

## Evidence to record

For each scenario, save:

- the user task prompt
- the final `monitor status` JSON
- relevant progress records
- question and answer IDs for Scenario B
- test or validation result
- artifact metadata if one was registered
- whether the human had to tell Codex to use monitor
- any incorrect or excessive monitor events

Do not store private project secrets in the public ai-agent-monitor repository. Summarize sensitive evidence instead.

## v1.0 gate

Phase 13 passes when both scenarios succeed in a real Codex project and there is no blocking defect in the monitor contract or onboarding.

A second non-Codex agent is useful portability evidence but is not required to release v1.0.

If a blocking defect is found:

1. record the observed behavior
2. fix only the defect demonstrated by the real workflow
3. add a regression test
4. repeat the affected scenario

Do not add notifications, automatic resume, or automatic telemetry during Phase 13.
