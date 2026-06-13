#!/usr/bin/env python3
import argparse
import email
import imaplib
import json
import os
import re
import ssl
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "state" / "processed_emails.json"
DEMO_FILE = ROOT / "data" / "demo_emails.json"
MEMORY_FILE = ROOT / "data" / "local_memory.json"
DELIVERY_LOG_FILE = ROOT / "logs" / "delivery.log"

LINKEDIN_PATTERNS = [
    "wants to connect",
    "sent you an invitation",
    "accepted your invitation",
    "connect with you on linkedin",
    "invitation to connect",
    "new invitation",
    "connection request",
    "accepted your request",
]

LINKEDIN_INVITATION_SENDERS = [
    "invitations@linkedin.com",
]

SELF_TEST_SUBJECT = "CONLAX_TEST"
SELF_TEST_CONFIG = Path.home() / ".config" / "imap-smtp-email" / ".env"


def load_env_file(path):
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_key_value_file(path):
    data = {}
    if not path.exists():
        return data
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def append_log(path, line):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(line.rstrip() + "\n")


def openclaw_delivery_enabled():
    return os.getenv("CONLAX_OPENCLAW_DELIVERY_ENABLED", "0").lower() in ("1", "true", "yes")


def deliver_brief(brief):
    if not openclaw_delivery_enabled():
        return False
    target = os.getenv("CONLAX_OPENCLAW_TARGET") or os.getenv("CONLAX_TELEGRAM_TARGET")
    channel = os.getenv("CONLAX_OPENCLAW_CHANNEL", "telegram")
    account = os.getenv("CONLAX_OPENCLAW_ACCOUNT", "")
    binary = os.getenv("CONLAX_OPENCLAW_BIN", "/Users/bing/.nvm/versions/node/v22.22.2/bin/openclaw")
    if not target:
        append_log(DELIVERY_LOG_FILE, "CONLAX_DELIVERY_SKIPPED missing target")
        return False

    cmd = [binary, "message", "send", "--channel", channel, "--target", target, "--message", brief, "--json"]
    if account:
        cmd.extend(["--account", account])

    try:
        env = os.environ.copy()
        node_bin = os.getenv("CONLAX_NODE_BIN_DIR", "/Users/bing/.nvm/versions/node/v22.22.2/bin")
        env["PATH"] = f"{node_bin}:{env.get('PATH', '')}"
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
    except Exception as exc:
        append_log(DELIVERY_LOG_FILE, f"CONLAX_DELIVERY_ERROR exception={exc}")
        return False

    if result.returncode != 0:
        append_log(DELIVERY_LOG_FILE, f"CONLAX_DELIVERY_ERROR code={result.returncode} stderr={result.stderr.strip()} stdout={result.stdout.strip()}")
        return False

    append_log(DELIVERY_LOG_FILE, f"CONLAX_DELIVERY_OK target={target} stdout={result.stdout.strip()}")
    return True


def is_linkedin_request(email):
    if email.get("conlax_self_test"):
        subject = email.get("subject", "").lower()
        body = email.get("body", "").lower()
        trigger = os.getenv("CONLAX_SELF_TEST_SUBJECT", SELF_TEST_SUBJECT).lower()
        return trigger in subject or trigger.lower().replace("_", " ") in body

    sender = email.get("from", "").lower()
    text = f"{sender} {email.get('subject','')} {email.get('body','')}".lower()
    is_linkedin_sender = "linkedin" in sender or any(address in sender for address in LINKEDIN_INVITATION_SENDERS)
    if not is_linkedin_sender and "linkedin" not in text:
        return False
    if any(pattern in text for pattern in LINKEDIN_PATTERNS):
        return True
    # Some LinkedIn emails phrase requests differently but still carry the core signal.
    return "connect" in text and ("invitation" in text or "request" in text)


def is_likely_person_name(value):
    value = re.sub(r"\s+", " ", value or "").strip()
    if not (2 <= len(value) <= 80):
        return False
    lowered = value.lower()
    skip_names = {
        item.strip().lower()
        for item in os.getenv("CONLAX_SELF_TEST_SKIP_NAMES", "hong,tera").split(",")
        if item.strip()
    }
    if lowered in skip_names:
        return False
    if lowered in {"response", "accept", "view profile", "notifications", "messaging", "mynetwork icon"}:
        return False
    if " icon" in lowered or lowered.endswith("icon]") or "profile picture" in lowered:
        return False
    if lowered.startswith((
        "fwd:",
        "fw:",
        "re:",
        "from:",
        "date:",
        "subject:",
        "to:",
        "linkedin",
        "[image:",
        "conlax_test",
        "headline:",
        "company:",
        "profile_url:",
        "this email",
        "---------- forwarded message",
    )):
        return False
    if any(token in lowered for token in [
        "http://",
        "https://",
        "@",
        "synthetic",
        "test trigger",
        "you have an invitation",
        "linkedin widget",
        "linkedin widgets",
        "people you may know",
        "connections in common",
        "forwarded message",
    ]):
        return False
    if len(value.split()) > 5:
        return False
    return any(ch.isalpha() for ch in value)


def visible_text(value):
    kept = []
    for char in value or "":
        category = unicodedata.category(char)
        if category in {"Cf", "Mn"}:
            continue
        kept.append(char)
    return re.sub(r"\s+", " ", "".join(kept)).strip()


def is_noise_line(value):
    line = visible_text(value)
    if not line:
        return True
    lowered = line.lower()
    if lowered.startswith("<http://") or lowered.startswith("<https://"):
        return True
    if lowered in {"accept", "view profile", "response"}:
        return True
    return False


def headline_after(lines, start_index, current_name=""):
    lowered_name = current_name.lower().strip()
    for next_index in range(start_index + 1, min(len(lines), start_index + 7)):
        next_line = visible_text(lines[next_index])
        lowered = next_line.lower()
        if not next_line:
            continue
        if lowered.startswith(("more people you may know", "build your network", "------------------------------")):
            return ""
        if is_noise_line(next_line):
            continue
        if lowered_name and lowered == lowered_name:
            continue
        if lowered.startswith(("[image:", "from:", "date:", "subject:", "to:", "conlax_test")):
            continue
        if ":" in next_line[:16]:
            continue
        return next_line
    return ""


