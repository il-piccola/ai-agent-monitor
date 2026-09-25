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


## Phase 10: Windows logon startup ← revised implementation complete, N100 verification pending

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
