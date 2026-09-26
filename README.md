# Australian Cricket calendar

One subscribable iCalendar feed with:

- Australia men's and women's international matches (Tests, ODIs, T20Is), home and away
- Every BBL and WBBL match, including finals

**Subscribe:** `https://phickman.github.io/cricket-calendar/cricket.ics`

In Outlook.com: Calendar → Add calendar → Subscribe from web → paste the link above.

## How it works

`build_calendar.py` (standard-library Python, no dependencies) pulls fixtures from:

1. **Cricket Australia** (`apiv2.cricket.com.au`), the primary source for BBL/WBBL and
   Australia internationals.
2. **ICC** (`assets-icc.sportz.io`), which fills gaps (mainly women's tours abroad).
   An ICC match is only added if Cricket Australia has no match for the same
   Australia side (men/women) on any of the same days, so duplicates and
   conflicting listings are dropped.

Tests appear as one event per day. Event notes include the competition, TV
channels and, once played, the result. Matches from the last 60 days are kept.

A GitHub Action rebuilds and publishes the feed to GitHub Pages daily. If either
source fails, the run fails and the previous calendar stays published.

## Changing how often it updates

Edit `.github/workflows/update-calendar.yml`:

- `cron:` sets when the calendar is rebuilt (UTC). The default is `"0 17 * * *"`, which is daily.
- `REFRESH_HOURS` tells calendar apps how often to re-check. Keep it in line with the cron.

To update immediately, open the **Actions** tab → *Update calendar* → **Run workflow**.

Outlook.com decides for itself how often to re-fetch subscribed calendars
(typically every few hours, sometimes longer), whatever these settings say.

## Running locally

```bash
python3 build_calendar.py   # writes site/cricket.ics
```