def location_after(lines, start_index, name="", headline=""):
    lowered_name = visible_text(name).lower()
    lowered_headline = visible_text(headline).lower()
    seen_headline = not lowered_headline
    for next_index in range(start_index + 1, min(len(lines), start_index + 10)):
        next_line = visible_text(lines[next_index])
        lowered = next_line.lower()
        if not next_line:
            continue
        if lowered.startswith(("more people you may know", "build your network", "------------------------------")):
            return ""
        if is_noise_line(next_line):
            continue
        if lowered.startswith(("[image:", "from:", "date:", "subject:", "to:", "conlax_test")):
            continue
        if lowered_name and lowered == lowered_name:
            continue
        if lowered_headline and lowered == lowered_headline:
            seen_headline = True
            continue
        if not seen_headline:
            continue
        if looks_like_profile_headline(next_line):
            continue
        if len(next_line) <= 90:
            return next_line
    return ""


def image_alt_name(value):
    line = visible_text(value)
    match = re.match(r"\[image:\s*(.+?)\]$", line, flags=re.I)
    if not match:
        return ""
    alt = match.group(1).strip()
    lowered_alt = alt.lower()
    if "icon" in lowered_alt or "linkedin" in lowered_alt or "social proof" in lowered_alt:
        return ""
    alt = re.sub(r"[’']s Profile Picture$", "", alt, flags=re.I).strip()
    if is_likely_person_name(alt):
        return alt
    return ""


def extract_linkedin_profile_url(body):
    profile_match = re.search(r"https?://[^\s<>)]*linkedin\.com/(?:comm/)?in/[^\s<>)]*", body or "")
    return normalize_linkedin_profile_url(profile_match.group(0)) if profile_match else ""


def normalize_linkedin_profile_url(url):
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    if "linkedin.com" not in parsed.netloc:
        return url
    parts = [part for part in parsed.path.split("/") if part]
    vanity = ""
    if len(parts) >= 2 and parts[0] == "in":
        vanity = parts[1]
    elif len(parts) >= 3 and parts[0] == "comm" and parts[1] == "in":
        vanity = parts[2]
    if not vanity:
        return url
    return f"https://www.linkedin.com/in/{vanity}"


def linkedin_profile_slug(url):
    normalized = normalize_linkedin_profile_url(url)
    parsed = urllib.parse.urlparse(normalized)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "in":
        return parts[1]
    return ""


def invitation_sentence_candidate(lines):
    for index, line in enumerate(lines[:18]):
        current = visible_text(line)
        if not current or current.lower().startswith(("from:", "date:", "subject:", "to:", "----------")):
            continue
        chunk = current
        if index + 1 < len(lines):
            next_line = visible_text(lines[index + 1])
            if next_line.lower() == "response":
                chunk = f"{chunk} response"
        match = re.search(r"\b([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}),\s+(.+?)\s+is waiting for your response\b", chunk)
        if match:
            name = match.group(1).strip()
            headline = match.group(2).strip()
            if is_likely_person_name(name):
                return name, headline
    return None


def extract_company_from_headline(headline):
    text = visible_text(headline)
    if not text:
        return ""
    patterns = [
        r"\bat\s+([^|,\n]+)",
        r"\bfrom\s+([^|,\n]+?)(?:\s+is waiting for your response|$)",
        r"\bGet\s+([A-Z][A-Za-z0-9& .-]{1,60})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            company = match.group(1).strip()
            company = re.sub(r"\s+(?:is waiting.*|\|.*)$", "", company).strip()
            company = re.sub(r"[^A-Za-z0-9& .'-]+$", "", company).strip()
            if company:
                return company
    return ""


def extract_role_from_headline(headline, company=""):
    text = visible_text(headline)
    if not text:
        return ""
    company = visible_text(company)
    role = text
    if " | " in role:
        parts = [part.strip() for part in role.split("|") if part.strip()]
        roleish = [
            part for part in parts
            if re.search(r"\b(founder|co-founder|director|manager|lead|head|sdr|bd|business development|sales|engineer|developer|designer|researcher|student|lecturer|president|consultant|operator|investor)\b", part, flags=re.I)
        ]
        if roleish:
            role = " + ".join(roleish)
    if company:
        role = re.sub(rf"\s+(?:at|from)\s+{re.escape(company)}\b.*$", "", role, flags=re.I).strip()
    role = re.sub(r"\s+is waiting for your response\b.*$", "", role, flags=re.I).strip()
    role = re.sub(r"^[^A-Za-z0-9]+", "", role).strip()
    return role[:140]


def infer_location_from_body(lines, name, headline):
    clean_name = visible_text(name)
    for index, line in enumerate(lines):
        if visible_text(line).lower() == clean_name.lower():
            location = location_after(lines, index, clean_name, headline)
            if location:
                return location
    return ""


def parse_self_test_fields(lines):
    fields = {}
    freeform = []
    for line in lines:
        if ":" in line:
            key, value = line.split(":", 1)
            normalized_key = key.strip().lower()
            if normalized_key in {"name", "headline", "role", "company", "location", "profile_url"}:
                fields[normalized_key] = value.strip()
                continue
        freeform.append(line)
    return fields, freeform


def looks_like_profile_headline(value):
    value = re.sub(r"\s+", " ", value or "").strip()
    if not value or len(value) > 180:
        return False
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "conlax_test", "fwd:", "fw:", "re:")):
        return False
    if any(token in lowered for token in [
        "founder",
        "student",
        "manager",
        "director",
        "strategic",
        "technology",
        "planning",
        "governance",
        "data management",
        "partner",
        "relationships",
        "revenue systems",
        "vc lab",
        "business development",
        "sdr",
        "head",
        "lead",
        "engineer",
        "developer",
        "designer",
        "researcher",
        "operator",
        "investor",
        "builder",
        "consultant",
        "attended",
        "working on",
        "building",
        "exploring",
        " at ",
        " of ",
    ]):
        return True
    return False


