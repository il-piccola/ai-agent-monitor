# Implementation Plan

This project is intentionally developed in small stages. Each stage should be usable and understandable before the next one is added.

## Phase 1: Show a local dashboard ✅

Create a small program that starts a local web page.

The page initially shows:

- Current task: no active task
- Recent progress: no entries yet
- Questions: none

Success condition: a user can start the program and open the dashboard in a browser.

## Phase 2: Record progress ✅

Add a simple command such as:

```text
monitor progress "Login page completed"
```

The dashboard should show the recorded message with a timestamp.

Success condition: an AI agent can report progress and a human can see it in the browser.

This is the first MVP.

## Phase 3: Track the current task ✅

Add commands for starting and completing a task.

Example:

```text
monitor task start "Build login page"
monitor task done
```

The dashboard should show what the agent is currently working on.

## Phase 4: Show questions for the human ✅

Allow an agent to record a question.

Example:

```text
monitor ask "Should I use option A or option B?"
```

The dashboard should list unanswered questions.

At first, showing the question is enough.

## Phase 5: Answer questions in the browser ✅

Add an answer field to the dashboard.

A human can answer a question in the browser. The answer is stored so the agent can read it later.

The first implementation does not need to restart the agent automatically.

## Phase 6: Register artifacts ✅

Allow the agent to register a generated file, for example a report, HTML preview, image, or document.

The dashboard should provide a link to the latest registered artifact.

Artifact records should eventually include enough information to identify the exact reviewed version, such as a hash and, when available, the Git commit.

## Phase 7: Add project-specific metrics ✅

Allow projects to record optional values such as:

- cost
- error count
- completed tasks
- elapsed time
- benchmark results

The shared monitor should not decide which metrics every project must use.

## Phase 8: Use the monitor from other projects ✅

Package the shared monitor as a CLI tool.

Other projects should call the installed CLI instead of copying this repository into themselves or using it as a Git submodule.

Each monitored project may keep its own configuration, dashboard, and local runtime database.

## Phase 9: Remote and smartphone access ✅

Add reusable remote access for installed-CLI projects through Tailscale Serve.

The first implementation:

- keeps each backend bound to localhost
- exposes HTTPS only inside the Tailscale tailnet
- does not enable Funnel
- automatically avoids existing Tailscale Serve ports
- keeps remote runtime state project-local
- starts, reports, and stops one project's endpoint without changing unrelated Serve entries

Success condition: two different monitored projects can be opened from the iPhone through different tailnet URLs, and stopping one project leaves the other project and the established production endpoint untouched.

Separate application-level authentication, public internet hosting, messaging integrations, and automatic agent resume remain outside this phase.


## Phase 10: Windows logon startup ✅

Add optional per-user Windows logon startup so a project's tailnet monitor can return without administrator rights.

The implementation:

- provides `monitor startup install|status|remove`
- writes a project-specific launcher into the current user's Windows Startup folder
- uses a launcher filename derived from the project ID
- launches the installed package from the saved project directory
- checks whether the remote is already healthy before starting another one
- retries while Tailscale is still initializing
- removes only the current project's verified launcher and local startup files
- supports Windows only; other platforms fail explicitly
- does not require Task Scheduler administrator permissions

The earlier Task Scheduler version was rejected after N100 verification returned `Access is denied` for the normal `LETO\ilpic` account.

Success condition: after installing startup for a test project and signing out/restarting into the same user session, the project's Tailscale endpoint returns without manually running `monitor remote start`, while unrelated Tailscale Serve entries remain unchanged.

## Phase 11: Agent integration contract and machine-readable status ← complete; verified on N100

Define the smallest stable interface that a CLI-capable agent needs in order to use the monitor without understanding its implementation.

Implement:

