# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Formal release: `v1.0.0` published
- Development package version: `1.6.11`
- Completed phases: 1 through 18
- Current work: active-project use and UI refinements, including the Phase 20 real-project reply path; Phase 19 remains deferred at the human's request

Version 1.6.11 fixes the dashboard's project-to-project telemetry mismatch: all ten known automatic-measurement slots now remain in the same order, with `—` when no source record exists. The Codex API-equivalent estimate and account-wide weekly balance are enabled for `ai-agent-monitor`, `nashiri-core`, `sleep-improvement-app`, and `moonlight-bamboo`. Project-specific values still come from each project's own records; a missing active task never produces an invented elapsed time. All 175 N100 tests and the dashboard JavaScript syntax check passed. The installed CLI was updated to 1.6.11, and all four Monitor URLs returned HTTP 200 on their original 9443–9446 ports. Headless Chromium at 900px and 390px confirmed identical section order, ten telemetry labels, two Codex cards, and card heights across the four projects. Each project's cost API returned a ready amount; the weekly balance was ready and consistent across all four. During the update, an orphaned old `nashiri-core` backend kept the uv tool environment locked; its project ID, process tree, and 8766 listener were verified before that old backend alone was stopped. The registered Monitor was then restored at 8766/9444. Existing game Serve entries were not changed. iPhone Safari was not tested in this pass.

## Verified baseline

Phase 14 read-only diagnostics and Phase 15 durable notifications are verified on the N100/iPhone.

Phase 15 final runtime configuration after verification:

- Telegram ON
- Email OFF
- one intended email recipient remains configured
- no pending notification remains
- production 8765/9443 and `nashiri-core` 8766/9444 remained HTTP 200
- Tailscale Serve mappings were unchanged

Version 1.2.1 multi-recipient email delivery passed the real two-recipient fan-out and recipient-removal tests.

## Phase 16 implementation

Version `1.3.1` includes an opt-in Codex runner lifecycle and automatic-resume path.

Durable records are separate:

- `answer_events`: human answers
- `agent_runners`: runner adapter, external identity, and lifecycle state
- `resume_requests`: whether one answer should resume a linked runner
- `resume_attempts`: each worker/Codex launch attempt

Runner states include `running`, `waiting_for_human`, `stopped`, `completed`, and `failed`.

Codex integration:

- the Codex repository skill now runs `monitor runner register codex` before `monitor status`
- registration reads the exact `CODEX_THREAD_ID` supplied to Codex shell tools
- `monitor ask` links the question to the registered runner and marks it waiting
- answering a linked question creates one durable resume request only when an active task exists and the runner is waiting
- automatic resume is OFF by default
- `monitor runner auto-resume on|off` controls the current project
- `monitor runner codex set --command ...` configures the executable when needed
- `monitor runner status` exposes runner/request/attempt state
- `monitor runner dispatch` forces one pending dispatch for verification/recovery
- `monitor runner retry <id>` is required to retry an ambiguous `uncertain` request

Safety behavior covered by tests:

- an answer cannot create duplicate resume requests
- only one active resume request is allowed per runner
- task completion between answer and dispatch cancels the resume
- runner completion between answer and dispatch cancels the resume
- a missing worker after restart becomes `uncertain` and is not silently relaunched
- worker/Codex failures remain inspectable
- auto-resume disabled leaves requests pending
- the worker resumes the exact stored Codex thread, never `--last`
- automatic Codex resume uses one-run config overrides `sandbox_mode="workspace-write"` and `approval_policy="on-request"`; it does not use `danger-full-access`
- runner config is project-local and ignored by Git
- doctor remains read-only and reports runner configuration/recovery warnings

The standard-library suite contains 133 tests.

## Current Codex integration basis

Current Codex CLI exposes `CODEX_THREAD_ID` to shell tool executions and supports non-interactive `codex exec resume <thread-id> <prompt>`. The Phase 16 adapter relies on those public CLI behaviors rather than scraping Codex rollout files.

## Phase 16 N100 verification (2026-09-27)

The first preflight found that N100 Codex CLI `0.155.0-alpha.16.4` rejects `--ask-for-approval`. The supported one-run configuration is:

```text
codex -c sandbox_mode="workspace-write" -c approval_policy="on-request" exec ...
```

