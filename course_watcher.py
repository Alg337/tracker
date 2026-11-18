#!/usr/bin/env python3
"""
Cornell Course Watcher
Checks the Cornell Class Roster API and emails you when a watched course
or section opens up. Standard library only, no installs needed.

Usage:
  python3 course_watcher.py              # check once, email if something opened
  python3 course_watcher.py --status     # print current status, no email
  python3 course_watcher.py --test-email # send a test email and exit
"""
import argparse
import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path

API = "https://classes.cornell.edu/api/2.0"
HERE = Path(__file__).resolve().parent
WATCHLIST_FILE = HERE / "watchlist.json"
STATE_FILE = HERE / "state.json"
STATUS_NAMES = {"O": "Open", "C": "Closed", "W": "Waitlist"}

# --------------------------------------------------------------------------- API

_last_request = 0.0


def api_get(method, **params):
    """GET an API method. Cornell asks for at most 1 request per second."""
    global _last_request
    wait = 1.1 - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)

    url = f"{API}/{method}.json?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "cornell-course-watcher/1.0"})

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            # The API answers some "not found" cases with a 4xx and a JSON message.
            try:
                body = json.load(e)
            except Exception:
                body = {"status": "error", "message": f"HTTP {e.code}"}
            if 400 <= e.code < 500:
                break
            if attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(3)
    _last_request = time.time()

    if body.get("status") != "success":
        raise LookupError(body.get("message") or f"API error for {url}")
    return body["data"]


def latest_roster():
    rosters = api_get("config/rosters")["rosters"]
    return rosters[-1]["slug"]


# ----------------------------------------------------------------- watch logic

WATCH_RE = re.compile(r"^([A-Z]+)\s*(\d{4})(?:\s+([A-Z]{3})\s+(\w+))?$")


def parse_watch(entry):
    """'CS 4780' -> whole course; 'CS 4780 LEC 001' -> one specific section."""
    m = WATCH_RE.match(entry.strip().upper())
    if not m:
        raise ValueError(
            f"Can't read watch entry {entry!r}. Use 'CS 4780' or 'CS 4780 LEC 001'."
        )
    subject, nbr, comp, sec = m.groups()
    return {"label": " ".join(p for p in m.groups() if p), "subject": subject,
            "nbr": nbr, "comp": comp, "sec": sec}


def iter_groups(classes):
    for cls in classes:
        for group in cls.get("enrollGroups") or []:
            yield cls, group.get("classSections") or []


def describe(s):
    return f"{s.get('ssrComponent')} {s.get('section')} (class #{s.get('classNbr')})"


def evaluate(watch, classes, waitlist_ok):
    """Return (is_open, summary_text, details list, title)."""
    def is_open(s):
        st = s.get("openStatus")
        return st == "O" or (waitlist_ok and st == "W")

    title = classes[0].get("titleShort") or classes[0].get("titleLong") or ""

    # One specific section
    if watch["comp"]:
        seen = {}
        for _, sections in iter_groups(classes):
            for s in sections:
                if s.get("ssrComponent") == watch["comp"] and str(s.get("section")) == watch["sec"]:
                    seen[s.get("classNbr")] = s
        if not seen:
            return None, "section not found", [], title
        open_now = [s for s in seen.values() if is_open(s)]
        status = STATUS_NAMES.get(next(iter(seen.values())).get("openStatus"), "Unknown")
        return bool(open_now), status, [describe(s) for s in open_now], title

    # Whole course: "open" means you could actually enroll, i.e. some enroll
    # group has at least one open section of every component it requires
    # (lecture AND discussion AND lab, etc.).
    enrollable_details = []
    total = open_count = 0
    counted = set()
    for _, sections in iter_groups(classes):
        by_comp = {}
        for s in sections:
            by_comp.setdefault(s.get("ssrComponent"), []).append(s)
            if s.get("classNbr") not in counted:
                counted.add(s.get("classNbr"))
                total += 1
                open_count += is_open(s)
        if by_comp and all(any(is_open(s) for s in lst) for lst in by_comp.values()):
            for lst in by_comp.values():
                enrollable_details += [describe(s) for s in lst if is_open(s)]
    details = list(dict.fromkeys(enrollable_details))
    summary = f"{open_count}/{total} sections open"
    return bool(details), summary, details, title


# ----------------------------------------------------------------------- email

def send_email(subject, body):
    to = os.environ.get("EMAIL_TO")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if not (to and user and password):
        sys.exit("Missing EMAIL_TO, SMTP_USER or SMTP_PASSWORD environment variables.")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("EMAIL_FROM", user)
    msg["To"] = to
    msg.set_content(body)

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, password)
            s.send_message(msg)


# ------------------------------------------------------------------------ main

def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def main():
    ap = argparse.ArgumentParser(description="Email me when a Cornell course opens.")
    ap.add_argument("--status", action="store_true", help="print statuses only, no email")
    ap.add_argument("--test-email", action="store_true", help="send a test email and exit")
    args = ap.parse_args()

    if args.test_email:
        send_email("Cornell course watcher: test", "Email is set up correctly.")
        print("Test email sent.")
        return

    cfg = load_json(WATCHLIST_FILE, None)
    if not cfg or not cfg.get("watch"):
        sys.exit(f"Add courses to {WATCHLIST_FILE.name} first.")

    roster = cfg.get("roster", "auto")
    if roster == "auto":
        roster = latest_roster()
    waitlist_ok = bool(cfg.get("count_waitlist_as_open", False))
    watches = [parse_watch(w) for w in cfg["watch"]]

    # One API request per subject.
    by_course = {}
    for subject in sorted({w["subject"] for w in watches}):
        try:
            data = api_get("search/classes", roster=roster, subject=subject)
        except LookupError as e:
            print(f"[warn] {subject} in {roster}: {e}")
            continue
        for cls in data.get("classes", []):
            by_course.setdefault((cls.get("subject"), str(cls.get("catalogNbr"))), []).append(cls)

    state = load_json(STATE_FILE, {})
    roster_state = state.setdefault(roster, {})
    newly_open = []

    print(f"Roster {roster}  ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    for w in watches:
        classes = by_course.get((w["subject"], w["nbr"]))
        if not classes:
            print(f"  {w['label']:<20} not found in {roster}")
            continue
        is_open, summary, details, title = evaluate(w, classes, waitlist_ok)
        if is_open is None:
            print(f"  {w['label']:<20} {summary}")
            continue
        print(f"  {w['label']:<20} {'OPEN  ' if is_open else 'closed'}  {summary}  {title}")

        was_open = roster_state.get(w["label"])
        if is_open and not was_open:
            url = f"https://classes.cornell.edu/browse/roster/{roster}/class/{w['subject']}/{w['nbr']}"
            newly_open.append((w, title, details, url))
        roster_state[w["label"]] = is_open

    if args.status:
        return

    if newly_open:
        names = ", ".join(w["label"] for w, *_ in newly_open)
        lines = []
        for w, title, details, url in newly_open:
            lines.append(f"{w['label']}  {title}")
            lines += [f"    open: {d}" for d in details]
            lines.append(f"    {url}\n")
        lines.append("Seats go fast, so enroll right away.")
        send_email(f"OPEN: {names} ({roster})", "\n".join(lines))
        print(f"Emailed about: {names}")

    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
