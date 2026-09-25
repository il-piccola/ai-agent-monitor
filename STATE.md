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
- automatic Windows logon startup: Phase 10 implementation pending N100 verification

## Phase 10 implementation

Phase 10 is implemented in `main` and needs N100 verification.

Implemented behavior:

- package version is `0.10.0`
- `monitor startup install` creates a Windows Task Scheduler `ONLOGON` task for the current project
- task names use the current project's hashed project ID
- startup files live under the project's ignored `.agent-monitor/runtime/`
- the launcher uses the Python executable from the installed tool environment, so the CLI bin directory does not need to be on PATH
- the launcher changes to the saved project directory before starting remote access
- it checks `remote status` first and exits successfully if the endpoint is already healthy
- otherwise it retries `remote start` up to 12 times with five-second pauses while Tailscale initializes
- `monitor startup status` checks whether the expected project-specific task exists
- `monitor startup remove` removes only the current project's matching task and local startup files
- startup state from a different project or a mismatched task name is refused
- installation cleanup removes a newly-created task if local startup-state persistence fails
- non-Windows platforms reject startup integration explicitly
- Phase 9 remote lifecycle and the established production 8765/9443 service are unchanged

The standard-library test suite now contains 52 tests, including Task Scheduler naming, PowerShell path escaping, install/status/remove behavior, project-boundary refusal, non-Windows refusal, and the Tailscale-startup retry script.

## Next task

On the N100 machine:

1. pull the latest `main`
2. run `python -m unittest discover -s tests -v` and confirm all 52 tests pass
3. reinstall the CLI with `uv tool install --force .`
4. confirm the installed package reports version `0.10.0`
5. use the Phase 8 Project A test directory and ensure its Phase 9 remote is currently stopped
6. run `monitor startup install` from Project A
7. run `monitor startup status` and verify the expected project-specific task is installed
8. inspect Task Scheduler or `schtasks /Query` and confirm the task trigger is user logon and the task command points to Project A's runtime PowerShell script
9. sign out and back in, or reboot and log in to the same Windows user
10. without manually running `monitor remote start`, verify Project A's `monitor remote status` reports `backend_alive: true` and `tailscale_active: true`
11. verify the Project A tailnet URL returns HTTP 200 from the N100 and iPhone
12. verify production HTTPS 9443 and existing Serve mappings on 443 and 8443 remain unchanged
13. run `monitor startup remove` from Project A
14. verify the scheduled task is gone
15. run `monitor remote stop` in Project A so the temporary Phase 10 endpoint is also stopped
16. confirm production backend 8765 and HTTPS 9443 still return HTTP 200

Do not configure startup for the production repository service during this first Phase 10 verification. Use only the disposable Project A test project.

## Not implemented yet

- Phase 10 N100 logon/reboot verification
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
