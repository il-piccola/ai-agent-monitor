# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Completed phases: 1, 2, 3, 4, 5, 6, 7, 8
- Current phase: 9

## Verified deployment

The original monitor deployment still runs on the N100 Windows machine through Tailscale Serve.

- production backend: `127.0.0.1:8765`
- production Tailscale HTTPS port: `9443`
- production tailnet URL: `https://leto.taile04360.ts.net:9443/`
- iPhone access: verified through Phase 7
- Phase 8 installed CLI and project isolation: verified on N100
- automatic start after Windows reboot: not implemented

## Phase 9 implementation

Phase 9 is implemented in `main` and needs N100/iPhone verification.

Implemented behavior:

- package version is `0.9.0`
- `monitor remote start` starts the current project's backend in the background
- backend remains bound to `127.0.0.1`
- Tailscale Serve provides HTTPS inside the tailnet
- Tailscale Funnel is not enabled
- existing Serve ports are read before choosing a new port
- backend ports are selected from `8765-8799`
- HTTPS ports are selected from `9443-9499` or `10443-10499`
- remote state and logs are stored under the project's `.agent-monitor/runtime/`
- existing Phase 8 projects automatically gain the `runtime/` ignore rule without losing custom ignore entries
- `monitor remote status` reports backend and Tailscale Serve state
- `monitor remote stop` removes only the current project's verified Serve mapping and verified backend process
- reused or unverified live PIDs are not killed
- reassigned Tailscale Serve ports are not modified
- the project health API exposes a hashed project ID and directory name, not the full local path
- startup cleanup removes a newly-created Serve mapping if a later startup step fails
- legacy repository deployment scripts remain compatible with the production 8765/9443 service

The standard-library test suite now contains 45 tests, including remote port parsing, project-local state, old-project ignore migration, project-path privacy, stale-state safety, reassigned Serve-port safety, and PID-reuse protection.

## Next task

On the N100 machine:

1. pull the latest `main`
2. run `python -m unittest discover -s tests -v` and confirm all 45 tests pass
3. reinstall the CLI with `uv tool install --force .`
4. confirm the established production endpoint on backend 8765 / HTTPS 9443 still returns HTTP 200
5. use the existing Phase 8 Project A directory and run `monitor remote start`
6. run `monitor remote status` and confirm `configured`, `backend_alive`, and `tailscale_active` are true
7. verify the printed Project A tailnet URL returns HTTP 200 on the N100 and opens on the iPhone with Project A's custom dashboard and A-only data
8. while Project A remains remote, use Project B and run `monitor remote start`
9. verify Project B receives a different backend port and different Tailscale HTTPS port, opens on the iPhone, uses the bundled dashboard, and shows B-only data
10. run `monitor remote stop` in Project B and verify Project A still works and production HTTPS 9443 still returns HTTP 200
11. run `monitor remote stop` in Project A and verify its endpoint stops while production HTTPS 9443 still returns HTTP 200
12. confirm the existing Tailscale services on ports 443 and 8443 were not changed

Do not add Slack/Discord/Telegram integration, automatic agent resume, public Funnel access, or automatic cost calculation during this verification.

## Not implemented yet

- Phase 9 N100/iPhone verification
- Slack, Discord, or Telegram integration
- automatic agent resume
- automatic LLM cost calculation
- Windows reboot auto-start
- separate application-level authentication beyond Tailscale tailnet membership

## Local workspace note

`uv tool install --force .` may generate `build/` and `*.egg-info/` directories in the checkout. These build artifacts are ignored by Git and do not need to be deleted.

## Handoff instruction

A new assistant or developer should read, in this order:

1. `README.md`
2. `IMPLEMENTATION_PLAN.md`
3. `ARCHITECTURE.md`
4. `STATE.md`

Then perform the task under **Next task**.
