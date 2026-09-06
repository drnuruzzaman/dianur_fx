# configs/Scheduler

The weekly job that keeps the macro release marks current on every chart.

| file | what it is |
|---|---|
| `calendar_refresh.xml` | the schedule itself — **edit this**, then re-run `install.cmd` |
| `install.cmd [HH:MM]` | registers the task (default Monday 08:00) |
| `uninstall.cmd` | removes the task; fetched data is untouched |
| `run-now.cmd` | refresh immediately, same script the task runs |

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
