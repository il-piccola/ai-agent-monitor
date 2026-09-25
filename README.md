# ai-agent-monitor

A small local monitor for long-running AI agent work.

The goal is to let a human see, at a glance, what an AI agent is doing without reading its full logs. The monitor will grow gradually from a simple local dashboard into a reusable tool that other projects can call.

## Current status

Phases 1 through 10 are verified on the N100. This includes Phase 9 tailnet-only smartphone access and Phase 10 per-user Windows logon startup for a test project.

Phases 11 and 12 are also verified on the N100. Phase 13 Scenario A passed after the installed CLI path fallback was added; Scenario B remains to be verified.

The dashboard supports progress updates, a current task, unanswered questions, browser-submitted answers, registered artifact snapshots, and project-specific metrics. The installed CLI keeps each project's data and optional dashboard separate. The data is stored locally and the dashboard refreshes automatically.

## Run the dashboard

Requirements:

- Python 3
- no external Python packages

From the repository directory:

```bash
python monitor.py
```

Then open:

```text
http://127.0.0.1:8765
```

Use `Ctrl+C` in the terminal to stop the server.

A different local port can be selected with:

```bash
python monitor.py --port 9000
```

The server intentionally listens only on `127.0.0.1`.

## Record progress

Phase 2 adds progress recording without requiring any external Python package.

From the repository directory:

```bash
python monitor.py progress "Login page completed"
```

The message is stored in:

```text
.agent-monitor/monitor.db
```

That runtime database is ignored by Git.

While the server is running, the dashboard requests `/api/progress` every three seconds and displays the newest recorded messages first.

Phase 8 packages the project as a reusable `monitor` CLI. The legacy `python monitor.py ...` entry point remains available for the source checkout.

## Initial goals

The monitor should eventually show:

- the agent's current task
- recent meaningful progress
- questions that need a human answer
- the latest generated artifacts
- optional project-specific metrics such as cost or error counts

The dashboard is intentionally project-specific. The shared tool provides the recording and retrieval mechanism; each project may decide what information to display.

## Development approach

We are starting small and completing one phase before adding later features.

See:

- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the staged development plan
- [ARCHITECTURE.md](ARCHITECTURE.md) for design decisions
- [STATE.md](STATE.md) for the current project state and next task

## Repository policy

This repository is public. Do not commit API keys, access tokens, private project data, personal information, or runtime databases.


## Serve inside a Tailscale tailnet

The repository includes helpers for deploying the dashboard through Tailscale Serve.

### Linux

From a checkout on the Tailscale server:

```bash
git pull
bash deploy/tailscale-serve.sh
```

The script:

- keeps the Python backend bound to `127.0.0.1`
- finds a free backend port in `8765-8799`
- avoids HTTPS ports `443` and `8443`
- checks existing Tailscale Serve configuration before choosing an HTTPS port
- chooses a free Tailscale HTTPS port from `9443-9499` or `10443-10499`
- starts the monitor in the background
- verifies that the local dashboard returns HTTP 200
- configures `tailscale serve --bg` as an HTTPS reverse proxy

The selected ports and process ID are written only to:

```text
.runtime/ports.env
```

Runtime files are ignored by Git.

To stop this deployment:

```bash
bash deploy/tailscale-stop.sh
```

Tailscale Serve exposes the application only inside the tailnet. This is intentionally different from Tailscale Funnel, which exposes a service to the public internet.

### Windows (PowerShell)

Requirements: Tailscale, PowerShell 7, and an installed Python interpreter available through `uv python find --no-project`.

From the repository directory:

```powershell
./deploy/tailscale-serve.ps1
```

The Windows helper chooses unused ports in the same ranges as the Linux helper, checks existing Serve configuration, starts the backend in a hidden process, and writes the selected ports and process ID to `.runtime/ports.env`. It leaves existing Serve ports in place.

To stop this deployment:

```powershell
./deploy/tailscale-stop.ps1
```

The backend does not start automatically after a Windows reboot. Run the start helper again after reboot if needed.


## Run tests

The storage tests use only Python's standard library:

```bash
python -m unittest discover -s tests -v
```


## Track the current task

Phase 3 adds one active task at a time.

Start or replace the current task:

