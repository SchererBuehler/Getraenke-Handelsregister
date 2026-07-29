#!/usr/bin/env python3
"""Swiss beverage-sector commercial-register monitor.

Fetches daily SOGC/SHAB publications through the official ZEFIX PublicREST API,
classifies new registrations, mutations and closures/deletions, enriches public
contact details, stores results in SQLite, and optionally sends a Telegram digest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("ZEFIX_BASE_URL", "https://www.zefix.admin.ch/ZefixPublicREST/api/v1").rstrip("/")
DB_PATH = Path(os.getenv("DB_PATH", "registry_bot.sqlite3"))
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
ENRICH_CONTACTS = os.getenv("ENRICH_CONTACTS", "true").lower() in {"1", "true", "yes", "on"}
MAX_CONTACT_PAGES = int(os.getenv("MAX_CONTACT_PAGES", "3"))
USER_AGENT = os.getenv("USER_AGENT", "CH-Beverage-Registry-Monitor/2.0 (+public company data research)")

BEVERAGE_PATTERNS = {
    "allgemein": [r"getränk", r"boisson", r"bevanda", r"beverage", r"drink"],
    "bier": [r"\bbier", r"brauerei", r"brasserie", r"\bbirra", r"brewery", r"microbrew"],
    "wein": [r"\bwein", r"\bvin\b", r"\bvino", r"winery", r"weingut", r"cave\s+à\s+vin"],
    "spirituosen": [r"spirituosen", r"spiritueux", r"distillat", r"distillerie", r"liquor", r"\bgin\b", r"whisky", r"vodka", r"\brum\b"],
    "alkoholfrei": [r"soft\s*drink", r"limonade", r"\bsoda\b", r"\bjus\b", r"saft", r"juice", r"eistee", r"thé\s+froid", r"energy\s*drink"],
    "wasser": [r"mineralwasser", r"eau\s+minérale", r"acqua\s+minerale", r"bottled\s+water", r"quellwasser"],
    "kaffee_tee": [r"kaffee", r"café", r"coffee", r"\btee\b", r"\bthé\b", r"\btea\b", r"rösterei", r"torrefazione"],
    "handel_vertrieb": [r"import.*getränk", r"export.*getränk", r"handel.*getränk", r"distribution.*boisson", r"commerce.*boisson", r"beverage.*distribution"],
}

EVENT_PATTERNS = {
    "new": [r"neueintragung", r"nouvelle inscription", r"nuova iscrizione", r"new registration", r"gründung", r"constitution", r"costituzione", r"incorporation"],
    "mutation": [r"mutation", r"änderung", r"aenderung", r"modification", r"modifica", r"statutenänderung", r"sitzverlegung", r"zweckänderung", r"firma neu", r"domizil neu"],
    "closure": [r"löschung", r"loeschung", r"radiation", r"cancellazione", r"liquidation", r"liquidazione", r"auflösung", r"aufloesung", r"dissolution", r"konkurs", r"faillite", r"fallimento"],
}

EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.I)
PHONE_RE = re.compile(r"(?:(?:\+|00)41|0)\s*(?:\(0\)\s*)?(?:\d[\s./()-]*){8,12}")
URL_RE = re.compile(r"https?://[^\s<>\"']+|\bwww\.[^\s<>\"']+", re.I)
CONTACT_LINK_RE = re.compile(r"(?:kontakt|contact|impressum|imprint|contatti|mentions-legales|über-uns|about)", re.I)
PERSON_KEY_RE = re.compile(r"person|member|director|manager|owner|partner|signatory|zeichnungs|gesellschafter|verwaltungsrat|geschäftsführer|liquidator|inhaber|associé|gérant|administrateur|titolare", re.I)


@dataclass
class Match:
    publication_id: str
    publication_date: str
    event_type: str
    company_name: str
    uid: str
    canton: str
    locality: str
    purpose: str
    categories: list[str]
    confidence: int
    contact_names: list[str]
    emails: list[str]
    phones: list[str]
    website: str
    contact_sources: dict[str, list[str]]
    source_url: str
    raw: dict[str, Any]


def setup_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            publication_id TEXT PRIMARY KEY,
            publication_date TEXT NOT NULL,
            company_name TEXT,
            uid TEXT,
            canton TEXT,
            locality TEXT,
            purpose TEXT,
            categories TEXT,
            confidence INTEGER,
            source_url TEXT,
            raw_json TEXT,
            created_at TEXT NOT NULL
        )
    """)
    existing = {row[1] for row in con.execute("PRAGMA table_info(matches)")}
    additions = {
        "event_type": "TEXT DEFAULT 'new'",
        "contact_names": "TEXT DEFAULT '[]'",
        "emails": "TEXT DEFAULT '[]'",
        "phones": "TEXT DEFAULT '[]'",
        "website": "TEXT DEFAULT ''",
        "contact_sources": "TEXT DEFAULT '{}'",
    }
    for column, definition in additions.items():
        if column not in existing:
            con.execute(f"ALTER TABLE matches ADD COLUMN {column} {definition}")
    con.commit()
    return con


