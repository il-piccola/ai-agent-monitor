# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Completed phases: 1, 2, 3, 4
- Current phase: 5

## Verified deployment

The app is deployed on the N100 Windows machine through Tailscale Serve.

- backend: `127.0.0.1:8765`
- Tailscale HTTPS port: `9443`
- tailnet URL: `https://leto.taile04360.ts.net:9443/`
- iPhone access: verified
- automatic start after Windows reboot: not implemented

Phases 2 through 4 were verified end to end on the N100 and iPhone:

- progress messages appear on the iPhone
- current task start/done appears on the iPhone
- unanswered questions and their count appear on the iPhone

## Phase 5 implementation

Phase 5 is implemented in `main` and needs N100/iPhone verification.

Implemented behavior:

- each unanswered question has an answer form
- `POST /api/questions/<id>/answer` stores a browser answer
- answered questions change from `open` to `answered`
- answered questions leave `/api/questions` and the dashboard list
- `GET /api/answers` returns stored answers
- `python monitor.py answers` prints stored answers for an agent
- answers and timestamps are stored in SQLite
- existing Phase 4 databases are migrated automatically
- the question form is not rebuilt by the 3-second refresh while the user is typing
- automatic agent resumption is not implemented

The standard-library test suite now contains 17 tests, including answer storage and Phase 4 database migration. Run it on the N100 before the iPhone verification.

## Next task

On the N100 machine:

1. pull the latest `main`
2. run `python -m unittest discover -s tests -v`
3. restart the existing deployment
4. keep the existing `Phase 4 iPhone test?` question if it is still open; otherwise create `Phase 5 iPhone test?`
5. verify local and Tailscale `/api/questions` return the open question
6. answer the question from the iPhone dashboard with `Phase 5 answer`
7. verify the unanswered count decreases and the answered question disappears
8. verify local and Tailscale `/api/answers` contain `Phase 5 answer`
9. verify `python monitor.py answers` can read the stored answer

Do not begin Phase 6 until these checks succeed.

## Not implemented yet

- Phase 5 N100/iPhone verification
- artifact registration
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

Then perform the task under **Next task** without adding Phase 6 features.
