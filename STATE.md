# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Formal release: `v1.0.0` published
- Development package version: `1.2.1`
- Completed phases: 1 through 15
- Current phase: Phase 15 verified; multi-recipient email extension implementation complete, N100 verification pending; Phase 16 not started

## Phase 14 baseline

Version `1.1.0` read-only diagnostics passed N100 verification with 84 tests. Production 8765/9443 and the established Tailscale Serve mappings remained unchanged.

## Phase 15 implementation

Version `1.2.0` implements durable Telegram notifications with optional email.

Durable state:

- `notification_outbox` stores one logical event per question
- question creation and outbox creation are one SQLite transaction
- `notification_deliveries` stores independent channel state
- enabled channels are snapshotted when the question is created
- a logical question event cannot be duplicated
- failed channel attempts remain pending with attempt count, attempt time, and last error
- successful channels are not resent when another channel fails
- answering a question cancels only still-pending channel deliveries
- if no channel is enabled, asking a question still works and the no-target outbox event is recorded as cancelled

Telegram:

- primary notification adapter
- uses Telegram Bot API `sendMessage`
- bot token comes from `AI_AGENT_MONITOR_TELEGRAM_BOT_TOKEN`
- chat ID is project-local non-secret configuration
- `monitor notify telegram set --chat-id ...`
- `monitor notify telegram discover`
- `monitor notify telegram on|off`

Optional email:

- off by default
- `monitor notify email set ...`
- `monitor notify email on|off`
- supports STARTTLS, SMTP-over-SSL, and explicit no-TLS mode
- authenticated SMTP password comes from `AI_AGENT_MONITOR_SMTP_PASSWORD`
- Telegram and email delivery/retry state are independent

Runtime:

- the monitor server polls pending deliveries every five seconds
- failed automatic attempts back off for 60 seconds
- `monitor notify send` forces an immediate retry
- `monitor notify status` reports non-secret configuration and whether required secret environment variables are available
- `monitor notifications` exposes durable outbox state
- doctor checks notification configuration
- `.agent-monitor/notifications.json` is ignored by Git and does not contain tokens/passwords

The standard-library test suite now contains 103 tests. Unit coverage includes transaction rollback, event uniqueness, persistence, per-channel retry, stale-notification cancellation, retry backoff, Telegram request construction, SMTP STARTTLS delivery, secret non-persistence, and the email on/off behavior.

## Phase 15 N100/iPhone verification

Phase 15 passed real N100/iPhone verification.

Telegram:

- CLI 1.2.0 was installed
- the bot token was read from the Windows user environment variable without being printed or written to project configuration
- `monitor notify telegram discover` obtained the intended chat ID after the user messaged the bot
- the iPhone received real Telegram notifications
- successful Telegram delivery was recorded as delivered in the durable outbox

Optional Gmail email:

- Gmail SMTP used `smtp.gmail.com:587` with STARTTLS
- the Gmail app password was read from `AI_AGENT_MONITOR_SMTP_PASSWORD` and was not stored in project configuration
- Email OFF: only Telegram delivered
- Email ON: Telegram and Gmail both delivered
- Email OFF again: only Telegram delivered
- the iPhone/Gmail client confirmed the expected messages in all three stages
- Telegram remained enabled when email was disabled
- no pending notification remained after the test

Operational safety:

- `nashiri-core` returned on the same 8766/9444 ports after the CLI update
- production 8765/9443 and existing Tailscale Serve mappings remained unchanged
- no production notification configuration was changed during the disposable-project email test
- repository code was not modified by the N100 verification
- the Telegram bot remains outbound notification only; human answers continue through the monitor dashboard

## Phase 15 multi-recipient email extension

Version `1.2.1` adds configurable multiple email recipients without changing Telegram behavior.

Implemented behavior:

- notification recipients can be any valid email addresses supported by the configured SMTP provider
- `monitor notify email set` accepts repeated `--to`
- `monitor notify email recipient add|remove|list` manages recipients without re-entering SMTP settings
- recipients are deduplicated case-insensitively
- each recipient receives a separate message, so addresses are not exposed to other recipients
- each recipient has independent pending/delivered/cancelled state
- one failed recipient can be retried without resending successful recipients
- removing a recipient cancels only that address's pending deliveries
- removing the last recipient disables email
- existing single-recipient `to` configuration is migrated in memory to the recipient list
- the existing notification-delivery table migrates to a per-target uniqueness key
- Telegram behavior and the email ON/OFF switch remain unchanged

The standard-library suite now contains 112 tests. Cross-platform CI has passed the new multi-recipient and legacy-migration tests on Windows and Ubuntu with Python 3.10 and 3.12.

## Next task

Verify the Phase 15 multi-recipient extension on the N100 before starting Phase 16.

Use the existing disposable Phase 15 notification project. Confirm the previous Gmail recipient survives the 1.2.1 upgrade, add a second test recipient, enable email, send one question, and verify both addresses receive separate messages while Telegram still receives one notification. Then remove the second recipient and verify a later question goes only to the remaining email recipient plus Telegram.

Do not expose email addresses, Gmail app passwords, or the Telegram bot token in chat or committed files.

Phase 16 has not started.

Before implementing automatic resume, define the runner lifecycle and persistence model. Keep human answers, resume requests, resume attempts, and runner state as separate records. The first runner adapter should target the verified Codex workflow.

Do not automatically launch or resume Codex until duplicate-resume prevention, completed-task handling, missing-runner handling, and restart recovery have explicit tests.

Telegram setup must be performed without committing or pasting the bot token into repository files. Create the bot in Telegram, send the bot a message from the target iPhone account, set `AI_AGENT_MONITOR_TELEGRAM_BOT_TOKEN` as a user environment variable on the N100, discover/configure the chat ID, and enable Telegram.

Use a disposable monitored project for the first real notification tests. Verify:

1. a new question produces a Telegram notification on the iPhone without the dashboard already open
2. the Telegram message identifies the project and question and includes the dashboard URL when remote state exists
3. successful delivery becomes delivered in the outbox
4. a deliberately failed delivery remains pending and is retried without creating another logical event
5. answering before a pending delivery sends cancels that pending delivery
6. optional email is OFF by default
7. configuring email does not send mail until `monitor notify email on`
8. after email is enabled, Telegram and email have independent delivery state
9. `monitor notify email off` cancels pending email delivery without disabling Telegram
10. production 8765/9443 and existing Serve mappings remain unchanged

For email, choose the actual SMTP provider during N100 verification. Do not store its password in `notifications.json`.

Do not begin automatic agent resume. That remains Phase 16.

## Post-v1 roadmap

- Phase 16: runner lifecycle and automatic resume
- Phase 17: multi-project registry
- Phase 18: automatic telemetry
- Phase 19: second-agent portability verification