def fetch_publications(day: date) -> Any:
    """Fetch public SHAB/Zefix publications without authentication."""
    url = f"{BASE_URL}/sogc/bydate/{day.isoformat()}"
    response = requests.get(
        url,
        timeout=TIMEOUT,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.json()


def iter_records(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        yield from (item for item in payload if isinstance(item, dict)); return
    if not isinstance(payload, dict): return
    for key in ("content", "results", "publications", "sogcPublications", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            yield from (item for item in value if isinstance(item, dict)); return
    for value in payload.values():
        if isinstance(value, list): yield from (item for item in value if isinstance(item, dict))
        elif isinstance(value, dict): yield from iter_records(value)


def flatten_text(obj: Any) -> str:
    parts: list[str] = []
    def walk(value: Any) -> None:
        if isinstance(value, str): parts.append(value)
        elif isinstance(value, dict):
            for nested in value.values(): walk(nested)
        elif isinstance(value, list):
            for nested in value: walk(nested)
    walk(obj)
    return " | ".join(parts)


def first_value(data: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            if isinstance(value, dict):
                for sub in ("de", "fr", "it", "en", "value", "name", "text"):
                    if value.get(sub): return str(value[sub])
            if not isinstance(value, (dict, list)): return str(value)
    return ""


def detect_event_type(record: dict[str, Any]) -> str:
    explicit = first_value(record, ["mutationType", "publicationType", "type", "messageType", "registrationType", "sogcType"])
    text = f"{explicit} {flatten_text(record)}".lower()
    # Closure wins because closure publications can also contain generic mutation wording.
    for event_type in ("closure", "new", "mutation"):
        if any(re.search(pattern, text, re.I) for pattern in EVENT_PATTERNS[event_type]):
            return event_type
    return "other"


def classify(text: str) -> tuple[list[str], int]:
    categories = [name for name, patterns in BEVERAGE_PATTERNS.items() if any(re.search(pattern, text, re.I) for pattern in patterns)]
    if not categories: return [], 0
    specific = [category for category in categories if category not in {"allgemein", "handel_vertrieb"}]
    score = 80 if specific else 60
    if len(categories) >= 2 or len(specific) >= 2: score = 95
    return categories, score


def unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set(); output: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", value).strip(" \t\r\n,;:.-")
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key); output.append(cleaned)
    return output


def extract_registry_contacts(record: dict[str, Any]) -> tuple[list[str], list[str], list[str], list[str]]:
    text = flatten_text(record)
    emails = unique(match.group(1).lower() for match in EMAIL_RE.finditer(text))
    phones = unique(match.group(0) for match in PHONE_RE.finditer(text))
    websites = unique((url if url.lower().startswith("http") else f"https://{url}") for url in URL_RE.findall(text))
    names: list[str] = []

    def walk(value: Any, key_hint: str = "") -> None:
        if isinstance(value, dict):
            if PERSON_KEY_RE.search(key_hint):
                first = first_value(value, ["firstName", "firstname", "givenName", "first_name"])
                last = first_value(value, ["lastName", "lastname", "familyName", "surname", "last_name"])
                full = first_value(value, ["fullName", "personName", "name", "displayName"])
                candidate = full or " ".join(part for part in (first, last) if part)
                if candidate and 2 <= len(candidate.split()) <= 7 and not any(char.isdigit() for char in candidate): names.append(candidate)
            for key, nested in value.items(): walk(nested, key)
        elif isinstance(value, list):
            for nested in value: walk(nested, key_hint)
    walk(record)
    return unique(names), emails, phones, websites


def html_to_text(html: str) -> str:
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.I | re.S)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def enrich_from_website(start_url: str) -> tuple[list[str], list[str], list[str]]:
    if not start_url or not ENRICH_CONTACTS: return [], [], []
    parsed = urlparse(start_url)
    if parsed.scheme not in {"http", "https"}: return [], [], []
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    queue = [start_url]; visited: set[str] = set(); emails: list[str] = []; phones: list[str] = []; sources: list[str] = []
    while queue and len(visited) < MAX_CONTACT_PAGES:
        url = queue.pop(0)
        if url in visited: continue
        visited.add(url)
        try:
            response = session.get(url, timeout=TIMEOUT, allow_redirects=True)
            if response.status_code >= 400 or "text/html" not in response.headers.get("Content-Type", ""): continue
            html = response.text[:2_000_000]
            text = html_to_text(html)
            page_emails = [match.group(1).lower() for match in EMAIL_RE.finditer(text + " " + html)]
            page_phones = [match.group(0) for match in PHONE_RE.finditer(text)]
            if page_emails or page_phones: sources.append(response.url)
            emails.extend(page_emails); phones.extend(page_phones)
            if len(visited) == 1:
                for href, label in re.findall(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html, re.I | re.S):
                    absolute = urljoin(response.url, href)
                    if urlparse(absolute).netloc == urlparse(response.url).netloc and CONTACT_LINK_RE.search(href + " " + html_to_text(label)):
                        queue.append(absolute)
        except requests.RequestException as exc:
            logging.debug("Kontakt-Enrichment fehlgeschlagen für %s: %s", url, exc)
    blocked_domains = {"example.com", "wixpress.com", "sentry.io"}
    emails = [email for email in unique(emails) if email.rsplit("@", 1)[-1] not in blocked_domains]
    return emails, unique(phones), unique(sources)


def normalize(record: dict[str, Any], day: date, event_type: str) -> Match | None:
    text = flatten_text(record)
    categories, confidence = classify(text)
    if not categories: return None
    company = first_value(record, ["companyName", "name", "legalName", "firm", "company"])
    uid = first_value(record, ["uid", "uidFormatted", "companyUid", "enterpriseId"])
    canton = first_value(record, ["canton", "cantonCode", "registryCanton"])
    locality = first_value(record, ["locality", "seat", "municipality", "city", "registeredOffice"])
    purpose = first_value(record, ["purpose", "companyPurpose", "objective", "description", "text"])
    raw_id = first_value(record, ["id", "publicationId", "sogcId", "messageId", "journalId"])
    stable = raw_id or hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    source_url = first_value(record, ["url", "publicationUrl", "link"])
    names, registry_emails, registry_phones, websites = extract_registry_contacts(record)
    website = websites[0] if websites else ""
    web_emails, web_phones, web_sources = enrich_from_website(website)
    sources = {
        "names": [source_url or "ZEFIX/SHAB publication"] if names else [],
        "emails": ([source_url or "ZEFIX/SHAB publication"] if registry_emails else []) + web_sources,
        "phones": ([source_url or "ZEFIX/SHAB publication"] if registry_phones else []) + web_sources,
    }
    return Match(stable, day.isoformat(), event_type, company, uid, canton, locality, purpose or text[:2000], categories, confidence,
                 names, unique(registry_emails + web_emails), unique(registry_phones + web_phones), website,
                 {key: unique(value) for key, value in sources.items()}, source_url, record)


def save_new(con: sqlite3.Connection, matches: Iterable[Match]) -> list[Match]:
    fresh: list[Match] = []
    sql = """INSERT INTO matches (
        publication_id, publication_date, company_name, uid, canton, locality, purpose,
        categories, confidence, source_url, raw_json, created_at, event_type,
        contact_names, emails, phones, website, contact_sources
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    for match in matches:
        try:
            con.execute(sql, (
                match.publication_id, match.publication_date, match.company_name, match.uid, match.canton,
                match.locality, match.purpose, json.dumps(match.categories, ensure_ascii=False), match.confidence,
                match.source_url, json.dumps(match.raw, ensure_ascii=False), datetime.now(timezone.utc).isoformat(),
                match.event_type, json.dumps(match.contact_names, ensure_ascii=False), json.dumps(match.emails),
                json.dumps(match.phones), match.website, json.dumps(match.contact_sources, ensure_ascii=False)
            ))
            fresh.append(match)
        except sqlite3.IntegrityError: pass
    con.commit(); return fresh


def send_telegram(matches: list[Match], day: date) -> None:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id or not matches: return
    labels = {"new": "Neueintragung", "mutation": "Mutation", "closure": "Schliessung/Löschung"}
    lines = [f"🥤 Getränke-Handelsregister Schweiz ({day.isoformat()}):"]
    for match in matches[:30]:
        lines.append(f"\n• [{labels.get(match.event_type, match.event_type)}] {match.company_name or 'Unbekannte Firma'} — {match.locality or match.canton}\n  {', '.join(match.categories)} | Score {match.confidence}\n  UID: {match.uid or '–'}")
        if match.contact_names: lines.append(f"  Personen: {', '.join(match.contact_names[:3])}")
        if match.emails: lines.append(f"  Mail: {', '.join(match.emails[:2])}")
        if match.phones: lines.append(f"  Telefon: {', '.join(match.phones[:2])}")
        if match.source_url: lines.append(f"  {match.source_url}")
    if len(matches) > 30: lines.append(f"\n… plus {len(matches)-30} weitere Treffer.")
    response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": "\n".join(lines), "disable_web_page_preview": True}, timeout=TIMEOUT)
    response.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Publikationsdatum YYYY-MM-DD; Standard: gestern")
    parser.add_argument("--min-confidence", type=int, default=int(os.getenv("MIN_CONFIDENCE", "60")))
    parser.add_argument("--events", default=os.getenv("EVENT_TYPES", "new,mutation,closure"), help="Kommagetrennt: new,mutation,closure")
    parser.add_argument("--json", action="store_true", help="Neue Treffer als JSON ausgeben")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
    day = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
    wanted_events = {item.strip() for item in args.events.split(",") if item.strip()}
    try:
        records = list(iter_records(fetch_publications(day)))
        matches: list[Match] = []
        for record in records:
            event_type = detect_event_type(record)
            if event_type not in wanted_events: continue
            match = normalize(record, day, event_type)
            if match and match.confidence >= args.min_confidence: matches.append(match)
        fresh = save_new(setup_db(), matches)
        send_telegram(fresh, day)
        if args.json: print(json.dumps([asdict(match) for match in fresh], ensure_ascii=False, indent=2))
        else:
            counts = {event: sum(1 for match in fresh if match.event_type == event) for event in ("new", "mutation", "closure")}
            print(f"{len(records)} Publikationen geprüft, {len(fresh)} neue Getränke-Treffer gespeichert "
                  f"(Neueintragungen {counts['new']}, Mutationen {counts['mutation']}, Schliessungen/Löschungen {counts['closure']}).")
            for match in fresh:
                contact = ", ".join((match.emails + match.phones)[:2]) or "keine öffentlichen Kontaktdaten"
                print(f"- [{match.event_type}] {match.company_name} | {match.locality or match.canton} | {contact}")
        return 0
    except Exception as exc:
        logging.exception("Bot fehlgeschlagen: %s", exc); return 1


if __name__ == "__main__":
    raise SystemExit(main())
