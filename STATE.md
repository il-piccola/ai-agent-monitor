# Current State

## Repository

- Repository: `il-piccola/ai-agent-monitor`
- Visibility: public
- Default branch: `main`
- Formal release: `v1.0.0` published
- Development package version: `1.2.0`
- Completed phases: 1 through 14
- Current phase: Phase 15 implementation complete; N100/iPhone verification pending

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

## Next task

Verify Phase 15 on the N100 and iPhone before beginning Phase 16.

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