def infer_person_from_body(lines):
    candidates = []
    sentence_candidate = invitation_sentence_candidate(lines)
    if sentence_candidate:
        name, headline = sentence_candidate
        candidates.append((0, name, headline, looks_like_profile_headline(headline)))
    for index, line in enumerate(lines):
        if visible_text(line).lower().startswith("more people you may know"):
            break
        alt_name = image_alt_name(line)
        if alt_name:
            headline = headline_after(lines, index, alt_name)
            candidates.append((index, alt_name, headline, looks_like_profile_headline(headline)))
            continue
        if " - " in line:
            possible_name, possible_headline = line.split(" - ", 1)
            if is_likely_person_name(possible_name):
                candidates.append((index, possible_name.strip(), possible_headline.strip(), True))
                continue
        if is_likely_person_name(line):
            headline = headline_after(lines, index, line.strip())
            candidates.append((index, line.strip(), headline, looks_like_profile_headline(headline)))
    for _, name, headline, has_profile_headline in candidates:
        if has_profile_headline and len(name.split()) >= 2:
            return name, headline
    for _, name, headline, has_profile_headline in candidates:
        if has_profile_headline:
            return name, headline
    if candidates:
        _, name, headline, _ = candidates[0]
        return name, headline
    return "Unknown connection", ""


def parse_email(email):
    body = email.get("body", "")
    if email.get("conlax_self_test") and "\\n" in body:
        body = body.replace("\\n", "\n")
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    subject = email.get("subject", "")

    if email.get("conlax_self_test"):
        fields, freeform = parse_self_test_fields(lines)
        inferred_name, inferred_headline = infer_person_from_body(freeform)
        invite = invitation_sentence_candidate(freeform)
        invite_headline = invite[1] if invite else ""
        invite_company = extract_company_from_headline(invite_headline)
        invite_role = extract_role_from_headline(invite_headline, invite_company)
        name = fields.get("name") or inferred_name
        headline = fields.get("headline") or inferred_headline or "Connection request from email body"
        company = fields.get("company") or extract_company_from_headline(headline) or invite_company
        location = fields.get("location") or infer_location_from_body(freeform, name, headline)
        role = fields.get("role") or extract_role_from_headline(headline, company)
        return {
            "id": str(email.get("id") or email.get("message_id") or subject or name),
            "name": name,
            "headline": headline,
            "role": role,
            "company": company,
            "location": location,
            "invite_headline": invite_headline,
            "invite_role": invite_role,
            "invite_company": invite_company,
            "profile_url": fields.get("profile_url", "") or extract_linkedin_profile_url(body),
            "subject": subject,
            "from": email.get("from", ""),
        }

    name = None
    inferred_name, inferred_headline = infer_person_from_body(lines)
    if inferred_name != "Unknown connection":
        name = inferred_name
    if not name:
        match = re.search(r"([A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+){0,3})\s+(?:wants|sent|accepted)", subject)
        if match:
            name = match.group(1).strip()
    if not name:
        name = "Unknown connection"

    headline = inferred_headline if inferred_name != "Unknown connection" else ""
    if not headline and len(lines) > 1 and not lines[1].lower().startswith(("sent you", "wants to", "accepted")):
        headline = lines[1]

    profile_url = extract_linkedin_profile_url(body)

    invite = invitation_sentence_candidate(lines)
    invite_headline = invite[1] if invite else ""
    invite_company = extract_company_from_headline(invite_headline)
    invite_role = extract_role_from_headline(invite_headline, invite_company)
    company = extract_company_from_headline(headline) or invite_company
    role = extract_role_from_headline(headline, company)
    location = infer_location_from_body(lines, name, headline)

    return {
        "id": str(email.get("id") or email.get("message_id") or email.get("subject") or name),
        "name": name,
        "headline": headline,
        "role": role,
        "company": company,
        "location": location,
        "invite_headline": invite_headline,
        "invite_role": invite_role,
        "invite_company": invite_company,
        "profile_url": profile_url,
        "subject": subject,
        "from": email.get("from", ""),
    }


def fetch_demo_emails():
    return load_json(DEMO_FILE, [])


def fetch_clawglasses_emails():
    url = os.getenv("CLAWGLASSES_EMAIL_URL")
    key = os.getenv("CLAWGLASSES_KEY")
    if not url or not key:
        raise RuntimeError("CLAWGLASSES_EMAIL_URL and CLAWGLASSES_KEY are required for clawglasses mode")
    req = urllib.request.Request(url, headers={"x-clawbot-key": key})
    context = None
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=20, context=context) as res:
        data = json.loads(res.read().decode("utf-8"))
    if isinstance(data, dict):
        emails = data.get("emails") or data.get("data") or data.get("messages") or []
    else:
        emails = data
    normalized = []
    for item in emails:
        normalized.append({
            "id": item.get("id") or item.get("messageId") or item.get("message_id"),
            "from": item.get("from") or item.get("sender") or "",
            "subject": item.get("subject") or "",
            "body": item.get("body") or item.get("text") or item.get("snippet") or "",
        })
    return normalized


