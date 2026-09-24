# ai-agent-monitor

A small local monitor for long-running AI agent work.

The goal is to let a human see, at a glance, what an AI agent is doing without reading its full logs. The monitor will grow gradually from a simple local dashboard into a reusable tool that other projects can call.

## Current status

Phase 1 is complete and has been verified from an iPhone through Tailscale Serve.

Phase 2 is implemented in the repository and is awaiting verification on the N100 deployment.

Progress messages are now stored in a local SQLite database and exposed to the dashboard through `/api/progress`. The dashboard refreshes the progress list automatically.

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

The final reusable `monitor progress ...` command will be introduced later when the project is packaged as a shared CLI.

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

The repository includes helpers for deploying the Phase 1 dashboard through Tailscale Serve.

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
