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

### N100 Scenario A attempt 1

The normal project task completed and its existing validation passed, but the
Scenario A monitor criteria did not pass. The fresh Codex shell read the
onboarding files and attempted `monitor status`; PowerShell reported that
`monitor` was not recognized. The installed uv tool bin directory was not on
that shell's `PATH`, and no task or progress records were created. The human's
post-run status check showed an empty snapshot. No manual task, progress, or
question commands were issued.

The demonstrated defect was that the onboarding contract assumed the installed
command was on `PATH`. Version 0.12.1 documents resolving the existing uv tool
bin path in PowerShell and other platforms. After reinstalling 0.12.1 and
refreshing the test project's generated onboarding files, Scenario A was
repeated as attempt 2 below.

### N100 Scenario A attempt 2

After installing version 0.12.1 and refreshing the generated Codex skill and
contract, a fresh Codex run repeated the same ordinary test task without any
Monitor commands in the user prompt. Codex read the status snapshot using the
documented executable-path fallback, started and completed the task, ran the
validator package tests successfully, recorded one milestone, and registered
the changed test file as a reviewable artifact. It created no human question.

The human's post-run status check showed `current_task: null`, one progress
record (ID 1), `open_questions: []`, and artifact ID 1. The final test result
was 29 schema-negative cases passing. The project identity is omitted here to
avoid copying details from the private test repository into this public repo.

Scenario A passed and its evidence was reviewed against the Phase 13 pass criteria. Scenario B is next.

Do not add notifications, automatic resume, or automatic telemetry during Phase 13.


### N100 Scenario B attempt 1

The first Scenario B task reached a genuine human decision. The human answered `絶対パス` from the monitor dashboard. A later Codex execution read that stored answer and implemented the absolute-path behavior, preserving the validator's existing absolute-path output and adding documentation and regression coverage. Tests covering a temporary file outside the project, relative-path input, and validation failure passed; `npm test` and the repository validation also passed. The task was marked complete and no open question remained.

This attempt does **not** prove the fresh-run continuation requirement. The later Codex execution received context from the original conversation through the task-creation flow, so it had access to both monitor state and prior conversation history. The evidence therefore cannot establish that monitor state alone was sufficient for recovery.

Scenario B remains incomplete. Repeat the continuation test with a Codex chat opened independently from the original conversation. The independent chat must receive only a short continuation request and must not inherit or quote the original transcript.


### N100 Scenario B independent continuation

Scenario B passed on an independent Codex execution with no inherited conversation history. The human decision was `A（短いエラーを保つ）`, submitted through the monitor dashboard.

The independent Codex run recovered the active task and stored answer from monitor state, inspected the verification script and related tests, and determined that the selected short-error behavior was already implemented. It therefore preserved the existing behavior rather than making an unnecessary code change. It ran `scripts/tests/test_verify_hermes_tree_digest.ps1` successfully, recorded progress consistent with the stored answer, and completed the monitor task.

The final monitor state had no current task and no unanswered question. Existing unrelated uncommitted changes were left intact. No service or port configuration was changed.

This verifies the Phase 13 fresh-run requirement: the later Codex run could reconstruct the relevant task and human decision from the repository instructions and monitor state without relying on the original chat transcript.

## Phase 13 result

Scenario A and Scenario B both passed in a real Codex project. The only blocking defect discovered during verification was the missing-CLI-path assumption from Scenario A attempt 1; version 0.12.1 fixed it and added regression coverage.

The v1.0 gate is satisfied for the Codex workflow. A second non-Codex agent remains optional portability evidence rather than a release requirement.