In the disposable `phase16-runner-test` project, a fresh Codex run registered its exact thread ID, started a task, and asked which value to write. The initial run stopped with no result file and one open question. Telegram delivered the question; the human answered `ALPHA` on the iPhone dashboard. Without a manual Codex launch, the registered thread resumed, wrote exactly one `ALPHA` line, completed the task and runner, and left one completed request and attempt with exit code 0. A subsequent dispatch claimed and launched zero requests. The full iPhone path used the same `on-request` policy with the config override placed after `exec`.

The `1.3.1` shared argv builder places both config overrides before `exec`. Its N100 preflight and a separate exact `exec resume` invocation passed. Preflight now decodes Codex output as UTF-8 on Windows, avoiding the observed CP932 decode error. The standard-library suite has 133 passing tests.

In a separate disposable project, a synthetic missing worker PID changed one running request and attempt to `uncertain`; dispatch claimed and launched zero requests, and no worker process was started. The test project's auto-resume setting was returned to OFF and its 8767/9445 remote was stopped. Production 8765/9443 and `nashiri-core` 8766/9444 returned HTTP 200; the original Serve mappings remained in place.

## Phase 17 implementation

Version `1.4.0` adds `monitor registry add|list|remove`, a read-only `/api/status` snapshot, a registry aggregation API, and a Japanese `/registry` page. Registration verifies identity through each dashboard's HTTP API. The registry reads no other project's SQLite database and keeps unavailable projects visible. Older Monitor servers are supported through existing APIs.

The intended N100 deployment reuses the existing 8765/9443 server for the registry and registers both that dashboard and `nashiri-core` at 8766/9444. It must not create a new Serve port.

The standard-library suite passes all 140 tests on N100 for the Phase 17 implementation.

N100 runtime verification: the existing 8765/9443 deployment was restarted on the same ports. `/registry`, `/api/registry`, and `/api/status` returned HTTP 200. The registry reported `ai-agent-monitor` and `nashiri-core` as available; the latter reported its active task and one unanswered question. Backend 8766 and HTTPS 9444 remained HTTP 200. The Serve mappings for 443, 8443, 8444, 9443, and 9444 remained unchanged. On 2026-09-27, the human confirmed that both project cards are displayed on the iPhone and that both registry links open their respective dashboards. Phase 17 success conditions are met.

## Phase 18 implementation

Version `1.5.0` adds read-only `monitor telemetry`, `/api/telemetry`, and a Japanese automatic-telemetry dashboard section. Each numeric measurement includes its source and observation time. The measurements derive from task start/end records, question/answer timestamps, and automatic-resume attempt records. Manual metrics remain separate. Tool failures, provider tokens/cost/cache, quality, and error rates are not inferred without authoritative data.

`task_runs` begins recording completed or replaced tasks as version 1.5 handles them. Older completed tasks cannot be reconstructed. Reading telemetry on an empty project does not create a database. The standard-library suite passes 148 tests on N100.

N100 runtime verification: the existing 8765/9443 server was restarted on those same ports. The dashboard HTML contains the Japanese automatic-telemetry section and returned HTTP 200. `/api/telemetry` returned schema 1, project identity, six measurements from the existing project database, source metadata, and observation timestamps. `/api/registry` still listed both projects. Backend 8766 and HTTPS 9444 returned HTTP 200. Serve mappings for 443, 8443, 8444, 9443, and 9444 were unchanged. On 2026-09-27, the human confirmed that values and source labels are visible in the automatic-telemetry section on iPhone. Phase 18 success conditions are met.

## Next task

Complete the active-project trial in `nashiri-core` before Phase 19. It already has Codex onboarding, project-local task/progress/question/artifact records, and the same 8766/9444 remote. Version 1.5.0 was installed and its automatic telemetry returned eight measurements from this real project without a new Serve port. A real PowerShell `monitor status | ConvertFrom-Json` found that Japanese CLI JSON emitted under Python's CP932 stdout could become invalid when PowerShell decodes native output as UTF-8. Version 1.5.1 escapes non-ASCII text in CLI JSON. All 149 tests passed on N100; the installed CLI's `status` and `telemetry` both parsed successfully against the real project. Its 8766/9444 remote returned HTTP 200 with eight telemetry measurements and the existing active task. The human opened the real-project dashboard on iPhone and found that most fixed interface text was English. Version 1.5.2 localizes those dashboard labels and messages in Japanese, including telemetry source descriptions; project-recorded text remains unchanged. All 149 tests and the dashboard JavaScript syntax check passed on N100. The installed CLI was updated and the `nashiri-core` remote was restarted on the same 8766/9444 ports, with its active task and eight telemetry measurements intact. HTTP 200 and Japanese dashboard HTML were confirmed from N100 over both local and tailnet URLs for 8765/9443 and 8766/9444. Serve mappings for 443, 8443, 8444, 9443, and 9444 were preserved. The project's unrelated worktree edits remain untouched; an auto-added `.agent-monitor/.gitignore` line was restored so the worktree matches its pre-trial state. iPhone visual confirmation of the localized dashboard is pending. This is a usability fix discovered during the active-project trial, not a new phase. Phase 19 remains deferred.

