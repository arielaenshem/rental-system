#!/usr/bin/env python3
"""
Email reminders for the HTML property manager (arelis_properties.html).

Reads the JSON backup exported from the HTML page and emails a summary of
upcoming dates (agreement end, last cheque, option notice/end, security
expiry) that fall within the configured number of days (default 30).
Each reminder is sent once; sent keys are kept in logs/html_reminders_sent.json.

Usage:
    python html_reminders.py arelis_backup.json
    python html_reminders.py arelis_backup.json --days 45 --dry-run

Run it daily (cron / Task Scheduler) to get automatic reminders.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from src.email_service.email_sender import EmailService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("html_reminders")

SENT_LOG = Path("logs/html_reminders_sent.json")


def _parse(d: str):
    try:
        return date.fromisoformat(d) if d else None
    except ValueError:
        return None


def collect_events(data: dict) -> list[dict]:
    events = []
    for p in data.get("properties", []):
        for u in p.get("units", []):
            who = u.get("tenant") or u.get("name") or ""
            items = [("סיום הסכם", u.get("end")), ("סיום צ׳קים", u.get("chequesEnd"))]
            if u.get("hasOption"):
                items += [("מועד הודעה על מימוש אופציה", u.get("optionNotice")),
                          ("סיום תקופת אופציה", u.get("optionEnd"))]
            items += [(f"פקיעת ביטחון – {s.get('type', '')}", s.get("expiry")) for s in u.get("securities", [])]
            for kind, d in items:
                dt = _parse(d)
                if dt:
                    events.append({"key": f"{u.get('id')}|{kind}|{d}", "kind": kind,
                                   "property": p.get("name", ""), "who": who, "date": dt})
    return events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("backup", help="JSON backup exported from arelis_properties.html")
    ap.add_argument("--days", type=int, help="days before the date to remind (default: from backup settings)")
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending")
    args = ap.parse_args()

    data = json.loads(Path(args.backup).read_text(encoding="utf-8"))
    settings = data.get("settings", {})
    days = args.days or int(settings.get("reminderDays") or 30)
    today = date.today()

    sent = json.loads(SENT_LOG.read_text(encoding="utf-8")) if SENT_LOG.exists() else {}
    due = [e for e in collect_events(data)
           if (e["date"] - today).days <= days and e["key"] not in sent
           and not data.get("dismissed", {}).get(e["key"])]
    if not due:
        logger.info("No new reminders.")
        return 0

    due.sort(key=lambda e: e["date"])
    lines = [f"{e['kind']} – {e['property']} – {e['who']} – {e['date']:%d/%m/%Y} "
             f"({(e['date'] - today).days} ימים)" for e in due]
    company = settings.get("company", "")
    subject = f"[{company}] תזכורת: {len(due)} מועדים קרובים"
    text = "מועדים קרובים בתיק הנכסים:\n\n" + "\n".join(f"• {l}" for l in lines)
    html = ('<div dir="rtl" style="font-family:Arial,sans-serif"><h3>מועדים קרובים בתיק הנכסים</h3><ul>'
            + "".join(f"<li>{l}</li>" for l in lines) + "</ul></div>")

    if args.dry_run:
        print(subject + "\n" + text)
        return 0

    svc = EmailService(renewal_recipient=settings.get("email") or None)
    if not svc.send_custom(subject, html, text):
        return 1
    sent.update({e["key"]: today.isoformat() for e in due})
    SENT_LOG.parent.mkdir(exist_ok=True)
    SENT_LOG.write_text(json.dumps(sent, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
