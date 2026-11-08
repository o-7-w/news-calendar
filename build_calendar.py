#!/usr/bin/env python3
"""
Baut aus dem ForexFactory-Wochenfeed eine gefilterte iCal-Datei (docs/news.ics).

Filter (anpassbar unten):
  - Rot (High) fuer alle Waehrungen in RED_CURRENCIES
  - Orange (Medium) nur fuer ORANGE_CURRENCIES (Puffer fuer QT Power, dort ist
    "restricted news" nicht definiert)
Eintrag zur offiziellen Uhrzeit, Erinnerung ALARM_MIN_BEFORE Minuten vorher.
Termine mit gleicher Uhrzeit werden zu einem Kalendereintrag zusammengefasst.
Nur Python-Standardbibliothek.
"""
import hashlib
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------- Einstellungen ----------
FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
RED_CURRENCIES = {"AUD", "CAD", "CHF", "CNY", "EUR", "GBP", "JPY", "NZD", "USD"}
ORANGE_CURRENCIES = {"USD", "EUR", "GBP"}  # leeres Set = Orange komplett aus
WINDOW_MIN = 5            # QT: +/-5 Min; 5ers: +/-2 Min (liegt innerhalb)
ALARM_MIN_BEFORE = 10     # Erinnerung vor der Veroeffentlichung
EVENT_LEN_MIN = 5         # Laenge des Kalendereintrags ab Veroeffentlichung
OUT_FILE = Path(__file__).parent / "docs" / "news.ics"
CAL_NAME = "News-Sperren (FF Rot + Orange USD/EUR/GBP)"
# -----------------------------------

IMPACT_LABEL = {"High": "ROT", "Medium": "ORANGE"}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "news-calendar-builder/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def keep(ev):
    imp, cur = ev.get("impact"), ev.get("country")
    if imp == "High":
        return cur in RED_CURRENCIES
    if imp == "Medium":
        return cur in ORANGE_CURRENCIES
    return False


def parse_dt(s):
    # Format im Feed: 2026-11-10T08:30:00-05:00
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def esc(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))


def fold(line):
    # RFC 5545: Zeilen max. 75 Oktette
    out, raw = [], line.encode("utf-8")
    while len(raw) > 75:
        cut = 75
        while (raw[cut] & 0xC0) == 0x80:  # nicht mitten in UTF-8-Zeichen trennen
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = b" " + raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n".join(out)


def fmt(dt):
    return dt.strftime("%Y%m%dT%H%M%SZ")


BERLIN = ZoneInfo("Europe/Berlin")
VTIMEZONE = [
    "BEGIN:VTIMEZONE", "TZID:Europe/Berlin",
    "BEGIN:DAYLIGHT", "TZOFFSETFROM:+0100", "TZOFFSETTO:+0200", "TZNAME:CEST",
    "DTSTART:19700329T020000", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU", "END:DAYLIGHT",
    "BEGIN:STANDARD", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0100", "TZNAME:CET",
    "DTSTART:19701025T030000", "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD",
    "END:VTIMEZONE",
]


def fmt_local(dt):
    return dt.astimezone(BERLIN).strftime("%Y%m%dT%H%M%S")


def build(events):
    groups = defaultdict(list)
    for ev in events:
        if keep(ev):
            groups[parse_dt(ev["date"])].append(ev)

    now = fmt(datetime.now(timezone.utc))
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ff-news-calendar//DE",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(CAL_NAME)}",
        "X-PUBLISHED-TTL:PT12H", "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-WR-TIMEZONE:Europe/Berlin",
    ] + VTIMEZONE
    for t in sorted(groups):
        evs = sorted(groups[t], key=lambda e: (e["impact"] != "High", e["country"]))
        red = any(e["impact"] == "High" for e in evs)
        curs = "/".join(sorted({e["country"] for e in evs}))
        tag = "\U0001F534" if red else "\U0001F7E0 (QT)"
        summary = f"{tag} {curs}: " + ", ".join(e["title"] for e in evs)
        desc = "\n".join(
            f"{IMPACT_LABEL.get(e['impact'], e['impact'])} {e['country']} {e['title']}"
            f" | Fc {e.get('forecast') or '-'} | Prev {e.get('previous') or '-'}"
            for e in evs)
        desc += (f"\n\nSperrfenster +/-{WINDOW_MIN} Min. 5ers: nur ROT, +/-2 Min, "
                 "betroffene Waehrung/Index, keine Order-Ausfuehrung (auch Pending). "
                 "QT Power: +/-5 Min, kein Eroeffnen/Schliessen. IF Micro Pro: keine Sperre.")
        uid = f"{fmt(t)}-{curs}-{hashlib.md5(summary.encode()).hexdigest()[:8]}@ff-news-calendar"
        lines += [
            "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{now}",
            f"DTSTART;TZID=Europe/Berlin:{fmt_local(t)}",
            f"DTEND;TZID=Europe/Berlin:{fmt_local(t + timedelta(minutes=EVENT_LEN_MIN))}",
            f"SUMMARY:{esc(summary)}",
            "TRANSP:TRANSPARENT",
            "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(summary)}",
            f"TRIGGER:-PT{ALARM_MIN_BEFORE}M", "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n", len(groups)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else None
    events = json.loads(Path(src).read_text()) if src else fetch(FEED_URL)
    ics, n = build(events)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(ics, encoding="utf-8", newline="")
    print(f"{n} Sperrfenster geschrieben nach {OUT_FILE}")


if __name__ == "__main__":
    main()