The human then reported that the actual recent progress remained unreadable because its recorded messages were English. Version 1.5.3 adds a separate `message_ja` display translation for progress while preserving the original `message`; the dashboard prefers the translation. It supports translation at record time and later by stable progress ID. The agent contract now asks for human-readable progress in the reader's language. The N100 suite passes 152 tests, including legacy database migration, original-text preservation, and the CLI `--ja` syntax. The installed 1.5.3 CLI restarted the active project's remote on the same 8766/9444 ports; all 46 currently visible progress records now have Japanese text, either recorded originally or in `message_ja`. Its API and dashboard return HTTP 200 through 9444. A project-local instruction was added to `nashiri-core/AI_AGENT_MONITOR.md` so future progress is written in Japanese or includes a Japanese display translation. iPhone visual confirmation remains pending. Phase 19 remains deferred.

Version 1.5.4 also adds Japanese display translations for the current task, open questions, and artifact labels while retaining the original values. The N100 suite passes 156 tests, including translation preservation, CLI syntax, and replacement-task safety. The installed 1.5.4 CLI restarted the real project's remote on the same 8766/9444 ports. Its active task, open question #3, and latest artifact #21 now have Japanese display text; their English originals remain in the API. Progress translations cover the 48 records present at the last N100 check. The 9444 HTML uses all four translated display fields, and the task, questions, artifact, and progress APIs return their translations over tailnet. The project-local agent contract now instructs future agents to write task titles, progress, questions, and artifact labels in Japanese. A concurrent agent is still adding progress, so any new English-only records need a Japanese translation until that agent picks up the revised contract. iPhone visual confirmation remains pending. Phase 19 remains deferred.

Version 1.5.5 shows only the newest progress event by default and puts older events in a collapsible section. The section's open state survives ordinary three-second refreshes. No new server or Serve port is needed. All 156 tests and the dashboard JavaScript syntax check passed on N100. The installed CLI was updated to 1.5.5; the `nashiri-core` remote returned on the same backend 8766 and HTTPS 9444, where the localized HTML includes the collapsible progress section and HTTP returned 200. The API still returned 48 progress records. On 2026-09-28, the human confirmed the Japanese dashboard display and collapsed recent-progress view on iPhone. Phase 19 remains deferred.

## Phase 20 N100 reply-path verification

Version 1.6.0 adds an opt-in Telegram reply receiver. Outgoing question notifications can request a reply and persist the bot message ID and chat ID with the delivery. The receiver only accepts text from the configured private chat replying to the exact delivered notification, persists the Telegram update offset, and calls the existing answer transaction. No webhook or new Serve port is used. All 160 standard-library tests pass on N100, and CI passed on Windows and Ubuntu with Python 3.10 and 3.12.

In the disposable `phase15-notification-test` project, Japanese test question #6 was delivered once to Telegram. The human replied `確認` to that notification. A one-shot poll processed one update and stored one answer against question #6; `monitor status` then showed the answer and removed #6 from open questions. Reply receiving was turned off in this disposable project afterward. The real `nashiri-core` project's Telegram notifications and replies are now enabled, with email still off. Its existing backend 8766/HTTPS 9444 and production 8765/HTTPS 9443 all returned HTTP 200, and Tailscale Serve mappings were unchanged. The real project's background reply loop will be verified when its next genuine human question occurs; no synthetic question was inserted into its active task. Phase 19 remains deferred.

On 2026-09-28, `monitor startup install` registered a per-user Windows Startup-folder launcher for `nashiri-core` only. `monitor startup status` reports installed, and doctor reports startup, remote, and notification checks as OK. The installed launcher points to this project's directory and the installed tool's Python. Running its script while the remote was already healthy exited 0 without changing the backend PID. All established Serve mappings (443, 8443, 8444, 9443, 9444) matched their prior targets, and both local and tailnet URLs for the two Monitor servers returned HTTP 200. The Telegram bot token is present in the Windows user environment. Actual recovery after a Windows sign-out or reboot has not yet been observed; that is the remaining startup verification.

