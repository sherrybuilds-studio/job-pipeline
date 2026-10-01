"""Read-only inbox pull for the application funnel: who wrote back?

    python scripts/mailcheck.py [--since 01-Sep-2026] [--folder INBOX --folder Spam] [--out mail.json]

Opens one mailbox over IMAP in READ-ONLY mode (nothing is marked read, moved
or deleted), lists every message since the date and prints sender, subject and
date. Credentials come from the environment (IMAP_HOST, IMAP_USER,
IMAP_PASSWORD, usually an app password) and are never printed. Record what you
find with `python -m pipeline tracker stage <company> replied`.
"""
import argparse
import email
import email.utils
import html
import imaplib
import json
import os
import re
import sys
from email.header import decode_header, make_header
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config  # noqa: E402


def body_text(msg) -> str:
    plain, rich = "", ""
    for part in msg.walk():
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        try:
            body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        except (AttributeError, LookupError):
            continue
        if part.get_content_subtype() == "plain":
            plain += body
        else:
            rich += body
    if not plain.strip():  # many ATS send HTML only
        rich = re.sub(r"(?is)<(script|style).*?</\1>", " ", rich)
        plain = html.unescape(re.sub(r"(?s)<[^>]+>", " ", rich))
    return re.sub(r"\s+", " ", plain).strip()


def pull(host: str, user: str, password: str, since: str, folders: list[str]) -> list[dict]:
    box = imaplib.IMAP4_SSL(host)
    box.login(user, password)
    out = []
    try:
        for folder in folders:
            if box.select(f'"{folder}"', readonly=True)[0] != "OK":
                print(f"{folder}: not found, skipped")
                continue
            _, data = box.search(None, f"(SINCE {since})")
            for num in data[0].split():
                _, raw = box.fetch(num, "(BODY.PEEK[])")
                msg = email.message_from_bytes(raw[0][1])
                when = email.utils.parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
                out.append({"folder": folder, "from": str(make_header(decode_header(msg.get("From", "")))),
                            "subject": str(make_header(decode_header(msg.get("Subject", "")))),
                            "date": when.isoformat() if when else "", "text": body_text(msg)[:1500]})
    finally:
        box.logout()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only IMAP pull: list messages since a date")
    ap.add_argument("--since", default="01-Sep-2026", help="IMAP date, e.g. 01-Sep-2026")
    ap.add_argument("--folder", action="append", dest="folders", help="mailbox folder (repeatable; default INBOX)")
    ap.add_argument("--out", help="also write the messages as JSON to this file")
    args = ap.parse_args(argv)
    config.load_env()
    host, user, password = os.getenv("IMAP_HOST"), os.getenv("IMAP_USER"), os.getenv("IMAP_PASSWORD")
    if not (host and user and password):
        print("IMAP_HOST, IMAP_USER and IMAP_PASSWORD must be set")
        return 1
    rows = sorted(pull(host, user, password, args.since, args.folders or ["INBOX"]), key=lambda r: r["date"])
    print(f"{len(rows)} messages since {args.since}")
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    for r in rows:
        print(f"{r['date'][:16]} [{r['folder']}] {r['from'][:48]:48s} | {r['subject'][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
