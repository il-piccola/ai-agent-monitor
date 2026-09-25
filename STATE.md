# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Completed phases: 1, 2, 3, 4, 5, 6, 7, 8, 9
- Current phase: 9 verified; Phase 10 not started

## Verified deployment

The original monitor deployment still runs on the N100 Windows machine through Tailscale Serve.

- production backend: `127.0.0.1:8765`
- production Tailscale HTTPS port: `9443`
- production tailnet URL: `https://leto.taile04360.ts.net:9443/`
- iPhone access: verified through Phase 7
- Phase 8 installed CLI and project isolation: verified on N100
- automatic start after Windows reboot: not implemented

## Phase 9 implementation

Phase 9 is implemented in `main` and verified on the N100 and iPhone on 2026-09-25.

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

N100 verification results:

- all 45 tests passed and CLI version `0.9.0` was installed
- Project A used backend `127.0.0.1:8766` and HTTPS `9444`; its dashboard title and metric were A-specific
- Project B used backend `127.0.0.1:8767` and HTTPS `9445`; its bundled dashboard and metric were B-specific
- both tailnet URLs returned HTTP 200 and were verified on the iPhone
- `/api/project` did not expose a Windows full path
- stopping B left A and production HTTPS `9443` responding; stopping A left production backend `8765` and HTTPS `9443` responding
- Tailscale Serve mappings for `443` (`127.0.0.1:4174`), `8443` (`127.0.0.1:5173`), and `9443` (`127.0.0.1:8765`) remained unchanged
- the Phase 9 project remotes are stopped; automatic start after Windows reboot is not configured

## Next task

Phase 9 verification is complete. Do not start Phase 10 until its scope is requested and agreed.

## Not implemented yet

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
