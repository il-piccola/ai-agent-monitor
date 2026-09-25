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
- Phase 10 automatic Windows logon startup: revised implementation awaiting N100 verification

## Phase 10 status

The first Phase 10 implementation used Windows Task Scheduler. N100 verification reached the registration step and failed with `Access is denied` because the normal `LETO\ilpic` user cannot register the scheduled task. No task was created, Project A remote remained stopped, and the production 443/8443/9443 Serve mappings and backend 8765 remained unchanged.

Because using another administrator account could change the logon-user behavior, the Task Scheduler approach was not continued.

Phase 10 has now been revised in `main`:

- package version is `0.10.1`
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

The repository test suite now contains 53 tests. The revised Startup-folder implementation has not yet been executed on the N100 because the user is currently away from the machine.

## Next task

When N100 access is available:

1. pull the latest `main`
2. run `python -m unittest discover -s tests -v` and confirm all 53 tests pass
3. reinstall with `uv tool install --force .`
4. confirm installed package version `0.10.1`
5. use only the disposable Phase 8 Project A directory; confirm its Phase 9 remote is stopped
6. run `monitor startup install` as the normal `LETO\ilpic` user without elevation
7. run `monitor startup status` and confirm `installed: true`
8. confirm the expected Project A `.cmd` launcher exists in the current user's Windows Startup folder
9. sign out and back in, or reboot and log in as the same `LETO\ilpic` user
10. without manually running `monitor remote start`, verify Project A `remote status` reports `backend_alive: true` and `tailscale_active: true`
11. verify the Project A tailnet URL returns HTTP 200 from the N100 and iPhone
12. verify production HTTPS 9443 and existing Serve mappings on 443 and 8443 remain unchanged
13. run `monitor startup remove` from Project A and confirm `startup status` becomes not installed
14. run `monitor remote stop` in Project A
15. confirm production backend 8765 and HTTPS 9443 still return HTTP 200

Do not configure automatic startup for the production repository service during this verification.

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