Version 1.6.1 renders each dashboard's browser title from the project directory name, which is the repository name for the deployed projects. The bundled dashboard also uses that name as its visible heading. A project-specific dashboard keeps its own heading and body while receiving the project title. No new backend or Serve port is needed; the N100 standard-library suite passes 162 tests.

N100 deployment verification: the installed CLI is 1.6.1. `nashiri-core` restarted on the existing 8766/9444 ports and the source-checkout Monitor restarted on 8765/9443. Their local and tailnet URLs all returned HTTP 200. The served HTML title and bundled heading were `nashiri-core` and `ai-agent-monitor` respectively. A read-only render of the existing Project A custom dashboard showed browser title `project-a` while keeping its `Project A Monitor` heading. Tailscale Serve mappings for 443, 8443, 8444, 9443, and 9444 stayed at their previous targets. The `nashiri-core` logon launcher still points to the installed Python and remains registered; its Telegram notification and reply settings stayed enabled. No additional server or port was started.

Version 1.6.2 replaces the one-off `nashiri-core` cost metric with an opt-in, per-page Codex API-equivalent estimate. The server reads only local Codex token usage records attributed to the current project and caches token metadata under the ignored project runtime directory. It uses published Standard API rates dated 2026-09-28, shows the covered period and excluded-response count, and labels the value as a reference rather than billed cost. Four new tests cover project separation, duplicate prevention, long-context pricing, cache refresh, opt-in behavior, and the API/CLI route; the full N100 suite passed 166 tests and the dashboard JavaScript syntax check passed.

N100 deployment verification: the installed CLI is 1.6.2. `nashiri-core` was stopped and returned on the same 8766/9444 ports. The cost section is enabled there; `/api/codex-cost` transitioned from `calculating` to `ready` and returned 127.81 USD for 4,521 priced responses, with 87 `codex-auto-review` responses excluded. The one-off `cost.api_reference_total` metric was deleted, leaving `/api/metrics` empty. The HTML and both APIs returned HTTP 200 over the existing 9444 tailnet URL. Existing 8765/9443 and 8766/9444 local and tailnet URLs returned HTTP 200; Serve mappings for 443, 8443, 8444, 9443, and 9444 matched the baseline exactly. No new port or service was created. The project's unrelated uncommitted work remains intact; only Monitor runtime files changed. iPhone visual confirmation of the new cost section is pending. Phase 19 remains deferred.

Version 1.6.3 puts the dynamic API-equivalent estimate inside the existing project-metrics area. It adds an independently enabled Codex weekly remaining-quota card, clearly labeled as account-wide. The card reads the local Codex app-server rate-limit endpoint over stdio, exposes only the remaining percentage and timestamps, and does not start a new listening port. All 170 N100 tests and the dashboard JavaScript syntax check passed. The installed 1.6.3 CLI restarted `nashiri-core` on the same 8766/9444 ports. Its tailnet API returned a 44% weekly remainder, and a browser render showed both the cost estimate and quota under **案件メトリクス**. The displayed cost continues to update as project records arrive; it was 128.58 USD at the last browser check. All four Monitor local/tailnet URLs returned HTTP 200, and the Serve mappings for 443, 8443, 8444, 9443, and 9444 matched the baseline. iPhone visual confirmation of this layout remains pending. Phase 19 remains deferred.

Version 1.6.4 keeps the last successful weekly-quota percentage and observation time unchanged when a dashboard request fails or Codex omits the weekly window. The server also retains its last successful value for page reloads. A later valid read updates the card normally. The N100 suite passed 171 tests and the dashboard JavaScript syntax check passed. The installed CLI restarted `nashiri-core` on the same 8766/9444 ports; its tailnet API returned a 42% weekly remainder and a browser render showed 42% under **案件メトリクス**. No new Serve entry or listening port was created. Phase 19 remains deferred.

Version 1.6.5 applies the same last-successful-display behavior to the Codex API-equivalent cost estimate. A transient dashboard request failure or calculation error does not replace the displayed amount, covered period, or calculation time; a successful later result updates them. The backend already retained its last successful calculation, and a regression test now covers that behavior. All 172 N100 tests and the dashboard JavaScript syntax check passed. The installed CLI restarted `nashiri-core` on the same 8766/9444 ports, where the tailnet API and browser displayed 130.42 USD after the initial scan. No new port was created. Phase 19 remains deferred.

