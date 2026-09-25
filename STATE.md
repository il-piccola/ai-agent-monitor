# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Completed phases: 1, 2, 3, 4, 5, 6
- Current phase: 7

## Verified deployment

The app runs on the N100 Windows machine through Tailscale Serve.

- backend: `127.0.0.1:8765`
- Tailscale HTTPS port: `9443`
- tailnet URL: `https://leto.taile04360.ts.net:9443/`
- iPhone access: verified
- automatic start after Windows reboot: not implemented

Phases 2 through 6 are verified end to end on the N100 and iPhone.

## Phase 7 implementation

Phase 7 is implemented in `main` and needs N100/iPhone verification.

Implemented behavior:

- `python monitor.py metric set <key> <value>` creates or updates a metric
- optional `--label` and `--unit` control dashboard display
- omitted label/unit are preserved when an existing metric value is updated
- an explicitly empty unit clears the stored unit
- `python monitor.py metric delete <key>` removes a metric
- `python monitor.py metrics` prints all stored metrics
- `GET /api/metrics` returns all metrics
- the dashboard shows responsive project-specific metric cards
- metric values are stored as text, so projects can choose their own formats
- metric text is inserted into the dashboard with `textContent`
- Phase 7 does not calculate LLM cost automatically

The standard-library test suite now contains 29 tests, including metric create/update, metadata preservation, deletion, sorting, default labels, and input validation.

## Next task

On the N100 machine:

1. pull the latest `main`
2. run `python -m unittest discover -s tests -v`
3. restart the existing deployment
4. register these test metrics:
   - `cost.total = 90.71`, label `Total cost`, unit `USD`
   - `tool.error_rate = 1.7`, label `Tool error rate`, unit `%`
   - `tasks.completed = 6 / 12`, label `Tasks completed`
5. verify local and Tailscale `/api/metrics` return HTTP 200 and all three values
6. verify the iPhone dashboard displays all three metric cards
7. update `cost.total` to `91.20` without repeating its label or unit, and verify the iPhone still shows `Total cost` and `USD`
8. delete `tool.error_rate` and verify it disappears from the API and iPhone

Do not begin Phase 8 until these checks succeed.

## Not implemented yet

- Phase 7 N100/iPhone verification
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

Then perform the task under **Next task** without adding Phase 8 features.
