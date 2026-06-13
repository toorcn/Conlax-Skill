---
name: conlax
description: Use when building, demoing, running, or improving Conlax: a real-time in-person networking copilot that detects LinkedIn connection request emails, enriches the person with Exa/search, uses Mem0 or local memory for user/event context, and sends an instant brief with questions to ask while the person is still nearby.
---

# Conlax

Conlax briefs the user during live in-person networking. Its hero moment is not CRM or post-event follow-up: it is helping the user understand a new LinkedIn connection while the person may still be nearby and giving them the next useful thing to say.

## Core Workflow

1. Set current event context and user goals.
2. Watch for LinkedIn connection request emails.
3. Parse name, role, headline/company/location clues, profile URL, sender, timestamp, and email id.
4. Enrich the person with Exa or web search.
5. Retrieve and update Mem0 memory, with local JSON fallback.
6. Generate a concise mobile brief optimized for a 5-10 second glance: talk-now action, who, role, company/location context, relevant angles, recent public activity/post signals when available, why it matters, one in-person follow-up opener, three sharp next questions, if-promising follow-up, tags, confidence, and source.
7. Send the brief through OpenClaw delivery when configured, usually Telegram.

## Product Framing

Use this wording when explaining Conlax:

Conlax is a real-time networking copilot that uses LinkedIn connection request emails to instantly brief community builders on who they just met, why they matter, and what to ask next while the conversation is still live.

The problem:

At in-person events, community builders meet many people quickly, but LinkedIn connection requests often arrive before they know who the person really is, causing them to miss the chance to ask better questions, spot relevant opportunities, or deepen the relationship in the moment.

## Commands

Run from this skill directory:

python3 scripts/conlax_watcher.py --demo --event "Build Club GTM AI Lab"
python3 scripts/conlax_watcher.py --once --event "Build Club GTM AI Lab"
python3 scripts/conlax_watcher.py --poll --poll-seconds 60 --event "Build Club GTM AI Lab"

Use --demo for hackathon demos and local testing. Use --once for one inbox check. Use --poll only when the user explicitly wants the watcher running.

## OpenClaw Install Checklist

For a fresh OpenClaw setup:

1. Unzip the skill into an OpenClaw-visible skills folder, for example ~/.openclaw/workspace/skills/conlax.
2. From the conlax folder, copy config.example.env to .env.
3. Set CONLAX_EMAIL_SOURCE:
   - demo for local demos without inbox access.
   - clawglasses for Hong's read-only email bridge, or another compatible email source wired into scripts/conlax_watcher.py.
4. Add enrichment keys if available:
   - EXA_API_KEY for public profile/company/post enrichment.
   - MEM0_API_KEY for durable memory; otherwise Conlax uses data/local_memory.json.
5. Enable delivery if briefs should appear in OpenClaw/Telegram:
   - CONLAX_OPENCLAW_DELIVERY_ENABLED=1
   - CONLAX_OPENCLAW_CHANNEL=telegram
   - CONLAX_OPENCLAW_TARGET=<target chat>
   - CONLAX_OPENCLAW_BIN=<absolute path to openclaw>
6. Run python3 scripts/conlax_watcher.py --demo once to verify the script and brief format.
7. Run python3 scripts/conlax_watcher.py --once --event "<event name>" to verify live email access.
8. Only after those pass, run it as a background watcher or LaunchAgent.

The distributed zip should contain only skill assets: SKILL.md, config.example.env, scripts/, references/, templates/, and demo data. Runtime files such as .env, logs/, state/, and data/local_memory.json are created or maintained locally and should not be shared unless intentionally exporting state.

## Setup

Copy config.example.env to .env or export equivalent environment variables:

