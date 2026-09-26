#!/usr/bin/env python3
"""Build a single iCalendar feed of Australia men's and women's international
matches plus every BBL and WBBL match.

Sources (both are the JSON services behind the official websites):
  * Cricket Australia (apiv2.cricket.com.au) - primary source for BBL/WBBL and
    Australia internationals. Most accurate for home series.
  * ICC (assets-icc.sportz.io) - fills gaps, mainly Australia women's tours
    abroad that Cricket Australia doesn't list. An ICC match is only added if
    Cricket Australia has no match for the same Australia team (men/women) on
    any of the same days, so conflicting or duplicate listings are dropped.

Any network or parse failure exits non-zero so the GitHub Action fails and the
previously published calendar stays in place.
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

OUTPUT = os.environ.get("OUTPUT", "site/cricket.ics")
REFRESH_HOURS = int(os.environ.get("REFRESH_HOURS", "24"))
LOOKBACK_DAYS = 60     # keep recently finished matches (with results) in the calendar
LOOKAHEAD_DAYS = 550   # ~18 months ahead

USER_AGENT = "cricket-calendar/1.0 (+https://github.com/phickman/cricket-calendar)"

CA_URL = "https://apiv2.cricket.com.au/web/fixtures/yearfilter"
CA_LIMIT = 999  # the API rejects limits of 1000 or more
ICC_URL = "https://assets-icc.sportz.io/cricket/v1/schedule"
ICC_CLIENT_ID = "tPZJbRgIub3Vua93/DWtyQ=="  # public id used by icc-cricket.com
ICC_LEAGUES = {"1,9": False, "10": True}   # league ids -> is women's

# Cricket Australia game type ids for senior internationals.
CA_INTL_TYPES = {1: "Test", 2: "ODI", 3: "T20I", 36: "Test", 37: "ODI", 38: "T20I"}
CA_AUS_TEAMS = {"Australia Men", "Australia Women"}

TEST_DAY_HOURS = 7


def fetch_json(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------- Cricket Australia

def ca_fixtures(start, **extra):
    fixtures = []
    for completed in ("false", "true"):
        data = fetch_json(CA_URL, {
            "isCompleted": completed,
            "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "limit": CA_LIMIT,
            "jsconfig": "eccn:true",
            "format": "json",
            **extra,
        })
        if data.get("responseError") or "fixtures" not in data:
            raise RuntimeError(f"Cricket Australia API error: {data.get('responseError')}")
        if len(data["fixtures"]) >= CA_LIMIT:
            raise RuntimeError("Cricket Australia returned a full page - results may be truncated")
        fixtures += data["fixtures"]
    return {f["id"]: f for f in fixtures}.values()


def ca_description(f):
    lines = [f["competition"]["name"]]
    if f.get("resultText") and f.get("isCompleted"):
        lines.append("Result: " + f["resultText"])
    channels = [c["channelName"] for c in sorted(f.get("channels") or [], key=lambda c: c.get("order", 0))]
    if channels:
        lines.append("Watch: " + ", ".join(channels))
    return "\n".join(lines)


def ca_location(f):
    v = f.get("venue") or {}
    return ", ".join(p for p in (v.get("name"), v.get("location")) if p)


def ca_bbl_events(start, now, until):
    events = []
    for f in ca_fixtures(start, isBigBash="true"):
        begin = parse_iso(f["startDateTime"])
        if not (start <= begin <= until):
            continue
        comp = f["competition"]["name"]
        tag = "WBBL" if f.get("isWomensMatch") else "BBL"
        home = f["homeTeam"]["name"].removesuffix(" Men").removesuffix(" Women")
        away = f["awayTeam"]["name"].removesuffix(" Men").removesuffix(" Women")
        events.append({
            "uid": f"ca-{f['id']}",
            "summary": f"🏏 {tag}: {home} v {away} – {f['name']}",
            "start": begin,
            "end": parse_iso(f["endDateTime"]),
            "location": ca_location(f),
            "description": ca_description(f),
        })
    return events


def ca_intl_matches(start, until):
    """Returns normalised Australia international matches from Cricket Australia."""
    matches = []
    for f in ca_fixtures(start, isBigBash="false"):
        if f.get("gameTypeId") not in CA_INTL_TYPES:
            continue
        teams = {f["homeTeam"]["name"], f["awayTeam"]["name"]}
        if not teams & CA_AUS_TEAMS:
            continue
        begin = parse_iso(f["startDateTime"])
        if not (start <= begin <= until):
            continue
        women = bool(f.get("isWomensMatch"))
        matches.append({
            "uid": f"ca-{f['id']}",
            "women": women,
            "home": team_label(f["homeTeam"]["name"].removesuffix(" Men").removesuffix(" Women"), women),
            "away": team_label(f["awayTeam"]["name"].removesuffix(" Men").removesuffix(" Women"), women),
            "name": f["name"],
            "format": CA_INTL_TYPES[f["gameTypeId"]],
            "start": begin,
            "end": parse_iso(f["endDateTime"]),
            "days": max(1, int(f.get("numberOfDays") or 1)),
            "location": ca_location(f),
            "description": ca_description(f),
        })
    return matches


# ---------------------------------------------------------------- ICC

def icc_datetime(date_str, time_str):
    return datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %H:%M").replace(tzinfo=timezone.utc)


def icc_intl_matches(start, until):
    matches = []
    for leagues, women in ICC_LEAGUES.items():
        page = 1
        while True:
            data = fetch_json(ICC_URL, {
                "client_id": ICC_CLIENT_ID,
                "feed_format": "json",
                "lang": "en",
                "is_deleted": "false",
                "is_upcoming": "true",
                "is_live": "true",
                "is_recent": "true",
                "from_date": start.strftime("%Y%m%d"),
                "to_date": until.strftime("%Y%m%d"),
                "league_ids": leagues,
                "pagination": "true",
                "page_number": page,
                "page_size": 500,
                "timezone": "0000",
            })
            if data.get("meta", {}).get("app_status_code") != 1:
                raise RuntimeError(f"ICC API error: {data.get('meta')}")
            batch = data["data"]["matches"]
            for m in batch:
                if "Australia" not in (m["teama"], m["teamb"]):
                    continue
                comp = m["comp_type"].lower()
                fmt = "Test" if comp.startswith("test") else "ODI" if comp.startswith("odi") else "T20I" if comp.startswith("t20 international") else None
                if fmt is None:
                    continue
                begin = icc_datetime(m["match_date_gmt"], m["match_time_gmt"])
                end = icc_datetime(m["end_match_date_gmt"], m["end_match_time_gmt"])
                days = (end.date() - begin.date()).days + 1 if fmt == "Test" else 1
                desc = [m["series_name"]]
                if m.get("match_result"):
                    desc.append("Result: " + m["match_result"])
                matches.append({
                    "uid": f"icc-{m['match_id']}",
                    "women": women,
                    "home": team_label(m["teama"], women),
                    "away": team_label(m["teamb"], women),
                    "name": m.get("match_number") or fmt,
                    "format": fmt,
                    "start": begin,
                    "end": end,
                    "days": max(1, days),
                    "location": m.get("venue", ""),
                    "description": "\n".join(desc),
                })
            if len(batch) < 500:
                break
            page += 1
    return matches


# ---------------------------------------------------------------- merge + output

def team_label(name, women):
    return f"{name} Women" if women else name


def match_days(m):
    first = m["start"].date()
    return {first + timedelta(days=i) for i in range(m["days"])}


def intl_events(matches):
    events = []
    for m in matches:
        title = f"🏏 {m['home']} v {m['away']} – {m['name']}"
        if m["days"] == 1:
            events.append({**m, "summary": title})
            continue
        for day in range(m["days"]):
            begin = m["start"] + timedelta(days=day)
            events.append({
                **m,
                "uid": f"{m['uid']}-d{day + 1}",
                "summary": f"{title}, Day {day + 1}",
                "start": begin,
                "end": begin + timedelta(hours=TEST_DAY_HOURS),
            })
    return events


def ics_escape(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    """Fold to 75-octet lines per RFC 5545 without splitting UTF-8 characters."""
    if len(line.encode("utf-8")) <= 75:
        return line
    out, chunk = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(chunk) + len(b) > (75 if not out else 74):
            out.append(chunk.decode("utf-8"))
            chunk = b""
        chunk += b
    out.append(chunk.decode("utf-8"))
    return "\r\n ".join(out)


def ics_time(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_ics(events, now):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//phickman//cricket-calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Australian Cricket",
        "X-WR-CALDESC:Australia men's & women's internationals\\, BBL and WBBL",
        f"REFRESH-INTERVAL;VALUE=DURATION:PT{REFRESH_HOURS}H",
        f"X-PUBLISHED-TTL:PT{REFRESH_HOURS}H",
    ]
    for e in sorted(events, key=lambda e: e["start"]):
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}@phickman-cricket-calendar",
            f"DTSTAMP:{ics_time(now)}",
            f"DTSTART:{ics_time(e['start'])}",
            f"DTEND:{ics_time(e['end'])}",
            f"SUMMARY:{ics_escape(e['summary'])}",
            f"LOCATION:{ics_escape(e['location'])}",
            f"DESCRIPTION:{ics_escape(e['description'])}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    os.makedirs(os.path.dirname(OUTPUT) or ".", exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8", newline="") as fh:
        fh.write("\r\n".join(fold(l) for l in lines) + "\r\n")


def main():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=LOOKBACK_DAYS)
    until = now + timedelta(days=LOOKAHEAD_DAYS)

    bbl = ca_bbl_events(start, now, until)
    ca_intl = ca_intl_matches(start, until)
    icc_intl = icc_intl_matches(start, until)

    taken = {}  # (women, day) -> CA match occupying that day
    for m in ca_intl:
        for d in match_days(m):
            taken[(m["women"], d)] = m
    extra = [m for m in icc_intl if not any((m["women"], d) in taken for d in match_days(m))]

    events = bbl + intl_events(ca_intl + extra)
    write_ics(events, now)

    print(f"BBL/WBBL events:            {len(bbl)}")
    print(f"CA internationals:          {len(ca_intl)}")
    print(f"ICC internationals added:   {len(extra)} (of {len(icc_intl)})")
    for m in sorted(extra, key=lambda m: m["start"]):
        print(f"  + {m['start']:%Y-%m-%d} {m['home']} v {m['away']} – {m['name']}")
    print(f"Total calendar events:      {len(events)} -> {OUTPUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # fail the Action so the last good calendar stays published
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