def fetch_self_test_emails():
    if os.getenv("CONLAX_SELF_TEST_ENABLED", "1").lower() in ("0", "false", "no"):
        return []

    config = load_key_value_file(SELF_TEST_CONFIG)
    host = os.getenv("CONLAX_SELF_TEST_IMAP_HOST") or config.get("IMAP_HOST")
    port = int(os.getenv("CONLAX_SELF_TEST_IMAP_PORT") or config.get("IMAP_PORT") or 993)
    user = os.getenv("CONLAX_SELF_TEST_IMAP_USER") or config.get("IMAP_USER")
    password = os.getenv("CONLAX_SELF_TEST_IMAP_PASS") or config.get("IMAP_PASS")
    mailbox = os.getenv("CONLAX_SELF_TEST_IMAP_MAILBOX") or config.get("IMAP_MAILBOX") or "INBOX"
    reject_unauthorized = (os.getenv("CONLAX_SELF_TEST_IMAP_REJECT_UNAUTHORIZED") or config.get("IMAP_REJECT_UNAUTHORIZED") or "true").lower()
    trigger = os.getenv("CONLAX_SELF_TEST_SUBJECT", SELF_TEST_SUBJECT).lower()
    limit = int(os.getenv("CONLAX_SELF_TEST_LIMIT", "25"))

    if not host or not user or not password:
        return []

    context = ssl.create_default_context()
    if reject_unauthorized in ("0", "false", "no"):
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    messages = []
    imap = imaplib.IMAP4_SSL(host, port, ssl_context=context)
    try:
        imap.login(user, password)
        imap.select(mailbox, readonly=True)
        status, data = imap.search(None, "ALL")
        if status != "OK" or not data:
            return []
        ids = data[0].split()[-limit:]
        for msg_id in ids:
            status, fetched = imap.fetch(msg_id, "(RFC822)")
            if status != "OK" or not fetched:
                continue
            raw = fetched[0][1]
            msg = email.message_from_bytes(raw)
            subject = email.header.make_header(email.header.decode_header(msg.get("subject", "")))
            subject = str(subject)
            if trigger not in subject.lower():
                continue

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    disposition = str(part.get("Content-Disposition", "")).lower()
                    if content_type == "text/plain" and "attachment" not in disposition:
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                            break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")

            messages.append({
                "id": f"self-test-{msg_id.decode()}",
                "from": msg.get("from", ""),
                "subject": subject,
                "body": body,
                "conlax_self_test": True,
            })
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return messages


def get_emails(mode):
    if mode == "demo":
        return fetch_demo_emails()
    if mode == "clawglasses":
        return fetch_clawglasses_emails() + fetch_self_test_emails()
    raise RuntimeError(f"Unsupported CONLAX_EMAIL_SOURCE: {mode}")


def urlopen_with_default_context(req, timeout=20):
    context = None
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()
    return urllib.request.urlopen(req, timeout=timeout, context=context)


def exa_search_person(person, event):
    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        return []
    profile_url = person.get("profile_url", "")
    profile_slug = linkedin_profile_slug(profile_url)
    if profile_slug:
        query_parts = [
            f"linkedin.com/in/{profile_slug}",
            person.get("name", ""),
            person.get("company", ""),
            person.get("headline", ""),
            "current role company experience what they work on",
        ]
    else:
        query_parts = [
            person.get("name", ""),
            person.get("headline", ""),
            person.get("company", ""),
            event or "",
            "professional profile company work",
        ]
    payload = {
        "query": " ".join(part for part in query_parts if part).strip(),
        "numResults": 3,
        "type": "auto",
        "contents": {
            "summary": {
                "query": "Summarize who this person is, their current role/company, what they work on, and any company context visible from their profile or profile-like search result."
            }
        },
    }
    req = urllib.request.Request(
        "https://api.exa.ai/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", "x-api-key": api_key},
        method="POST",
    )
    try:
        with urlopen_with_default_context(req, timeout=20) as res:
            data = json.loads(res.read().decode("utf-8"))
    except Exception:
        return []
    results = []
    for item in data.get("results", [])[:3]:
        results.append({
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "summary": item.get("summary") or "",
        })
    return results


def exa_search_company(company):
    api_key = os.getenv("EXA_API_KEY")
    if not api_key or not company:
        return []
    payload = {
        "query": f"{company} company product what it does customers pricing spend management procurement",
        "numResults": 3,
        "type": "auto",
        "contents": {
            "summary": {
                "query": "Summarize what this company does, who it serves, and why it may be relevant in a networking conversation."
            }
        },
    }
    req = urllib.request.Request(
        "https://api.exa.ai/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", "x-api-key": api_key},
        method="POST",
    )
    try:
        with urlopen_with_default_context(req, timeout=20) as res:
            data = json.loads(res.read().decode("utf-8"))
    except Exception:
        return []
    results = []
    for item in data.get("results", [])[:3]:
        results.append({
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "summary": item.get("summary") or "",
        })
    return results


CONTACT_DIRECTORY_HINTS = [
    "adapt.io",
    "apollo.io",
    "contactout.com",
    "email-format.com",
    "hunter.io",
    "lusha.com",
    "rocketreach.co",
    "signalhire.com",
]


def is_contact_directory_result(result):
    haystack = f"{result.get('title','')} {result.get('url','')} {result.get('summary','')}".lower()
    return any(hint in haystack for hint in CONTACT_DIRECTORY_HINTS)


def exa_search_activity(person, event):
    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        return []
    profile_url = person.get("profile_url", "")
    profile_slug = linkedin_profile_slug(profile_url)
    name = person.get("name", "")
    company = person.get("company", "")
    headline = person.get("headline", "")
    query_parts = [
        name,
        company,
        headline,
        f"linkedin.com/in/{profile_slug}" if profile_slug else "",
        "recent public LinkedIn posts activity talks content founder updates topics",
        event or "",
    ]
    payload = {
        "query": " ".join(part for part in query_parts if part).strip(),
        "numResults": 5,
        "type": "auto",
        "contents": {
            "summary": {
                "query": "Summarize recent public activity, posts, talks, content themes, or builder/company updates that could help start a useful in-person follow-up conversation. Exclude contact details and do not infer private facts."
            }
        },
    }
    req = urllib.request.Request(
        "https://api.exa.ai/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", "x-api-key": api_key},
        method="POST",
    )
    try:
        with urlopen_with_default_context(req, timeout=20) as res:
            data = json.loads(res.read().decode("utf-8"))
    except Exception:
        return []
    results = []
    for item in data.get("results", [])[:5]:
        result = {
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "summary": item.get("summary") or "",
        }
        if not is_contact_directory_result(result):
            results.append(result)
    return results


def fallback_company_context(person):
    company = (person.get("company") or "").strip()
    text = f"{company} {person.get('headline', '')}".lower()
    if not company:
        return ""
    if "spendbase" in text or "spend" in text or "procurement" in text or "finops" in text:
        return f"{company} appears to sit around spend management, procurement, SaaS/cloud cost control, approvals, cards, invoices, and vendor/tool savings. Verify the exact product angle with them."
    if "recruit" in text or "talent" in text or "hire" in text:
        return f"{company} appears connected to hiring, talent, or recruiting workflows. Ask what side of the talent market they serve."
    if "ai" in text or "automation" in text:
        return f"{company} appears connected to AI or automation. Ask what workflow they automate and who the buyer is."
    return ""