```bash
python monitor.py task start "Build login page"
```

Complete the current task:

```bash
python monitor.py task done
```

The current task is stored in the same local SQLite database and appears in the dashboard through `/api/task`. The dashboard refreshes it every three seconds.


## Record a question

Phase 4 lets the agent record a question for the human:

```bash
python monitor.py ask "Should I use option A or option B?"
```

Questions are stored in the local SQLite database with an `open` status. The dashboard requests `/api/questions` every three seconds, shows the number of unanswered questions, and lists the newest questions first.

Phase 4 displays unanswered questions. Phase 5 adds browser-based answers.


## Answer a question from the dashboard

Each unanswered question has an answer form below it.

When the human submits an answer:

- the browser sends it to `POST /api/questions/<id>/answer`
- the answer and answer time are stored in SQLite
- the question changes from `open` to `answered`
- the question disappears from the unanswered list
- automatic agent resumption does not occur

An agent can read recent stored answers with:

```bash
python monitor.py answers
```

The same data is also available from:

```text
GET /api/answers
```

Existing Phase 4 databases are migrated automatically by adding the answer columns when the application first opens the database after updating.


## Register a generated artifact

Phase 6 snapshots a generated file into the monitor's local runtime storage.

From the directory that contains the file:

```bash
python monitor.py artifact ./report.html --name "Benchmark report"
```

Registration:

- only accepts a regular file inside the current working directory
- copies the file into `.agent-monitor/artifacts/`
- computes its SHA-256
- records its size, MIME type, timestamp, and Git commit when available
- keeps the registered snapshot unchanged if the original file changes later

The latest registered artifact is available from:

```text
GET /api/artifacts/latest
```

and appears in the dashboard as a link.

Artifact files are served through:

```text
GET /artifacts/<id>
```

HTML artifacts are served with a browser sandbox so they do not inherit the monitor dashboard's same-origin privileges.


## Record project-specific metrics

Phase 7 provides arbitrary project metrics without defining a fixed schema for every project.

Set a metric:

```bash
python monitor.py metric set cost.total 90.71 --label "Total cost" --unit USD
```

Record a project-specific value:

```bash
python monitor.py metric set benchmark.score 0.87 --label "Benchmark score"
python monitor.py metric set tasks.completed "6 / 12" --label "Tasks completed"
```

Update only the value:

```bash
python monitor.py metric set cost.total 91.20
```

The existing label and unit are preserved when they are omitted during an update.

Delete a metric:

```bash
python monitor.py metric delete benchmark.score
```

Read the stored metrics:

```bash
python monitor.py metrics
```

They are also available from:

```text
GET /api/metrics
```

The dashboard refreshes the metric cards every three seconds. Values are stored as display text on purpose, so each project can decide whether a metric represents money, counts, percentages, ratios, benchmark results, or another value. Phase 7 does not calculate LLM costs automatically.


## Install the shared CLI

Phase 8 packages the monitor so it can be installed once on a machine and called from multiple projects.

From this source checkout:

```bash
uv tool install --force .
```

Or install directly from GitHub:

```bash
uv tool install --force git+https://github.com/il-piccola/ai-agent-monitor.git
```

The package installs both `monitor` and `ai-agent-monitor` commands.

Each command uses the current working directory as the monitored project root. Runtime data therefore stays inside that project's:

```text
.agent-monitor/
```

For example:

```bash
cd project-a
monitor init
monitor progress "Project A started"
monitor metric set build.status passing --label "Build"

cd ../project-b
monitor init
monitor progress "Project B started"
```

Project A and Project B use separate SQLite databases.

Start a dashboard for the current project with:

```bash
monitor serve --port 8765
```

The older source-checkout form remains compatible:

```bash
python monitor.py --port 8765
```

### Project-specific dashboard

By default, an installed CLI uses its bundled dashboard.

To create an editable dashboard override for only the current project:

```bash
monitor init --dashboard
```

This copies the bundled dashboard to:

```text
.agent-monitor/dashboard.html
```

The project-local dashboard takes precedence when that project's server starts. The nested `.agent-monitor/.gitignore` ignores the runtime database and artifact snapshots while allowing the dashboard override to be version-controlled if desired.


## Remote smartphone access with Tailscale

