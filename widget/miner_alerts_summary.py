#!/usr/bin/env python3
import os, sys
from datetime import datetime, timedelta

PHRASE = {"NOT_MINING": "not mining", "NO_SHARES": "no shares accepted", "BACKUP_POOL": "on backup pool",
          "OFFLINE": "offline", "HOT": "running hot"}


def when(dt, now):
    t = f"{dt.hour % 12 or 12}:{dt.minute:02d}{'am' if dt.hour < 12 else 'pm'}"
    if dt.date() == now.date():
        return t
    if dt.date() == (now - timedelta(days=1)).date():
        return "yesterday " + t
    return f"{dt:%b} {dt.day} {t}"


def summary(path, now=None):
    now = now or datetime.now()
    since, open_, spans = now - timedelta(hours=24), {}, []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        p = line.split(" | ", 4)
        try:
            at = datetime.strptime(p[0], "%Y-%m-%d %H:%M:%S")
            name, code, kind = p[1], p[2], p[3]
        except (ValueError, IndexError):
            continue
        key = (name, code)
        if kind == "raised" and key not in open_:
            open_[key] = at
        elif kind == "cleared" and key in open_:
            spans.append((open_.pop(key), at, name, code))
    spans += [(a, None, n, c) for (n, c), a in open_.items()]
    parts = []
    for a, b, name, code in sorted(spans, key=lambda s: s[0]):
        if (b or now) < since or a > now or code not in PHRASE:
            continue
        parts.append(f"{name} {PHRASE[code]} " + (f"since {when(a, now)}" if b is None else f"{when(a, now)} to {when(b, now)}"))
    return "Miner alerts in the last 24 hours: " + ("; ".join(parts) if parts else "none") + "."


if __name__ == "__main__":
    print(summary(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "alerts.log")))