def compact_context(value, max_items=3, max_chars=360, item_chars=86):
    text = visible_text(value)
    text = re.sub(r"^summary:\s*", "", text, flags=re.I).strip()
    if not text:
        return ""
    parts = []
    for raw_part in re.split(r"\s+-\s+|\n+-\s+|\n+", text):
        part = raw_part.strip(" -")
        if part:
            parts.append(part)
    if not parts:
        parts = [text]
    clipped = []
    for part in parts:
        part = part.strip()
        lowered = part.lower()
        if lowered.startswith(("name:", "name/identity:", "who he is:", "who she is:", "who they are:", "who they are")):
            continue
        if lowered in {"company context:", "profile context:", "current role/company:", "what they work on:"}:
            continue
        if len(part) > item_chars:
            part = part[: item_chars - 3].rstrip() + "..."
        clipped.append(part)
        if len(clipped) >= max_items:
            break
    compacted = "\n".join(f"- {part}" for part in clipped)
    return compacted[:max_chars].rstrip()


def compact_activity_context(value, max_items=2, max_chars=320, item_chars=150):
    compacted = compact_context(value, max_items=5, max_chars=600, item_chars=item_chars)
    if not compacted:
        return ""
    lines = []
    skipped = []
    for raw_line in compacted.splitlines():
        line = raw_line.strip()
        lowered = line.lower().lstrip("- ").strip()
        if lowered.startswith(("role and focus:", "current role:", "profile:", "name:", "who they are:")):
            skipped.append(line)
            continue
        lines.append(line)
        if len(lines) >= max_items:
            break
    if not lines:
        lines = skipped[:max_items]
    compacted = "\n".join(lines)[:max_chars].rstrip()
    return re.sub(r"\ba Alibaba\b", "an Alibaba", compacted)


def best_profile_result(person, exa_results):
    if not exa_results:
        return None
    slug = linkedin_profile_slug(person.get("profile_url", ""))
    name_parts = [part.lower() for part in re.split(r"\s+", person.get("name", "")) if len(part) > 2]
    for item in exa_results:
        haystack = f"{item.get('title','')} {item.get('summary','')} {item.get('url','')}".lower()
        url = item.get("url", "").lower()
        if slug and slug in url:
            return item
        if name_parts and all(part in haystack for part in name_parts[:2]):
            return item
    return None


def is_exact_profile_result(person, result):
    slug = linkedin_profile_slug(person.get("profile_url", ""))
    return bool(slug and result and slug in (result.get("url", "").lower()))


def best_activity_result(person, activity_results):
    if not activity_results:
        return None
    slug = linkedin_profile_slug(person.get("profile_url", ""))
    name_parts = [part.lower() for part in re.split(r"\s+", person.get("name", "")) if len(part) > 2]
    company = (person.get("company") or "").lower()
    for item in activity_results:
        haystack = f"{item.get('title','')} {item.get('summary','')} {item.get('url','')}".lower()
        url = item.get("url", "").lower()
        if slug and (slug in url or slug in haystack):
            return item
        if name_parts and all(part in haystack for part in name_parts[:2]):
            return item
        if name_parts and company and any(part in haystack for part in name_parts[:2]) and company in haystack:
            return item
    return None


def relevant_angles_for(person, tags):
    text = f"{person.get('headline', '')} {person.get('role', '')} {person.get('company', '')} {person.get('invite_headline', '')} {person.get('invite_role', '')} {person.get('invite_company', '')}".lower()
    angles = []
    if "founder" in text:
        angles.append("founder/startup")
    if "strategic" in text or "planning" in text:
        angles.append("strategy")
    if "technology" in text:
        angles.append("technology")
    if "finance ops" in tags:
        angles.append("spend/procurement")
    if any(word in text for word in ["sdr", "sales", "business development", "bd", "gtm", "outbound"]):
        angles.append("sales/BD")
    if "startup" in tags and "founder/startup" not in angles:
        angles.append("startup/operator")
    if "AI" in tags:
        angles.append("AI/automation")
    if "community" in tags:
        angles.append("community")
    if "career" in tags:
        angles.append("talent/career")
    if "builder" in tags:
        angles.append("builder/student")
    if "investor" in tags:
        angles.append("investor")
    deduped = []
    for angle in angles:
        if angle not in deduped:
            deduped.append(angle)
    return deduped[:4]


def mem0_client():
    api_key = os.getenv("MEM0_API_KEY")
    if not api_key:
        return None
    try:
        from mem0 import MemoryClient
    except Exception:
        return None
    try:
        return MemoryClient(api_key=api_key)
    except Exception:
        return None


def mem0_search_context(person, event):
    client = mem0_client()
    if not client:
        return []
    query = f"Networking context for {person.get('name')} {person.get('headline')} at {event}"
    try:
        try:
            results = client.search(query, user_id="hong")
        except TypeError:
            results = client.search(query, filters={"user_id": "hong"})
    except Exception:
        return []
    if isinstance(results, dict):
        results = results.get("results") or results.get("memories") or []
    memories = []
    for item in (results or [])[:3]:
        if isinstance(item, dict):
            memories.append(item.get("memory") or item.get("text") or item.get("content") or str(item))
        else:
            memories.append(str(item))
    return [memory for memory in memories if memory]


def mem0_add_person(person, event, enriched):
    client = mem0_client()
    if not client:
        return False
    content = (
        f"Met or received LinkedIn connection from {person.get('name')} during {event}. "
        f"Headline: {person.get('headline') or 'unknown'}. "
        f"Role: {person.get('role') or enriched.get('role_summary') or 'unknown'}. "
        f"Company: {person.get('company') or 'unknown'}. "
        f"Location: {person.get('location') or 'unknown'}. "
        f"Tags: {', '.join(enriched.get('tags', []))}. "
        f"Relevance: {' '.join(enriched.get('reasons', []))}"
    )
    try:
        client.add([{"role": "user", "content": content}], user_id="hong")
        return True
    except Exception:
        return False


