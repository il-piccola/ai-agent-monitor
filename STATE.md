# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Completed phases: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
- Current phase: Phase 11 planned and validated; implementation not started

## Verified deployment

The original monitor deployment runs on the N100 Windows machine through Tailscale Serve.

- production backend: `127.0.0.1:8765`
- production Tailscale HTTPS port: `9443`
- production tailnet URL: `https://leto.taile04360.ts.net:9443/`
- iPhone access: verified through Phase 10
- Phase 8 installed CLI and project isolation: verified on N100
- Phase 9 project-specific remote access: verified on N100 and iPhone
- Phase 10 per-user logon startup: verified on N100; the Project A test launcher was removed after verification
- production backend currently runs on port 8765 (PID 9760); production auto-start remains unconfigured

## Phase 10 status

The first Task Scheduler implementation could not register as the normal `LETO\ilpic` user, so it was replaced by a per-user Windows Startup-folder launcher. Package version `0.10.2` is installed on the N100.

Verification results:

- all 56 standard-library tests passed on the N100
- Project A's Startup launcher installed as the normal user without elevation
- after sign-out and re-login, Project A remote started without a manual `remote start` command and returned HTTP 200
- the first logon run reused port 8765 after the original production process ended at sign-out, so the existing 9443 mapping temporarily served Project A; Project A was stopped and the original production backend was restored
- port selection was fixed to reserve local backend ports still referenced by Tailscale Serve proxies, and a regression test covers this collision
- the startup success message was fixed to report the launcher path
- with production active, Project A used backend 8766 / HTTPS 9444; both dashboards returned HTTP 200, the production URL remained the standard dashboard, and the iPhone showed `Project A Monitor` with Project A metrics
- after verification, the Project A Startup launcher and remote Serve entry were removed; startup and remote status both report inactive
- final production local HTTP 8765 and tailnet HTTPS 9443 both returned HTTP 200; Serve mappings 443 -> 4174, 8443 -> 5173, and 9443 -> 8765 remained unchanged
- production automatic startup was not configured; the current production process runs in the logged-in user's session and will end at a later sign-out

## Continuous integration

A GitHub Actions workflow runs package installation, `monitor --help`, and the standard-library test suite on Windows and Ubuntu with Python 3.10 and 3.12. The N100 logon and iPhone checks were also completed for Phase 10.

## Next task

Implement Phase 11 only.

Phase 11 scope:

1. add bounded, read-only, machine-readable `monitor status`
2. include a schema version, project identity, current task, recent progress, open questions, recent answers, latest artifact, and metrics
3. include stable IDs and timestamps where the underlying records provide them
4. add `AGENT_INTEGRATION.md` with agent-neutral rules for when to call each existing monitor command
5. test empty, partial, and populated state and verify that reading status does not mutate monitor state

Do not begin Phase 12 onboarding, automatic resume, notifications, or automatic telemetry until Phase 11 is verified.

## Not implemented yet

- Slack, Discord, or Telegram integration
- automatic agent resume
- automatic LLM cost calculation
- separate application-level authentication beyond Tailscale tailnet membership
- pre-login Windows service startup

## Local workspace note

`uv tool install --force .` may generate `build/` and `*.egg-info/` directories in the checkout. These build artifacts are ignored by Git and do not need to be deleted.

## Handoff instruction

A new assistant or developer should read, in this order:

1. `README.md`
2. `IMPLEMENTATION_PLAN.md`
3. `ARCHITECTURE.md`
4. `STATE.md`

Then perform the task under **Next task**.