Phase 9 adds project-local remote lifecycle commands to the installed CLI.

From a monitored project directory:

```bash
monitor remote start
```

The command:

- keeps the Python backend on `127.0.0.1`
- starts the backend in the background
- reads the existing Tailscale Serve configuration
- avoids existing Serve ports, including the established 443, 8443, and 9443 services
- selects an unused HTTPS port from `9443-9499` or `10443-10499`
- configures Tailscale Serve, not Funnel
- prints the tailnet-only HTTPS URL
- stores runtime state under `.agent-monitor/runtime/`

Check it with:

```bash
monitor remote status
```

Stop only the current project's remote endpoint with:

```bash
monitor remote stop
```

Before changing a Tailscale Serve entry or terminating a saved PID, the monitor checks that the state still belongs to the current project and that the Serve proxy still points to the expected backend. A reused or unverified live PID is not terminated.

The application itself does not add a separate username/password layer in Phase 9. Access control is provided by membership in the Tailscale tailnet. The command does not enable Tailscale Funnel and therefore does not intentionally publish the dashboard to the public internet.

The existing repository deployment helpers remain available for the original N100 checkout. The `monitor remote ...` commands are intended for installed-CLI use from arbitrary monitored projects.


## Windows logon startup

Phase 10 adds optional per-user Windows logon startup for project remotes without requiring administrator rights.

From the project directory:

```powershell
monitor startup install
```

The command writes a project-specific `.cmd` launcher into the current Windows user's Startup folder. The launcher calls the project's PowerShell startup script from `.agent-monitor/runtime/`, uses the installed Python environment directly, changes to the project directory, and starts that project's `monitor remote start`.

Check it with:

```powershell
monitor startup status
```

Remove it with:

```powershell
monitor startup remove
```

Each launcher filename includes the project's hashed project ID, so different monitored projects use different Startup-folder entries.

The startup script first checks whether the project's remote endpoint is already healthy. If not, it retries startup for up to roughly one minute so a slightly delayed Tailscale service does not immediately cause a permanent failure.

The initial Task Scheduler approach was dropped after the N100 verification showed that the normal `LETO\ilpic` session could not register scheduled tasks. The Startup-folder implementation is intentionally per-user and does not require switching to another administrator account.

This runs after the Windows user logs in. It is not a pre-login Windows service.


## Agent status snapshot

Phase 11 adds a read-only machine interface for agents:

```bash
monitor status
```

The command prints bounded JSON containing the project identity, current task, recent progress, all unanswered questions, recent answers, latest artifact metadata, and project metrics.

The payload includes `schema_version: 1`. Recent progress and answered-question history are limited to the newest 10 records by default; unanswered questions are not hidden by that history limit.

Reading status does not acknowledge answers, close questions, or complete tasks. On a project with no monitor database yet, it returns an empty status snapshot without creating the database.

Agent behavior is defined in `AGENT_INTEGRATION.md`. The contract is intentionally independent of Codex or another specific runner. Phase 12 adds optional project onboarding for Codex and other agents; Phase 11 does not install agent instructions into other repositories.


## Agent onboarding

Phase 12 adds project-level onboarding without copying the monitor source repository.

For Codex:

```bash
monitor agent install codex
```

This installs three project files:

- a short AI Agent Monitor managed block in the repository-level `AGENTS.md`
- `.agents/skills/ai-agent-monitor/SKILL.md`
- `AI_AGENT_MONITOR.md`, the agent-neutral monitor contract

Existing text outside the managed `AGENTS.md` block is preserved. Re-running the command updates the monitor-owned block and files without adding duplicate blocks.

Remove the Codex integration with:

```bash
monitor agent remove codex
```

Removal deletes only the monitor-owned block and unchanged monitor-owned files. If the generated contract or skill was edited after installation, removal refuses to delete that modified file.

For another CLI-capable agent that does not use Codex repository skills:

```bash
monitor agent install generic
```

This installs only `AI_AGENT_MONITOR.md`. Point that agent's own instruction mechanism at the file.

To print instructions without changing the project:

```bash
monitor agent emit generic
monitor agent emit codex
```

Phase 12 does not automatically start an agent, resume a run, or modify vendor-specific configuration outside the files above.
