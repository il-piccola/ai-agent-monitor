# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Completed phases: 1, 2, 3, 4, 5, 6, 7, 8, 9
- Current phase: 10

## Verified deployment

The original monitor deployment runs on the N100 Windows machine through Tailscale Serve.

- production backend: `127.0.0.1:8765`
- production Tailscale HTTPS port: `9443`
- production tailnet URL: `https://leto.taile04360.ts.net:9443/`
- iPhone access: verified through Phase 9
- Phase 8 installed CLI and project isolation: verified on N100
- Phase 9 project-specific remote access: verified on N100 and iPhone
- Phase 10 Startup-folder launcher: Project A autostarted after re-login; final isolated-port and iPhone checks pending

## Phase 10 status

The first Phase 10 implementation used Windows Task Scheduler. N100 verification reached the registration step and failed with `Access is denied` because the normal `LETO\ilpic` user cannot register the scheduled task. No task was created, Project A remote remained stopped, and the production 443/8443/9443 Serve mappings and backend 8765 remained unchanged.

Because using another administrator account could change the logon-user behavior, the Task Scheduler approach was not continued.

Phase 10 has now been revised in `main`:

- package version is `0.10.2`
- `monitor startup install` uses the current user's Windows Startup folder instead of Task Scheduler
- administrator rights are not required by the monitor for registration
- the Startup-folder launcher filename includes the current project's hashed project ID
- the launcher calls the project's ignored `.agent-monitor/runtime/startup.ps1`
- the installed Python executable is used directly, so `C:\Users\ilpic\.local\bin` does not need to be on PATH
- the launcher changes to the saved project directory
- the PowerShell script checks `remote status` before starting another remote
- it retries `remote start` up to 12 times with five-second delays while Tailscale initializes
- `monitor startup status` verifies both local startup state and the expected per-user launcher
- `monitor startup remove` removes only the current project's matching launcher and runtime startup files
- mismatched project state or launcher paths are refused
- non-Windows platforms reject startup integration explicitly
- Phase 9 remote lifecycle and the production 8765/9443 service are unchanged

The 53 tests on `main` passed on the N100. Project A's per-user Startup launcher was installed without elevation, and after signing out and back into LETO\ilpic, it automatically started the remote monitor and returned HTTP 200. The first run reused backend port 8765 because the old production process ended at sign-out while its existing Serve mapping still pointed to that port, so the production URL temporarily served Project A. Project A was stopped and the original production server was restarted on 8765; production HTTPS 9443 again returned the standard dashboard. Backend selection was fixed to reserve local proxy target ports referenced by Tailscale Serve, with tests covering the collision. The startup CLI success message was also fixed. The revised suite now has 56 passing tests, and CLI 0.10.2 is installed. Under 0.10.2, Project A now runs at backend 8766 / HTTPS 9444 while the production dashboard remains at 8765 / HTTPS 9443. The Project A Startup launcher remains installed; final iPhone verification and cleanup are pending.

## Continuous integration

A GitHub Actions workflow now runs package installation, `monitor --help`, and the full standard-library test suite on Windows and Ubuntu with Python 3.10 and 3.12. This supplements N100 verification but does not replace the real Windows logon/iPhone test required to complete Phase 10.

## Next task

1. Verify Project A at `https://leto.taile04360.ts.net:9444/` on the iPhone.
2. Run `monitor startup remove` in Project A and confirm `startup status` reports not installed.
3. Run `monitor remote stop` in Project A.
4. Confirm production backend 8765 and HTTPS 9443 still return the standard dashboard and Serve mappings 443, 8443, and 9443 are unchanged.

Do not configure automatic startup for the production repository service.

## Not implemented yet

- Phase 10 N100 logon/reboot verification for the revised Startup-folder implementation
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
