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
ORANGE_CURRENCIES = set()  # Orange aus: QT Power hat keine News-Regel (Help Center, Stand 29.10.2026)
WINDOW_MIN = 5            # QT: +/-5 Min; 5ers: +/-2 Min (liegt innerhalb)
ALARM_MIN_BEFORE = 10     # Erinnerung vor der Veroeffentlichung
EVENT_LEN_MIN = 5         # Laenge des Kalendereintrags ab Veroeffentlichung
OUT_FILE = Path(__file__).parent / "docs" / "news.ics"
CAL_NAME = "News-Sperren (FF Rot)"
# -----------------------------------

# NYSE-Feiertage und verkuerzte Handelstage (Quelle: NYSE/ICE Pressemitteilung 23.12.2025)
US_HOLIDAYS = [
    ("2026-11-26", "Thanksgiving"), ("2026-12-25", "Christmas"),
    ("2027-01-01", "New Year's Day"), ("2027-01-18", "Martin Luther King Jr. Day"),
    ("2027-02-15", "Washington's Birthday"), ("2027-03-26", "Good Friday"),
    ("2027-05-31", "Memorial Day"), ("2027-06-18", "Juneteenth"),
    ("2027-07-05", "Independence Day"), ("2027-09-06", "Labor Day"),
    ("2027-11-25", "Thanksgiving"), ("2027-12-24", "Christmas"),
    ("2028-01-17", "Martin Luther King Jr. Day"), ("2028-02-21", "Washington's Birthday"),
    ("2028-04-14", "Good Friday"), ("2028-05-29", "Memorial Day"),
    ("2028-06-19", "Juneteenth"), ("2028-07-04", "Independence Day"),
    ("2028-09-04", "Labor Day"), ("2028-11-23", "Thanksgiving"),
    ("2028-12-25", "Christmas"),
]
# Eurex (FDAX/FESX) geschlossen - Quelle: Eurex Trading Calendar 2026 + indikativer Kalender 2027/28
# Flagge: "EU" = Eurex komplett zu (alle Derivate), "DE" = nur deutsche Aktien-/Indexderivate (FDAX)
EUREX_HOLIDAYS = [
    ("2026-12-24", "Heiligabend", "EU"), ("2026-12-25", "1. Weihnachtstag", "EU"),
    ("2026-12-31", "Silvester", "EU"),
    ("2027-01-01", "Neujahr", "EU"), ("2027-03-26", "Karfreitag", "EU"), ("2027-03-29", "Ostermontag", "EU"),
    ("2027-12-24", "Heiligabend", "EU"), ("2027-12-31", "Silvester", "EU"),
    ("2028-04-14", "Karfreitag", "EU"), ("2028-04-17", "Ostermontag", "EU"), ("2028-05-01", "Tag der Arbeit", "EU"),
    ("2028-12-25", "1. Weihnachtstag", "EU"), ("2028-12-26", "2. Weihnachtstag", "EU"),
]
US_EARLY_CLOSE = [  # Schluss 13:00 New York
    ("2026-11-27", "Tag nach Thanksgiving"), ("2026-12-24", "Heiligabend"),
    ("2027-11-26", "Tag nach Thanksgiving"), ("2028-07-03", "Vortag Independence Day"),
    ("2028-11-24", "Tag nach Thanksgiving"),
]
HOLIDAY_ALARM = "-PT6H"   # Ganztagestermin: Hinweis am Vortag 18:00
NEW_YORK = "America/New_York"

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
    today = datetime.now(BERLIN).date()
    # Eurex: kein Ganztagstermin (Feiertage kommen aus dem iCloud-Kalender),
    # nur ein Hinweis-Termin am Vortag 18:00
    flags = {"EU": "\U0001F1EA\U0001F1FA", "DE": "\U0001F1E9\U0001F1EA"}
    for d, name, scope in EUREX_HOLIDAYS:
        day = datetime.fromisoformat(d).date()
        if day < today:
            continue
        start = datetime.combine(day - timedelta(days=1), datetime.min.time()).replace(
            hour=18, tzinfo=BERLIN)
        summary = f"EUREX closed tomorrow {flags[scope]}"
        lines += [
            "BEGIN:VEVENT", f"UID:eurex-holiday-{d}@ff-news-calendar", f"DTSTAMP:{now}",
            f"DTSTART;TZID=Europe/Berlin:{fmt_local(start)}",
            f"DTEND;TZID=Europe/Berlin:{fmt_local(start + timedelta(minutes=EVENT_LEN_MIN))}",
            f"SUMMARY:{esc(summary)}", "TRANSP:TRANSPARENT",
            "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(summary)}",
            "TRIGGER:PT0M", "END:VALARM", "END:VEVENT",
        ]
    holidays = [(d, n, "\U0001F1FA\U0001F1F8", "us") for d, n in US_HOLIDAYS]
    for d, name, flag, src in holidays:
        day = datetime.fromisoformat(d).date()
        if day < today:
            continue
        summary = f"{name} {flag}"
        lines += [
            "BEGIN:VEVENT", f"UID:{src}-holiday-{d}@ff-news-calendar", f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{esc(summary)}", "TRANSP:TRANSPARENT",
            "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(summary)}",
            f"TRIGGER:{HOLIDAY_ALARM}", "END:VALARM", "END:VEVENT",
        ]
    for d, name in US_EARLY_CLOSE:
        close = datetime.fromisoformat(d + "T13:00").replace(tzinfo=ZoneInfo(NEW_YORK))
        if close.date() < today:
            continue
        summary = f"US Close {close.astimezone(BERLIN):%H:%M} \U0001F1FA\U0001F1F8"
        lines += [
            "BEGIN:VEVENT", f"UID:us-early-{d}@ff-news-calendar", f"DTSTAMP:{now}",
            f"DTSTART;TZID=Europe/Berlin:{fmt_local(close)}",
            f"DTEND;TZID=Europe/Berlin:{fmt_local(close + timedelta(minutes=EVENT_LEN_MIN))}",
            f"SUMMARY:{esc(summary)}", "TRANSP:TRANSPARENT",
            "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(summary)}",
            "TRIGGER:-P1D", "END:VALARM", "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n", len(groups)


def easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return datetime(y, month, day).date()


def berlin_holidays(year):
    """Gesetzliche Feiertage Berlin (arbeitsfrei)."""
    e = easter(year)
    d = lambda m, t: datetime(year, m, t).date()
    return [
        (d(1, 1), "Neujahr"), (d(3, 8), "Frauentag"),
        (e - timedelta(days=2), "Karfreitag"), (e + timedelta(days=1), "Ostermontag"),
        (d(5, 1), "Tag der Arbeit"), (e + timedelta(days=39), "Christi Himmelfahrt"),
        (e + timedelta(days=50), "Pfingstmontag"), (d(10, 3), "Tag der Deutschen Einheit"),
        (d(12, 25), "1. Weihnachtstag"), (d(12, 26), "2. Weihnachtstag"),
    ]


def build_berlin():
    now = fmt(datetime.now(timezone.utc))
    this_year = datetime.now(BERLIN).year
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ff-news-calendar//DE",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Feiertage Berlin",
        "X-PUBLISHED-TTL:PT24H", "REFRESH-INTERVAL;VALUE=DURATION:PT24H",
    ]
    for y in range(this_year - 1, this_year + 3):
        for day, name in berlin_holidays(y):
            lines += [
                "BEGIN:VEVENT", f"UID:berlin-{day:%Y%m%d}@ff-news-calendar", f"DTSTAMP:{now}",
                f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
                f"SUMMARY:{esc(name)}", "TRANSP:TRANSPARENT", "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else None
    events = json.loads(Path(src).read_text()) if src else fetch(FEED_URL)
    ics, n = build(events)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(ics, encoding="utf-8", newline="")
    print(f"{n} Sperrfenster geschrieben nach {OUT_FILE}")
    (OUT_FILE.parent / "berlin.ics").write_text(build_berlin(), encoding="utf-8", newline="")


if __name__ == "__main__":
    main()
