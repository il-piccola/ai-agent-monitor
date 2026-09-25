# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Completed phases: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
- Current phase: 12 implementation complete; verification pending

## Verified deployment

The original monitor deployment runs on the N100 Windows machine through Tailscale Serve.

- production backend: `127.0.0.1:8765`
- production Tailscale HTTPS port: `9443`
- production tailnet URL: `https://leto.taile04360.ts.net:9443/`
- iPhone access: verified through Phase 10
- Phase 11 machine-readable status: verified on N100
- production automatic startup remains unconfigured

## Phase 12 implementation

Package version `0.12.0` implements project onboarding for Codex and other CLI-capable agents.

Implemented behavior:

- `monitor agent install codex` installs the Codex adapter
- repository-level `AGENTS.md` receives one marked AI Agent Monitor block
- existing `AGENTS.md` content outside that block is preserved
- `.agents/skills/ai-agent-monitor/SKILL.md` contains the Codex repository skill
- `AI_AGENT_MONITOR.md` contains the agent-neutral contract
- re-running Codex install updates monitor-owned content without duplicating the managed block
- malformed managed-block markers are refused before monitor-owned files are written
- `monitor agent remove codex` removes the managed block and unchanged generated files
- modified generated skill or contract files are not silently deleted
- `monitor agent install generic` installs only the agent-neutral contract
- `monitor agent emit generic|codex` prints integration text without changing the project
- bundled contract content is tested against the repository's canonical `AGENT_INTEGRATION.md`
- Phase 12 does not add automatic resume or vendor-specific runtime APIs

The standard-library test suite now contains 70 tests.

## Next task

Verify Phase 12 before beginning Phase 13.

On the N100:

1. pull the latest `main`
2. run the full test suite and confirm all 70 tests pass
3. reinstall the CLI with `uv tool install --force .`
4. confirm package version `0.12.0`
5. create a disposable Git project with an existing `AGENTS.md` containing unrelated instructions
6. run `monitor agent install codex`
7. confirm the original `AGENTS.md` text remains and exactly one monitor managed block exists
8. confirm `.agents/skills/ai-agent-monitor/SKILL.md` and `AI_AGENT_MONITOR.md` exist
9. run `monitor agent install codex` again and confirm no duplicate managed block appears
10. run `monitor agent remove codex` and confirm the unrelated `AGENTS.md` text remains while monitor-owned files are removed
11. run `monitor agent install generic` in a second disposable project and confirm only `AI_AGENT_MONITOR.md` is installed
12. run `monitor agent emit generic` and confirm it prints the contract without creating project files

Do not begin a real Codex autonomous workflow yet. That is Phase 13.

## Not implemented yet

- Phase 12 N100 installed-CLI verification
- Phase 13 real-agent workflow verification
- Slack, Discord, or Telegram integration
- automatic agent resume
- automatic LLM cost calculation
- separate application-level authentication beyond Tailscale tailnet membership
- pre-login Windows service startup

## Handoff instruction

A new assistant or developer should read, in this order:

1. `README.md`
2. `IMPLEMENTATION_PLAN.md`
3. `ARCHITECTURE.md`
4. `STATE.md`
5. `AGENT_INTEGRATION.md`

Then perform the task under **Next task**.
