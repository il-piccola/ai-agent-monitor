# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Completed phases: 1, 2, 3, 4, 5, 6, 7
- Current phase: 8

## Verified deployment

The app runs on the N100 Windows machine through Tailscale Serve.

- backend: `127.0.0.1:8765`
- Tailscale HTTPS port: `9443`
- tailnet URL: `https://leto.taile04360.ts.net:9443/`
- iPhone access: verified
- automatic start after Windows reboot: not implemented

Phases 2 through 7 are verified end to end on the N100 and iPhone.

## Phase 8 implementation

Phase 8 is implemented in `main` and needs N100 verification.

Implemented behavior:

- Python package: `ai_agent_monitor`
- package metadata and console scripts in `pyproject.toml`
- installed commands: `monitor` and `ai-agent-monitor`
- root `monitor.py` remains a compatibility wrapper for existing deployment scripts
- current working directory becomes the project root
- each project stores its own `.agent-monitor/monitor.db` and artifact snapshots
- `monitor init` creates project runtime scaffolding
- `monitor init --dashboard` creates a project-specific dashboard override
- `monitor serve --port <port>` serves the current project's dashboard/data
- bundled dashboard is included as package data
- tests invoke the package implementation directly
- a subprocess integration test verifies that two separate working directories keep different metrics and different SQLite databases

The standard-library test suite now contains 34 tests.

## Next task

On the N100 machine:

1. pull the latest `main`
2. run `python -m unittest discover -s tests -v`
3. restart the existing Tailscale deployment and confirm the legacy `monitor.py` wrapper still serves the current monitor
4. install the tool from the checkout with `uv tool install --force .`
5. confirm `monitor` is available; if it is not on PATH, use the executable under `uv tool dir --bin`
6. create two empty test projects outside this repository, for example `phase8-project-a` and `phase8-project-b`
7. in Project A, run `monitor init` and `monitor metric set project.name A --label Project`
8. in Project B, run `monitor init` and `monitor metric set project.name B --label Project`
9. verify `monitor metrics` in A returns A and in B returns B
10. verify each project has its own `.agent-monitor/monitor.db`
11. in Project A, run `monitor init --dashboard`, customize a visible heading, then run `monitor serve` on an unused local port and confirm the customized dashboard is served
12. confirm Project B still uses the bundled dashboard and its own data

Do not begin Phase 9 until these checks succeed.

## Not implemented yet

- Phase 8 N100 verification
- Slack, Discord, or Telegram integration
- automatic agent resume
- automatic LLM cost calculation
- Windows reboot auto-start

## Handoff instruction

A new assistant or developer should read, in this order:

1. `README.md`
2. `IMPLEMENTATION_PLAN.md`
3. `ARCHITECTURE.md`
4. `STATE.md`

Then perform the task under **Next task** without adding Phase 9 features.