- CONLAX_EMAIL_SOURCE: demo or clawglasses
- CLAWGLASSES_EMAIL_URL: read-only email endpoint if using ClawGlasses
- CLAWGLASSES_KEY: API key for the email endpoint
- EXA_API_KEY: optional, enables live Exa enrichment
- MEM0_API_KEY: optional, enables Mem0 memory
- CONLAX_USER_GOALS: comma-separated user goals
- CONLAX_OPENCLAW_DELIVERY_ENABLED: set to 1 to send new briefs through OpenClaw
- CONLAX_OPENCLAW_CHANNEL: usually telegram
- CONLAX_OPENCLAW_TARGET: chat target, for example telegram:1063008785
- CONLAX_OPENCLAW_BIN: absolute openclaw CLI path
- CONLAX_SELF_TEST_ENABLED: set to 1 to allow CONLAX_TEST emails to trigger synthetic briefs
- CONLAX_SELF_TEST_SKIP_NAMES: comma-separated forwarder/header names to ignore during self-test body parsing, usually hong,tera

Never hardcode credentials in the skill.

## Background Watcher

On macOS, use a LaunchAgent only after --demo and --once checks work. The LaunchAgent should:

- run python3 scripts/conlax_watcher.py --poll --poll-seconds 30 --verbose
- set WorkingDirectory to the installed conlax folder
- write stdout/stderr to logs/conlax_watcher.log and logs/conlax_watcher.err.log
- use absolute paths for Python and OpenClaw-related binaries

LaunchAgent environments are sparse. If OpenClaw delivery fails there but works in a shell, check CONLAX_OPENCLAW_BIN and CONLAX_NODE_BIN_DIR first.

## Self-Test

To verify the full path, send an email to the watcher mailbox, usually tera@polytera.dev, with subject containing CONLAX_TEST.

The subject is only a trigger and dedupe signal. It must not influence the brief content. Put the test person's name/headline in the email body; the generated brief should focus on that body-detected person.

If forwarding a real LinkedIn invitation into a CONLAX_TEST email, the body may include wrapper lines such as forwarded-message headers, image alt text, CONLAX_TEST, or the forwarder name before the connector. The parser should skip configured names like Hong/tera and prefer the name followed by a profile-like headline.

For raw LinkedIn email bodies, the parser should also handle invitation copy like "Yurii, Director of Business Development from Chargebase is waiting for your response" and profile-card blocks like "[image: Yurii Momot]" followed by the name and headline. Ignore boilerplate such as response, Accept, View profile, LinkedIn widgets, invisible preview padding, and "More people you may know" recommendations.

When invitation copy and profile-card context differ, preserve both. Example: the email preview may say "Founder from syncHRonise" while profile/search context points to another strategic technology role. Briefs should show the invite signal separately and label uncertain search context as "Search-derived, verify".

Optional body fields:

```text
name: Test Person
headline: Founder at ExampleCo
role: Founder
company: ExampleCo
location: Singapore
profile_url: https://linkedin.com/in/example
```

Expected result: the watcher treats the email as a synthetic connection request, generates an action-first brief, dedupes it by email UID, and sends through OpenClaw if delivery is enabled. Synthetic contacts must not be stored in people memory.

## Delivery Rules

- Keep visible briefs short enough to read at an event.
- Space information into glanceable blocks; avoid dense paragraphs when several facts are available.
- Keep profile/company and recent-signal bullets short enough for mobile scanning; long profile summaries should be clipped into compact bullets.
- Lead with what to say or do now.
- Make relevance concrete when headline/company/location gives enough signal; avoid generic "possible relevance" unless data is thin.
- Include who they are professionally, especially role/title and multiple relevant angles when the headline shows more than one thing.
- Add a Recent signal block when public profile/post/activity search finds useful context. This should deepen the human angle: what they talk about, build, post, or seem active around. Exclude contact-directory results and private contact details.
- For company context, prefer profile-derived/search-derived work history and company description over the LinkedIn tagline alone. Normalize LinkedIn tracking URLs before enrichment.
- Include one ready-to-say opener that assumes the user already met the person in person; avoid cold-open language like "saw your LinkedIn come through". When recent public activity is available, use it as the opener hook.
- Separate "Ask next" from "If promising" follow-up.
- Do not paste raw private email contents unless specifically asked.
- Label low-confidence enrichment clearly.
- Include source and confidence quietly at the end.

## References

- Read references/integration.md when wiring this into OpenClaw or another runtime.
- Read templates/brief.md before changing the brief format.
- Use data/demo_emails.json for demos or tests without real inbox access.
