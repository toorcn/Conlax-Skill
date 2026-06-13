# Conlax Skill

Conlax is a real-time in-person networking copilot for OpenClaw. It watches for LinkedIn connection request emails, identifies the person, enriches them with public profile/company/post context, and sends a short brief with what to say next while the person may still be nearby.

## What It Does

- Detects LinkedIn connection/invitation emails, including invitations@linkedin.com.
- Parses the connector's name, headline, role, company, location, profile URL, and invite signal.
- Enriches context with Exa when available:
  - profile/company context
  - recent public activity or post signals
  - role/company disambiguation
- Uses Mem0 for durable memory when available, with local JSON fallback.
- Sends a mobile-friendly brief through OpenClaw delivery, usually Telegram.
- Supports CONLAX_TEST self-test emails so you can verify the full pipeline without waiting for a real LinkedIn request.

## Install In OpenClaw

1. Unzip or clone this folder into an OpenClaw-visible skills directory, for example:

   ```bash
   ~/.openclaw/workspace/skills/conlax
   ```

2. Copy the example config:

   ```bash
   cp config.example.env .env
   ```

3. Edit `.env` with your local settings.

4. Run a demo check:

   ```bash
   python3 scripts/conlax_watcher.py --demo --event "Test Event"
   ```

5. Run one live inbox check:

   ```bash
   python3 scripts/conlax_watcher.py --once --event "Current Event"
   ```

6. After demo and live checks pass, run it as a background watcher:

   ```bash
   python3 scripts/conlax_watcher.py --poll --poll-seconds 30 --event "Current Event"
   ```

## Required Configuration

Set these in `.env` or export them in the runtime environment.

```bash
CONLAX_EMAIL_SOURCE=demo
CONLAX_USER_GOALS=AI tools,startups,community building,useful professional relationships
```

For a live inbox, configure the email source used by your OpenClaw setup. Hong's current setup uses a read-only ClawGlasses bridge:

```bash
CONLAX_EMAIL_SOURCE=clawglasses
CLAWGLASSES_EMAIL_URL=
CLAWGLASSES_KEY=
```

## Exa And Mem0

Both are optional, but they make the brief much more useful.

```bash
EXA_API_KEY=
MEM0_API_KEY=
```

- Without Exa, Conlax still parses LinkedIn emails, but profile/company/post depth will be thinner.
- Without Mem0, Conlax stores memory in local JSON only.
- With Exa, Conlax can add profile-derived company context and recent public activity signals.
- With Mem0, Conlax can remember goals, event context, people seen, and prior interaction tags.

## OpenClaw Delivery

Enable delivery when you want new briefs sent into OpenClaw/Telegram.

```bash
CONLAX_OPENCLAW_DELIVERY_ENABLED=1
CONLAX_OPENCLAW_CHANNEL=telegram
CONLAX_OPENCLAW_TARGET=telegram:<chat_id>
CONLAX_OPENCLAW_BIN=/absolute/path/to/openclaw
CONLAX_NODE_BIN_DIR=/absolute/path/to/node/bin
```

The watcher calls:

```bash
openclaw message send --channel "$CONLAX_OPENCLAW_CHANNEL" --target "$CONLAX_OPENCLAW_TARGET" --message "$brief"
```

Use absolute paths for `CONLAX_OPENCLAW_BIN` and `CONLAX_NODE_BIN_DIR`, especially when running from LaunchAgent or another sparse background environment.

## Self-Test

Enable:

```bash
CONLAX_SELF_TEST_ENABLED=1
CONLAX_SELF_TEST_SUBJECT=CONLAX_TEST
CONLAX_SELF_TEST_SKIP_NAMES=hong,tera
```

Send an email to the watcher mailbox with a subject containing `CONLAX_TEST`.

Body example:

```text
name: Test Person
headline: Founder at ExampleCo | AI Builder
role: Founder
company: ExampleCo
location: Singapore
profile_url: https://www.linkedin.com/in/example
```

The subject is only a trigger and dedupe signal. It must not influence the brief identity; the person should be detected from the email body.

## Brief Format

Conlax briefs are optimized for a quick phone glance:

1. Talk now
2. Who
3. Angles
4. Profile/company context
5. Recent signal
6. Why it matters
7. Open with
8. Ask next
9. If promising
10. Tags, confidence, and source

Recent signal uses public profile/post/activity search when available. It should surface what the person appears to be building, posting about, speaking about, or actively exploring. It should not include private contact details.

## Background Watcher

On macOS, run Conlax with a LaunchAgent only after `--demo` and `--once` work.

Recommended LaunchAgent shape:

- Program: absolute Python path
- Arguments: `scripts/conlax_watcher.py --poll --poll-seconds 30 --verbose`
- WorkingDirectory: installed Conlax folder
- Stdout/stderr: `logs/conlax_watcher.log` and `logs/conlax_watcher.err.log`
- KeepAlive: true
- RunAtLoad: true

If OpenClaw delivery works in a terminal but fails in LaunchAgent, check absolute binary paths first.

## Files

- `SKILL.md`: OpenClaw skill instructions.
- `scripts/conlax_watcher.py`: watcher, parser, enrichment, delivery.
- `config.example.env`: safe example config.
- `references/integration.md`: integration notes.
- `templates/brief.md`: brief format.
- `data/demo_emails.json`: demo input.

Do not commit or share `.env`, `logs/`, `state/`, `data/local_memory.json`, or runtime cache files.
