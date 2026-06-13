# Conlax Integration

## OpenClaw Runtime Pattern

Conlax works best as:

1. An installed skill for instructions and scripts.
2. A background watcher process or scheduled task.
3. A chat delivery layer, usually Telegram through OpenClaw message.

Recommended runtime flow:

email source -> conlax_watcher.py -> action-first Markdown brief -> openclaw message send

## Fresh Install Path

1. Unzip conlax-skill.zip into ~/.openclaw/workspace/skills/conlax or another OpenClaw-visible skills directory.
2. Copy config.example.env to .env and fill only local credentials/targets there.
3. Run --demo first; this validates Python imports and the brief template without touching email.
4. Run --once with the current event name; this validates email source, parsing, enrichment, and delivery configuration.
5. Send a CONLAX_TEST email to the watcher mailbox to validate the complete inbox -> parser -> enrichment -> OpenClaw delivery path.
6. Promote to a background watcher only after the above checks pass.

Do not include .env, logs/, state/, or local_memory.json in shared zips. They are runtime-local state.

## Trigger Detection

Match LinkedIn request emails with a conservative filter:

- sender contains linkedin.com
- sender can explicitly match invitations@linkedin.com
- subject/body contains one of:
  - wants to connect
  - sent you an invitation
  - accepted your invitation
  - connect with you on LinkedIn

Deduplicate with state/processed_emails.json.

Self-test trigger:

- When CONLAX_SELF_TEST_ENABLED=1, the watcher also checks the configured IMAP mailbox from ~/.config/imap-smtp-email/.env.
- Any message whose subject contains CONLAX_TEST is treated as a synthetic connection request.
- CONLAX_TEST is only a trigger/dedupe signal; never use the subject as the person identity.
- Body fields can include name, headline, role, company, location, and profile_url. If fields are absent, infer name/headline/role/company/location from the body lines.
- Forwarded invitations may include wrapper lines before the connector, such as Fwd:, forwarded-message separators, image alt text, CONLAX_TEST, Hong, or mailbox headers. Ignore configured wrapper names with CONLAX_SELF_TEST_SKIP_NAMES and prefer the first likely person name followed by a profile-like headline.
- Raw LinkedIn emails may include preview text like "Name, role from company is waiting for your response" and profile-card blocks. Prefer the invitation/profile-card connector before the "More people you may know" recommendation section, infer company from phrases like "at/from/Get Company", infer location from the line after the headline, and ignore boilerplate labels like response, Accept, View profile, LinkedIn widgets, image icons, and invisible preview padding.
- LinkedIn nav icon alt text may be split across lines, such as "[image: Messaging icon]" or "Mynetwork icon]"; never treat these as people. If invitation copy and profile/search context differ, preserve both and mark non-exact search context as "Search-derived, verify".
- Synthetic contacts should generate briefs but should not be stored in people memory.

## Exa

Use Exa as the public enrichment layer. Search query should combine person name, company/headline, LinkedIn/public profile clue, and event context if useful.

Return low confidence if the identity is ambiguous.

Normalize LinkedIn email tracking URLs, especially /comm/in/... links, into clean /in/... profile URLs before enrichment. Prefer profile-derived/search-derived role, work history, and company context over the tagline alone. If person enrichment is thin but company is visible, enrich the company separately. The company context should explain what the company does and what conversation angle it creates, not just repeat the company name.

## Mem0

Use Mem0 to remember user goals, current event, people seen at the event, past interactions, and tags such as AI, startup, recruiter, sponsor, investor, student, builder, community.

If Mem0 is unavailable, use data/local_memory.json.

## OpenClaw Delivery

When CONLAX_OPENCLAW_DELIVERY_ENABLED=1, the script sends each new brief through:

openclaw message send --channel "$CONLAX_OPENCLAW_CHANNEL" --target "$CONLAX_OPENCLAW_TARGET" --message "$brief"

Use an absolute CONLAX_OPENCLAW_BIN. LaunchAgent environments may not include node in PATH, so the watcher prepends CONLAX_NODE_BIN_DIR, defaulting to /Users/bing/.nvm/versions/node/v22.22.2/bin.

If OpenClaw delivery is disabled, the script still prints briefs to stdout for local demos and log-based inspection.

## Brief Design

The brief should be live-event oriented, not CRM oriented:

1. Talk now: one concrete action or angle.
2. Who: name, headline, role/title, company/workplace, location, and relevant angles when visible.
3. Angles: short bullet list when the person has multiple relevant threads.
4. Profile/company context: compact profile-derived company/work context when available; fallback to company search.
5. Recent signal: compact public post/activity/content themes when available; omit private contact details and label non-exact search-derived context for verification.
6. Why it matters: specific overlap with user goals or event context.
7. Open with: one ready-to-say sentence that assumes the user already met them in person. Prefer a recent-signal hook over a generic title/company hook when available.
8. Ask next: three sharp questions.
9. If promising: a concise follow-up line.
10. Tags, confidence, and source.

Keep sections visually separated for mobile scanning; prefer short labels and bullets over dense paragraphs. Profile/company and Recent signal context should be compact, clipped bullets rather than pasted search summaries.