def load_memory(event):
    memory = load_json(MEMORY_FILE, {"user_goals": [], "events": [], "people_seen": []})
    env_goals = os.getenv("CONLAX_USER_GOALS")
    if env_goals:
        memory["user_goals"] = [part.strip() for part in env_goals.split(",") if part.strip()]
    if event and event not in memory.get("events", []):
        memory.setdefault("events", []).append(event)
    return memory


def enrich_person(person, event, memory):
    text = f"{person.get('headline','')} {person.get('role','')} {person.get('company','')} {person.get('invite_headline','')} {person.get('invite_role','')} {person.get('invite_company','')} {person.get('name','')}".lower()
    tags = []
    if any(word in text for word in ["founder", "startup", "operator"]):
        tags.append("startup")
    if any(word in text for word in ["strategic", "planning", "technology"]):
        tags.append("strategy/tech")
    if any(word in text for word in ["ai", "automation", "workflow"]):
        tags.append("AI")
    if "recruit" in text or "career" in text:
        tags.append("career")
    if "community" in text or "developer" in text:
        tags.append("community")
    if "student" in text or "builder" in text:
        tags.append("builder")
    if "investor" in text:
        tags.append("investor")
    if any(word in text for word in ["spend", "procurement", "finops", "cards", "invoices"]):
        tags.append("finance ops")
    if any(word in text for word in ["sdr", "sales", "business development", "outbound", "gtm"]):
        tags.append("sales/BD")
    if not tags:
        tags.append("review")

    goals = memory.get("user_goals", [])
    goal_text = ", ".join(goals[:4]) if goals else "your current goals"
    headline = person.get("headline") or "LinkedIn connection"
    confidence = "medium" if person.get("headline") else "low"
    if "startup" in tags or "AI" in tags or "community" in tags:
        confidence = "high"

    exa_results = exa_search_person(person, event)
    company_results = exa_search_company(person.get("company", ""))
    activity_results = exa_search_activity(person, event)
    mem0_context = mem0_search_context(person, event)
    profile_context = ""
    profile_source_url = ""
    company_context = ""
    company_source_url = ""
    activity_context = ""
    activity_source_url = ""
    reasons = [
        f"Possible relevance to {goal_text}.",
        f"Seen during {event}." if event else "Seen during the current networking window.",
    ]
    if exa_results:
        top = exa_results[0]
        public_context = top.get("summary") or top.get("title")
        if public_context:
            haystack = f"{top.get('title','')} {top.get('summary','')} {top.get('url','')}".lower()
            name_parts = [part.lower() for part in re.split(r"\s+", person.get("name", "")) if len(part) > 2]
            name_match = any(part in haystack for part in name_parts)
            prefix = "Public context" if name_match else "Possible public context; verify identity"
            reasons[0] = f"{prefix}: {public_context[:220]}"
            confidence = "high" if name_match else "medium"
    profile_result = best_profile_result(person, exa_results)
    if profile_result:
        profile_context = compact_context(profile_result.get("summary") or profile_result.get("title") or "", max_items=3)
        profile_source_url = profile_result.get("url") or ""
        if profile_context:
            company_context = profile_context
            company_source_url = profile_source_url
            reasons[0] = f"Profile context: {profile_context[:220]}"
            confidence = "high" if is_exact_profile_result(person, profile_result) else "medium"
    if company_results:
        top_company = company_results[0]
        if not company_context:
            company_context = compact_context(top_company.get("summary") or top_company.get("title") or "", max_items=2)
            company_source_url = top_company.get("url") or ""
        if company_context and not exa_results:
            reasons[0] = f"Company context: {company_context}"
    if not company_context:
        company_context = fallback_company_context(person)
        if company_context and not exa_results:
            reasons[0] = f"Company context: {company_context}"
    activity_result = best_activity_result(person, activity_results)
    if activity_result:
        activity_context = compact_activity_context(
            activity_result.get("summary") or activity_result.get("title") or "",
            max_items=2,
            max_chars=320,
            item_chars=150,
        )
        activity_source_url = activity_result.get("url") or ""
    if mem0_context:
        reasons[1] = f"Memory context: {mem0_context[0][:180]}"

    return {
        "identity_summary": headline,
        "profile_context": profile_context,
        "profile_source_url": profile_source_url,
        "company_context": company_context,
        "company_source_url": company_source_url,
        "activity_context": activity_context,
        "activity_source_url": activity_source_url,
        "role_summary": person.get("role") or extract_role_from_headline(headline, person.get("company", "")),
        "relevant_angles": relevant_angles_for(person, tags),
        "tags": tags,
        "reasons": reasons,
        "confidence": confidence,
    }


def questions_for(enriched):
    tags = set(enriched["tags"])
    if "AI" in tags and "community" in tags:
        return [
            "What signal tells you two people should meet?",
            "Where does event networking break most: discovery, intro, or follow-up?",
            "Are you building this for organizers or attendees?",
        ]
    if "startup" in tags or "AI" in tags:
        return [
            "What problem are you solving first?",
            "Who feels this pain most urgently?",
            "What would make tonight useful for you?",
        ]
    if "career" in tags:
        return [
            "What kinds of early-career profiles are you looking for?",
            "Which teams or roles are growing right now?",
            "What would make someone stand out to you?",
        ]
    if "community" in tags:
        return [
            "What kind of community are you building around?",
            "What events or formats have worked well for you?",
            "Who are you hoping to meet tonight?",
        ]
    if "finance ops" in tags:
        return [
            "Where do teams usually waste the most money: SaaS, cloud, cards, or renewals?",
            "Who tends to feel the pain first: finance, founders, or ops?",
            "What kind of company gets value from this earliest?",
        ]
    if "sales/BD" in tags:
        return [
            "What kind of customer conversation are you trying to start most often?",
            "Which signal makes an account worth talking to now?",
            "What has changed in outbound this year for your team?",
        ]
    return [
        "What brought you to this event?",
        "What are you currently working on?",
        "Who would be useful for you to meet here?",
    ]


