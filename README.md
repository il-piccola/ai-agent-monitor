# ai-agent-monitor

A small local monitor for long-running AI agent work.

The goal is to let a human see, at a glance, what an AI agent is doing without reading its full logs. The monitor will grow gradually from a simple local dashboard into a reusable tool that other projects can call.

## Current status

Phases 1 through 7 are implemented and verified on the N100 and iPhone through Tailscale Serve.

The dashboard supports progress updates, a current task, unanswered questions, browser-submitted answers, registered artifact snapshots, and project-specific metrics. The data is stored locally and the dashboard refreshes automatically.

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