- `monitor status` with bounded, machine-readable JSON output
- a schema version in the status payload
- current task
- recent progress with IDs and timestamps
- all unanswered questions with IDs and timestamps
- a bounded set of recent answered questions with question IDs, question text, answers, and timestamps
- latest artifact metadata
- project metrics
- project identity without exposing local filesystem paths
- an `AGENT_INTEGRATION.md` document that defines when an agent should call each monitor command
- tests for the exact status schema and for empty, partial, and populated project state
- a read-only guarantee: reading status must not acknowledge, close, delete, or otherwise mutate monitor state

The contract must remain agent-neutral. Codex is the first consumer, not a dependency of the monitor.

Treat `monitor status` as a state snapshot rather than an event queue. Stable IDs and timestamps let an agent reason about repeated answers without requiring the read operation itself to mutate state. If real-project verification later shows that explicit answer acknowledgement is necessary, add it from observed need rather than assuming it now.

Keep the default payload small enough to place in agent context repeatedly. Do not return an unbounded activity history.

Do not add automatic resume in this phase.

Success condition: an agent can learn the current monitored state using one command and can determine which existing CLI action to take next without reading SQLite, HTML, or the monitor source code.

## Phase 12: Project onboarding for Codex and other agents ← complete; verified on N100

Make the integration easy to add to an existing project without copying this repository.

Implement:

- a command that emits or installs short agent instructions for the current project
- a short repository-level `AGENTS.md` managed block for Codex
- a focused repository skill under `.agents/skills/` for the repeatable monitor workflow, with `AGENTS.md` telling Codex when to use it
- the agent-neutral contract kept separately from the Codex adapter
- a generic Markdown instruction variant for CLI-capable agents that do not discover Codex repository skills
- safe handling of an existing `AGENTS.md`: do not overwrite unrelated project instructions
- explicit begin/end markers around monitor-owned `AGENTS.md` content so install, update, and removal are idempotent
- an uninstall or removal path for monitor-owned instruction and skill files
- documentation showing manual integration for agents that use a different instruction-file or skill mechanism

Keep the injected instructions short. They should point the agent to the monitor contract rather than duplicate the entire monitor manual.

Success condition: a fresh project can be prepared for Codex with a small number of commands, Codex receives persistent monitor instructions, and removing the integration does not damage unrelated project instructions.

## Phase 13: Real agent workflow verification and v1.0 gate ✅

Verify the monitor in a real development project rather than adding more infrastructure.

Run real Codex tasks where the human does not manually issue monitor commands on the agent's behalf.

Use at least two scenarios: one normal task that should complete without a human question, and one task with a genuine decision point that requires a human answer. Scenario A and Scenario B passed on N100, including transcript-independent continuation from a dashboard answer. This checks both over-questioning and failure to ask.

Verify this sequence:

1. the agent reads the integration instructions
2. the agent reads `monitor status`
3. the agent records the active task
4. the agent records meaningful progress checkpoints
5. the agent records a human question when a decision is actually required
6. the human answers from the iPhone dashboard
7. a later agent turn/run reads the stored answer
8. the agent registers a reviewable artifact when appropriate
9. the agent updates project-specific metrics when the project defines them
10. the agent marks the task complete
11. the monitor does not receive noisy progress events for trivial internal steps
12. a fresh later agent run can reconstruct the relevant state without relying on the previous chat transcript
13. a task that does not require human input completes without creating a gratuitous question

Judge these behaviors from the monitor database/API and agent command history where available, not only from the final prose response.

Repeat the same contract with one non-Codex CLI-capable agent if a suitable agent is available. This second-agent check is a portability test, not a requirement to add vendor-specific code.

Success condition: the complete human/agent loop works in a real project with no manual database edits and no need for the human to translate normal agent activity into monitor commands.

If the success condition is met and no blocking defects remain, release `1.0.0`.

## Release v1.0.0: Formal GitHub release ✅

Freeze the verified Phase 13 result as the first formal release.

Work:

- prepare release notes
- verify the final release-preparation commit in CI
- create Git tag `v1.0.0`
- create the GitHub Release from that tag
- verify the tag resolves to the intended commit and the release notes describe both capabilities and known limits

