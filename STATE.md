# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Completed phases: 1, 2, 3, 4, 5
- Current phase: 6

## Verified deployment

The app runs on the N100 Windows machine through Tailscale Serve.

- backend: `127.0.0.1:8765`
- Tailscale HTTPS port: `9443`
- tailnet URL: `https://leto.taile04360.ts.net:9443/`
- iPhone access: verified
- automatic start after Windows reboot: not implemented

Phases 2 through 5 are verified end to end on the N100 and iPhone.

## Phase 6 implementation

Phase 6 is implemented in `main` and needs N100/iPhone verification.

Implemented behavior:

- `python monitor.py artifact <path> --name "<label>"` registers a file
- registration only accepts a regular file inside the current working directory
- the file is copied to `.agent-monitor/artifacts/`
- the snapshot gets SHA-256, size, MIME type, timestamp, and Git commit when available
- SQLite stores artifact metadata
- `GET /api/artifacts/latest` returns the latest artifact
- `GET /artifacts/<id>` serves the stored snapshot
- the dashboard shows the latest artifact name, timestamp, size, short hash, and optional Git commit
- HTML artifacts use a sandboxed browser context
- artifact snapshots are ignored by Git

The test suite now includes Phase 6 storage, immutability, newest-artifact, and path-safety tests. The current Chat execution environment cannot resolve GitHub hosts for a clean local checkout, so the test suite must be run on the N100 before end-to-end verification.

## Next task

On the N100 machine:

1. pull the latest `main`
2. run `python -m unittest discover -s tests -v`
3. restart the existing deployment
4. create a small `phase6-test.html` inside the repository
5. register it as `Phase 6 iPhone test`
6. verify local and Tailscale `/api/artifacts/latest` return HTTP 200 and the registered metadata
7. verify the artifact URL itself returns HTTP 200
8. delete or modify the original `phase6-test.html`
9. verify the registered artifact URL still shows the original snapshot
10. verify the iPhone dashboard shows `Phase 6 iPhone test` and its link opens the snapshot

Do not begin Phase 7 until these checks succeed.

## Not implemented yet

- Phase 6 N100/iPhone verification
- project-specific metrics
- packaging as a reusable CLI
- use from other projects
- Slack, Discord, or Telegram integration
- automatic agent resume
- automatic LLM cost calculation

## Handoff instruction

A new assistant or developer should read, in this order:

1. `README.md`
2. `IMPLEMENTATION_PLAN.md`
3. `ARCHITECTURE.md`
4. `STATE.md`

Then perform the task under **Next task** without adding Phase 7 features.