def action_line_for(person, enriched):
    name = person.get("name") or "this person"
    headline = enriched.get("identity_summary") or person.get("headline") or "their work"
    topic = topic_from_headline(headline)
    company = person.get("company") or "their work"
    tags = set(enriched["tags"])
    activity_hook = context_hook(enriched.get("activity_context", ""))
    if activity_hook:
        return f"Ask {name} about {activity_hook} and how it connects to {company}."
    if "AI" in tags and "community" in tags:
        return f"Ask {name} about live event matching - this overlaps directly with Conlax and community tooling."
    if "AI" in tags and "startup" in tags:
        return f"Ask {name} what {company} is building and where AI is most useful."
    if "AI" in tags:
        return f"Ask {name} what they are building and where AI creates the most leverage in {topic}."
    if "startup" in tags:
        return f"Ask {name} what they are building, who it is for, and what help would be useful tonight."
    if "career" in tags:
        return f"Ask {name} what profiles or teams they are looking for, then map whether you can help."
    if "community" in tags:
        return f"Ask {name} what kind of community they are building and what makes a useful connection."
    if "finance ops" in tags:
        return f"Ask {name} where companies usually leak spend first and how {person.get('company') or 'their company'} helps catch it."
    if "sales/BD" in tags:
        return f"Ask {name} what buyer signal matters most in their sales or business development work."
    return f"Ask {name} what brought them here and what would make this event useful."


def topic_from_headline(headline):
    text = (headline or "").strip()
    if not text:
        return "their work"
    lowered = text.lower()
    for marker in [" exploring ", " building ", " working on ", " focused on "]:
        if marker in lowered:
            return text[lowered.index(marker) + len(marker):].strip() or text
    return text


def context_hook(context):
    text = "".join(
        char for char in (context or "")
        if unicodedata.category(char) not in {"Cf", "Mn"}
    ).strip()
    if not text:
        return ""
    text = re.sub(r"^(profile/activity-derived|search-derived, verify|profile-derived|search-derived):\s*", "", text, flags=re.I).strip()
    lines = [re.sub(r"\s+", " ", line.strip(" -")).strip() for line in text.splitlines() if line.strip(" -")]
    if not lines:
        lines = [visible_text(text)]
    preferred = [
        line for line in lines
        if re.match(r"(?i)^(recent activity|posts?|public activity|talks?|content themes?|builder/company updates?):", line)
    ]
    hook = (preferred or lines)[0]
    hook = re.sub(r"^(summary|recent signal|public activity|recent activity|posts?|talks?|content themes?|builder/company updates?):\s*", "", hook, flags=re.I).strip()
    hook = re.sub(r"(?i)^posted about\s+", "", hook).strip()
    hook = re.sub(r"\ba Alibaba\b", "an Alibaba", hook)
    if hook.endswith(".") and not hook.endswith("..."):
        hook = hook[:-1]
    if len(hook) > 96:
        hook = hook[:93].rsplit(" ", 1)[0].rstrip() + "..."
    return hook


def opener_for(person, enriched):
    headline = enriched.get("identity_summary") or person.get("headline") or "your work"
    topic = topic_from_headline(headline)
    company = person.get("company") or "your work"
    tags = set(enriched["tags"])
    activity_hook = context_hook(enriched.get("activity_context", ""))
    if activity_hook:
        return f"Good meeting you earlier - I noticed the {activity_hook} angle. Is that still where you are putting most energy?"
    if "AI" in tags and "community" in tags:
        return f"Good meeting you earlier - your work around {topic} sounded close to live networking. Are you focused more on intros or follow-up?"
    if "AI" in tags and "startup" in tags:
        return f"Good meeting you earlier - what part of {company} are you most focused on right now?"
    if "AI" in tags:
        return f"Good meeting you earlier - the {topic} angle stuck with me. What part are you most focused on right now?"
    if "career" in tags:
        return f"Good meeting you earlier - I noticed the {headline} angle. What kinds of people are you hoping to meet today?"
    if "community" in tags:
        return f"Good meeting you earlier - your {headline} context made me curious. What community problem are you thinking about most?"
    return f"Good meeting you earlier - what are you focused on at {company} right now?"


def fit_follow_up_for(enriched):
    tags = set(enriched["tags"])
    if "AI" in tags and "community" in tags:
        return "I am testing a real-time networking copilot. Would be useful to compare notes."
    if "AI" in tags or "startup" in tags:
        return "This overlaps with tools and startups I am exploring. Worth comparing notes?"
    if "career" in tags:
        return "I may know a few relevant people. What profile should I keep in mind?"
    return "Good to meet you - happy to swap notes after the event."


def relevance_for(person, enriched):
    reasons = enriched["reasons"]
    tags = set(enriched["tags"])
    headline = enriched.get("identity_summary") or person.get("headline") or ""
    if reasons and reasons[0].startswith(("Public context", "Possible public context")):
        return reasons[0]
    if reasons and reasons[0].startswith("Company context"):
        angles = ", ".join(enriched.get("relevant_angles") or [])
        if angles:
            return f"Useful follow-up angle from the meeting: connect their role to {angles}."
        return "Useful follow-up angle from the meeting: connect their role, company problem, and your current goals."
    if reasons and reasons[0].startswith("Profile context"):
        angles = ", ".join(enriched.get("relevant_angles") or [])
        if "AI" in tags and "startup" in tags:
            return "Strong founder + AI signal; useful for a startup/media follow-up."
        if angles:
            return f"Use the profile context to connect their role to {angles}."
        return "Use the profile context to ask a sharper follow-up."
    if "AI" in tags and "community" in tags:
        return f"Works near AI, community, and event follow-up. Headline: {headline}."
    if "AI" in tags:
        return f"Overlaps with AI tools and current builder/startup interests. Headline: {headline}."
    if "startup" in tags:
        return f"Relevant to startups and useful professional relationships. Headline: {headline}."
    if "career" in tags:
        return f"Could be relevant for career opportunities or talent context. Headline: {headline}."
    if "community" in tags:
        return f"Relevant to community building and event follow-up. Headline: {headline}."
    return reasons[0]


def source_for(person):
    subject = person.get("subject", "")
    sender = person.get("from", "")
    if "conlax_test" in subject.lower():
        return "self-test email"
    if "linkedin" in f"{subject} {sender}".lower():
        return "LinkedIn email"
    return "email"