Do not add functional changes while preparing the release.

## Phase 14: Diagnostics and recovery

Add read-only operational diagnosis before increasing automation.

Primary command:

```text
monitor doctor
```

Initial checks should cover:

- CLI/package version
- project identity and runtime paths
- SQLite readability and schema compatibility
- current task and unanswered-question state
- agent onboarding files and managed-block consistency
- stale or inconsistent runtime state
- backend process identity
- Tailscale Serve mapping consistency when remote access is configured
- runtime log growth and other actionable local warnings

Provide machine-readable output in addition to concise human output.

Do not add broad automatic repair in the first implementation. Diagnosis and repair remain separate until concrete safe fixes are defined.

Success condition: healthy state is reported clearly, and deliberately introduced DB/runtime/remote inconsistencies are identified without destructive changes.

## Phase 15: Durable notifications

Notify the human when attention is required without making an external messaging service the source of truth.

Add a durable notification outbox with enough state to distinguish creation, delivery, retry, and failure. Notification events should reference the monitor entity that caused them, such as a question ID.

Implement one notification adapter first. Add additional adapters only after the event/outbox boundary is proven.

Required failure behavior:

- temporary delivery failure remains pending
- retry does not create duplicate logical notifications
- process restart does not lose pending notifications
- successful delivery is recorded

Success condition: a real agent question reaches the user's iPhone without the dashboard already being open, while SQLite remains the authoritative question/answer store.

## Phase 16: Runner lifecycle and automatic resume

Model runner state before automatically starting or resuming an agent.

Keep runner-specific behavior behind adapters. Do not put Codex-specific resume logic into the monitor core.

Distinguish:

- human answer
- resume request
- resume attempt
- runner state

At minimum, runner state must distinguish running, waiting for human input, stopped, completed, and failed.

Required safety behavior:

- one answer cannot create duplicate concurrent resumes
- a completed task is not resumed
- a missing or expired runner is handled explicitly
- restart/recovery does not silently launch duplicate agents
- failures remain inspectable

Success condition: after a human answers from the iPhone, the appropriate Codex workflow continues and completes without the human manually starting another run.

## Phase 17: Multi-project registry

Provide one human entry point for multiple monitored projects without merging their project databases.

The registry should consume stable monitor status/health boundaries rather than reading every project's SQLite schema directly.

Show at least:

- project identity
- current task
- unanswered-question count
- last activity
- health/availability
- project dashboard link

Success condition: the user can open one iPhone URL, see which projects need attention, and navigate to each independent dashboard.

## Phase 18: Automatic telemetry

Collect only measurements that can be observed reliably.

Candidate measurements include:

- task/run duration
- tool failures
- question count
- human wait time
- resume count
- provider-reported token/cost/cache values when authoritative data is available

Keep manual project metrics supported. Automatically collected metrics should record their source and observation time.

Do not infer cost, quality, or error rates from incomplete data.

Success condition: useful operational measurements appear without human entry and their provenance is clear.

## Phase 19: Second-agent portability verification

Verify the agent-neutral contract with one non-Codex CLI-capable agent.

Repeat the core Phase 13 loop:

- read status
- manage task/progress
- ask a real human question
- receive the iPhone answer
- continue from a fresh run
- complete the task

Prefer adapter/instruction changes over monitor-core changes.

Success condition: the same monitor core supports the second agent without vendor-specific state leaking into the core model.

## Explicitly out of scope for the first MVP

Do not add these before the local progress workflow works:

- cloud hosting
- authentication
- Slack integration
- Discord integration
- Telegram integration
- automatic agent restart or resume
- automatic LLM cost calculation
- multiple users
- multiple-agent orchestration
- Docker unless a concrete need appears
- PostgreSQL or Redis
- React or another frontend framework unless plain HTML and JavaScript become insufficient