Version 1.6.6 reuses manual project-metric cards and skips writes when their key, label, value, and unit are unchanged. The Codex cost and weekly-quota cards also ignore background calculation/observation timestamps when their meaningful values have not changed. The full N100 suite still passes 172 tests and the dashboard JavaScript syntax check passes. The installed CLI restarted `nashiri-core` on its existing 8766/9444 ports. In the browser, 131.62 USD and 38% stayed displayed with their original timestamps while the API reported later calculation/observation times for the same values. Both cards remained inside **案件メトリクス**. The four local/tailnet Monitor URLs returned HTTP 200, existing Serve mappings were preserved, and no new port was created. Phase 19 remains deferred.

Version 1.6.7 moves **質問** immediately below **自動計測** and adds a collapsed, on-demand history of every answered question and its answer. Japanese display translations are preferred where present. Dashboard section heights are fixed with internal scrolling, so error messages or expanded content do not shift lower sections. The Codex API-equivalent card now shares the weekly-quota card's column width and shows only its short reference label and amount. All 174 N100 tests and the dashboard JavaScript syntax check passed. The installed CLI and source deployment were restarted on their existing 8766/9444 and 8765/9443 ports. In the N100 browser, the real `nashiri-core` dashboard showed the requested section order, compact cost card, and all three answered questions when expanded. `/api/answers` returned the three answers; the 9443 dashboard served the updated UI. All four local/tailnet Monitor URLs returned HTTP 200, and Serve mappings for 443, 8443, 8444, 9443, and 9444 remained unchanged. No new port was created. Phase 19 remains deferred.

Version 1.6.8 reduces the fixed project-metrics height from 260 to 220 px and increases automatic telemetry from 270 to 400 px in both dashboard copies. The `ai-agent-monitor` repository now has Codex onboarding so later work can update its own task, progress, and artifacts. Its old local SQLite database contained only Phase 2/4/5/6/7 setup-test records; an integrity-checked backup and the original database are preserved under the ignored `.agent-monitor/runtime/` directory. A fresh database now records real project work. The two old manual demo metrics are absent, while project-specific Codex API-equivalent cost and account-wide weekly remaining quota are enabled, matching the visible metrics used by `nashiri-core`. The agent contract clarifies that this reference estimate is not actual billed cost. The full N100 suite passes 174 tests; dashboard JavaScript syntax and Codex onboarding diagnostics pass. The source dashboard at 8765/9443 and installed CLI 1.6.8 dashboard at 8766/9444 both display the new heights and return HTTP 200 over local and tailnet URLs. All five original Tailscale Serve mappings are semantically unchanged; status output order can vary. The unrelated `nashiri-core` worktree edits were preserved. Phase 19 remains deferred.

Version 1.6.9 fixes automatic-telemetry presentation across projects. The dashboard keeps seven shared cards in a fixed order; if a task is not running or no answer exists, the corresponding duration card shows a dash and explanation without adding a fabricated measurement to `/api/telemetry`. Project-specific optional measurements, such as the last completed task duration, follow the shared cards. The N100 suite still passes 174 tests and the dashboard JavaScript syntax check passes. The source dashboard at 8765/9443 and the updated installed CLI at 8766/9444 both serve the new layout. In the N100 browser, the active-task value is first for both `ai-agent-monitor` and `nashiri-core`; the project without answers shows a dash in the fifth shared position. Both servers keep their existing ports. Phase 19 remains deferred.

Version 1.6.10 fixes a Windows CP932 CLI-output failure discovered while recording the final Japanese progress message for 1.6.9. The event committed to SQLite but printing its em dash raised `UnicodeEncodeError`. Human-readable CLI output now uses UTF-8, while JSON remains ASCII escaped. A subprocess regression test starts with strict CP932 stdout, records the message, and verifies both clean UTF-8 output and database persistence. The N100 suite passes 175 tests and the dashboard JavaScript syntax check passes. The installed 1.6.10 CLI recorded a real Japanese message containing the same dash without error. After task completion, the N100 browser showed the elapsed-time card first with a dash on the 9443 dashboard, followed by the same seven shared fields and order as the active `nashiri-core` dashboard on 9444. All four local/tailnet Monitor URLs returned HTTP 200 and the five existing Serve mappings were unchanged. Phase 19 remains deferred.
