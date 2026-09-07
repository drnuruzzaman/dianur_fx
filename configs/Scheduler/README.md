# configs/Scheduler

The weekly job that keeps the macro release marks current on every chart.

Two tasks live here: a weekly one that keeps the calendar current, and a
per-minute one that alerts before a release.

| file | what it is |
|---|---|
| `calendar_refresh.xml` | the weekly schedule — **edit this**, then re-run `install.cmd` |
| `install.cmd [HH:MM]` | registers the refresh task (default Monday 08:00) |
| `uninstall.cmd` | removes it; fetched data is untouched |
| `run-now.cmd` | refresh immediately, same script the task runs |
| `event_alert.xml` | the release-alert schedule |
| `event_alert_install.cmd [MINUTES]` | registers it (default 10 minutes of warning) |
| `event_alert_uninstall.cmd` | removes it |
| `event_alert_test.cmd` | sends one message now, to prove the wiring |

## The release alert

`tools/event_alert.py` sends a Telegram message a set number of minutes before a
macro release, reading the same `data/calendar/history.json` the charts mark
from — so an alert and a chart mark cannot disagree about when something is.

    configs/Scheduler/event_alert_install.cmd        10 minutes of warning
    configs/Scheduler/event_alert_install.cmd 30     30 minutes

BEFORE IT WILL SEND ANYTHING, put both of these in `configs/secrets.env`:

    TELEGRAM_BOT_TOKEN=...     BotFather -> /newbot
    TELEGRAM_CHAT_ID=...       message the bot, then read
                               api.telegram.org/bot<TOKEN>/getUpdates

Without both it prints what it WOULD have sent and exits 2. It never sends to a
default anywhere. `python tools/event_alert.py --dry-run --lead 5000` shows the
next few messages without needing credentials at all.

WHY IT POLLS EVERY MINUTE and that is not wasteful: the tool records every alert
it sends in `data/calendar/alerted.json`, keyed by the event's timestamp and
kind, so a release is announced exactly once no matter how often the task runs.
A poll that finds nothing due does nothing. The alternative — a task scheduled
per event — would need re-registering every time the calendar refreshes.

IT WILL NOT FIRE ON THE PAST. A laptop that was asleep wakes up and sends
nothing for releases that already landed. A missed alert is missed; a late one
reads as a live warning and is worse than useless.

## What runs

`tools/refresh_calendar.py`, which calls two importers in order:

1. **xoomar** (`--replace`) — FOMC decisions, US releases with real timestamps,
   ECB/BOE/BOJ rate changes. Primary.
2. **FRED** (merge) — fills 2016–2019, which xoomar's calendar does not reach.

The order matters: reversed, FRED's day-only timestamps would win the dedupe
over xoomar's minute-accurate ones for the years both cover.

Output is `data/calendar/history.json`, read by the live chart, the Elliott
replay and the strategy replay. A run appends to `logs/calendar_refresh.log`.

## Why the XML rather than schtasks flags

Three settings a `/SC WEEKLY` command line cannot express, and all three matter
for a job that runs at 08:00 on a laptop:

- `DisallowStartIfOnBatteries=false` — the Windows default refuses to start on
  battery, which alone would stop this ever running.
- `StartWhenAvailable=true` — catches up a run missed because the machine was
  asleep, instead of skipping the week.
- `RestartOnFailure` — three retries at 30 minutes, for a morning with no
  network yet.

Editing the task in the Windows UI instead of this file leaves the checked-in
XML lying about what is scheduled.

## Checking on it

```
schtasks /Query /TN "DiaNurFx calendar refresh" /V /FO LIST
tail logs/calendar_refresh.log
```

A failed run restores the previous calendar and exits non-zero, so a bad Monday
leaves last week's marks on screen rather than an empty chart.

## The command bot

`tools/telegram_bot.py` answers `/status`, `/profit` and `/news`, and is
registered by `telegram_bot_install.cmd`. It long-polls, so replies land in
about a second. `--check` previews every reply without sending; `--discover`
prints the chat id of anyone who writes and answers nobody.

`TELEGRAM_CHAT_ID` is a comma-separated list, so one bot serves a private
chat and a group. Group ids are negative. Adding a group grants `/profit` to
everyone in it, now and later.