def identity_context_for(person, enriched=None):
    lines = []
    enriched = enriched or {}
    role = enriched.get("role_summary") or person.get("role") or ""
    company = person.get("company") or ""
    location = person.get("location") or ""
    invite_role = person.get("invite_role") or ""
    invite_company = person.get("invite_company") or ""
    if role:
        lines.append(f"Role: {role}")
    if company:
        lines.append(f"Company: {company}")
    if location:
        lines.append(f"Location: {location}")
    invite_bits = ", ".join(bit for bit in [invite_role, invite_company] if bit)
    if invite_bits and invite_bits not in "\n".join(lines):
        lines.append(f"Invite signal: {invite_bits}")
    return "\n".join(lines) if lines else "Role/company/location: not visible in email"


def angles_context_for(enriched):
    angles = enriched.get("relevant_angles") or []
    if not angles:
        return "- Not enough signal yet"
    return "\n".join(f"- {angle}" for angle in angles)


def company_context_for(person, enriched):
    company = person.get("company") or "Company"
    if enriched.get("company_context"):
        exact = is_exact_profile_result(person, {"url": enriched.get("profile_source_url", "")})
        source = "Profile-derived" if enriched.get("profile_context") and exact else "Search-derived, verify" if enriched.get("profile_context") else company
        return f"{source}:\n{enriched['company_context']}"
    if person.get("company"):
        return f"{company}: no extra company context found yet"
    return "No company visible in email"


def activity_context_for(person, enriched):
    context = enriched.get("activity_context") or ""
    if not context:
        return ""
    exact = is_exact_profile_result(person, {"url": enriched.get("activity_source_url", "")})
    source = "Profile/activity-derived" if exact else "Search-derived, verify"
    return f"{source}:\n{context}"


def build_brief(person, enriched):
    qs = questions_for(enriched)
    tags = ", ".join(enriched["tags"])
    action = action_line_for(person, enriched)
    opener = opener_for(person, enriched)
    follow = fit_follow_up_for(enriched)
    relevance = relevance_for(person, enriched)
    source = source_for(person)
    identity_context = identity_context_for(person, enriched)
    angles_context = angles_context_for(enriched)
    company_context = company_context_for(person, enriched)
    activity_context = activity_context_for(person, enriched)
    activity_block = ""
    if activity_context:
        activity_block = f"""
**Recent signal**
{activity_context}
"""
    return f'''**Conlax brief**

**Talk now**
{action}

**Who**
{person["name"]}
{enriched["identity_summary"]}
{identity_context}

**Angles**
{angles_context}

**Profile/company context**
{company_context}
{activity_block}

**Why it matters**
{relevance}

**Open with**
"{opener}"

**Ask next**
1. {qs[0]}
2. {qs[1]}
3. {qs[2]}

**If promising**
{follow}

Tags: {tags}
Confidence: {enriched["confidence"]} - Source: {source}
'''


def update_memory(person, event, enriched):
    memory = load_memory(event)
    seen = memory.setdefault("people_seen", [])
    record = {
        "name": person["name"],
        "headline": person.get("headline", ""),
        "company": person.get("company", ""),
        "location": person.get("location", ""),
        "event": event,
        "tags": enriched["tags"],
        "last_seen": int(time.time()),
    }
    seen = [item for item in seen if item.get("name") != person["name"]]
    seen.append(record)
    memory["people_seen"] = seen[-200:]
    save_json(MEMORY_FILE, memory)
    mem0_add_person(person, event, enriched)


def process_once(mode, event, include_processed=False):
    processed = set(load_json(STATE_FILE, []))
    emails = get_emails(mode)
    briefs = []
    matched = 0
    for email in emails:
        if not is_linkedin_request(email):
            continue
        matched += 1
        person = parse_email(email)
        if person["id"] in processed and not include_processed:
            continue
        memory = load_memory(event)
        enriched = enrich_person(person, event, memory)
        if not email.get("conlax_self_test"):
            update_memory(person, event, enriched)
        brief = build_brief(person, enriched)
        briefs.append(brief)
        deliver_brief(brief)
        processed.add(person["id"])
    save_json(STATE_FILE, sorted(processed))
    return briefs, matched, len(emails)


def main():
    load_env_file(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Conlax LinkedIn connection watcher")
    parser.add_argument("--demo", action="store_true", help="Run demo emails and include processed items")
    parser.add_argument("--once", action="store_true", help="Check once and exit")
    parser.add_argument("--poll", action="store_true", help="Poll continuously")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--event", default=os.getenv("CONLAX_EVENT", "Current event"))
    parser.add_argument("--live-apis", action="store_true", help="Allow demo mode to call live Exa/Mem0 APIs")
    parser.add_argument("--verbose", action="store_true", help="Print a heartbeat line after every inbox check")
    args = parser.parse_args()

    mode = "demo" if args.demo else os.getenv("CONLAX_EMAIL_SOURCE", "demo")
    if args.demo:
        if not args.live_apis:
            os.environ.pop("EXA_API_KEY", None)
            os.environ.pop("MEM0_API_KEY", None)
        briefs, matched, total = process_once("demo", args.event, include_processed=True)
        if args.verbose:
            print(f"CONLAX_CHECKED emails={total} linkedin_matches={matched} new_briefs={len(briefs)} event={args.event}", flush=True)
        print("\n---\n".join(briefs))
        return
    if args.once or not args.poll:
        briefs, matched, total = process_once(mode, args.event)
        if args.verbose:
            print(f"CONLAX_CHECKED emails={total} linkedin_matches={matched} new_briefs={len(briefs)} event={args.event}", flush=True)
        print("\n---\n".join(briefs) if briefs else "CONLAX_NO_NEW_CONNECTIONS")
        return
    while True:
        briefs, matched, total = process_once(mode, args.event)
        if args.verbose:
            print(f"CONLAX_CHECKED emails={total} linkedin_matches={matched} new_briefs={len(briefs)} event={args.event}", flush=True)
        if briefs:
            print("\n---\n".join(briefs), flush=True)
        time.sleep(max(args.poll_seconds, 15))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"CONLAX_ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
