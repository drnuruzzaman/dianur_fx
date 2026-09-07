#!/usr/bin/env python
"""
Run every alert job in one foreground process, instead of Windows Task Scheduler.

    python tools/alerts_daemon.py            run everything, Ctrl-C to stop
    python tools/alerts_daemon.py --list     show the schedule and exit
    python tools/alerts_daemon.py --once     run each timed job once, no bot

WHAT THE SCHEDULER WAS ACTUALLY DOING, because it was doing two different jobs
and only one of them is a schedule:

  - For `signal_alert` and `event_alert` it was a TIMER. Both are one-shot
    scripts that poll, send, and exit, and the minute trigger is what made them
    run at all.
  - For `telegram_bot` it was a SUPERVISOR. That script already loops forever on
    `getUpdates`; its task is `IgnoreNew` with no execution time limit, so the
    minute trigger never starts a second copy -- it only restarts the one that
    died. Firing it as a timer is what produced the HTTP 409 Conflict during
    development: two pollers, one bot token.

This file does both, so neither is left to the scheduler.

CHILD PROCESSES, NOT IMPORTS. Each job runs exactly the command the scheduled
task ran, through `sys.executable`. That is deliberate and worth the process
spawn: the jobs keep their own argument parsing, their own exit codes and their
own crash isolation, so nothing here can change what an alert does or silence
one by holding a stale module. A traceback in `event_alert` kills that child and
the next tick starts a new one; it cannot take the bot down with it.

WHAT YOU LOSE WITHOUT THE SCHEDULER, stated plainly rather than discovered
later: this process must be running. It does not start at logon by itself, it
does not survive a reboot, and it stops when you close its window or log out.
The loop is hardened against everything below that -- a job that raises, exits
non-zero, hangs past its interval, or dies repeatedly -- but it cannot restart
the interpreter it is running in. See the README for a Startup-folder shortcut,
which covers logon without going back to Task Scheduler.

NOT A TRADE INSTRUCTION, and this changes nothing about that. It is the same
read-only polling the scheduled tasks did, on the same cadence.
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The timed jobs, mirroring configs/Scheduler/*.xml exactly. `every` is
#: seconds; the arguments are copied from each task's <Arguments>, so a change
#: there is a change here and the two are meant to be read side by side.
JOBS = [
    {'name': 'signal_alert', 'every': 60,
     'args': ['tools/signal_alert.py']},
    {'name': 'event_alert', 'every': 60,
     'args': ['tools/event_alert.py', '--lead', '10', '--local']},
    {'name': 'calendar', 'every': 30 * 60,
     'args': ['tools/refresh_calendar.py']},
]

#: The bot is not on this list because it is not timed -- it is kept alive.
BOT = ['tools/telegram_bot.py']

#: A child that outlives its own interval is not killed, it is SKIPPED. A
#: `signal_alert` waiting on a slow bridge (its own poll timeout is 90s, longer
#: than the 60s tick) is doing exactly what it should; starting a second one
#: would double every message it is about to send, and the dedupe ledger is
#: written only after a successful send, so the copies would not be caught.
#: Killing it instead would abandon a poll that was about to succeed.

#: How long to wait before restarting a bot that exited. Immediate restart on a
#: bot that fails instantly -- a revoked token, no network -- is a spin loop
#: that fills the log and hammers Telegram.
BOT_BACKOFF = [5, 15, 30, 60, 120]


def stamp():
    return datetime.now(timezone.utc).astimezone().strftime('%H:%M:%S')


def say(msg):
    print('%s  %s' % (stamp(), msg), flush=True)


def spawn(args):
    """Start one job. Children INHERIT stdout/stderr rather than getting pipes.

    A pipe nobody drains fills its buffer and blocks the child forever, which
    for a supervisor is the worst failure mode available: the job appears to be
    running and never finishes, so its slot is never free again. Inheriting
    costs interleaved output and is worth it.
    """
    return subprocess.Popen([sys.executable] + args, cwd=ROOT)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--list', action='store_true',
                    help='print the schedule and exit')
    ap.add_argument('--once', action='store_true',
                    help='run each timed job once and exit; does not start the bot')
    ap.add_argument('--no-bot', action='store_true',
                    help='run the timed jobs only, leaving the bot to something else')
    args = ap.parse_args()

    if args.list:
        print('timed jobs')
        for j in JOBS:
            print('  every %4ds  %s' % (j['every'], ' '.join(j['args'])))
        print('kept alive')
        print('  %s' % ' '.join(BOT))
        return 0

    if args.once:
        rc = 0
        for j in JOBS:
            say('run %s' % j['name'])
            rc = spawn(j['args']).wait() or rc
        return rc

    say('alerts daemon up — %d timed job(s)%s. Ctrl-C to stop.'
        % (len(JOBS), '' if args.no_bot else ' plus the bot'))

    # Every job is due immediately on the first pass, so a restart sends
    # anything that came due while the daemon was down rather than waiting out
    # a full interval first.
    for j in JOBS:
        j['next'] = 0.0
        j['proc'] = None
        j['skipped'] = 0

    bot = None
    bot_fails = 0
    bot_retry_at = 0.0
    bot_started = 0.0

    try:
        while True:
            now = time.monotonic()

            for j in JOBS:
                if j['proc'] is not None:
                    if j['proc'].poll() is None:
                        continue                       # still running
                    rc = j['proc'].returncode
                    if rc != 0:
                        # Reported, not fatal. A non-zero exit is usually one
                        # unreachable cell or a missing token, and the next tick
                        # is the retry -- the jobs are built to be re-run.
                        say('%s exited %d' % (j['name'], rc))
                    j['proc'] = None

                if now < j['next']:
                    continue
                if j['proc'] is not None:
                    continue
                j['next'] = now + j['every']
                j['proc'] = spawn(j['args'])

            # A job still running when its turn comes round again keeps its
            # slot. This loop runs once a SECOND, so the counter is seconds
            # overdue, not ticks missed -- said that way in the message, and
            # reported at three widening points so a slow bridge does not
            # become a wall of identical lines.
            for j in JOBS:
                if j['proc'] is not None and now >= j['next']:
                    j['skipped'] += 1
                    if j['skipped'] in (1, 30, 300):
                        say('%s still running, %ds past due — not starting a second'
                            % (j['name'], j['skipped']))
                elif j['proc'] is None:
                    j['skipped'] = 0

            if not args.no_bot:
                if bot is not None and bot.poll() is not None:
                    say('bot exited %d' % bot.returncode)
                    bot = None
                    bot_retry_at = now + BOT_BACKOFF[min(bot_fails, len(BOT_BACKOFF) - 1)]
                    bot_fails += 1
                if bot is None and now >= bot_retry_at:
                    say('starting bot')
                    bot = spawn(BOT)
                    # A bot that stays up for a while has recovered; the next
                    # failure should wait 5s again, not 120.
                    bot_started = now
                elif bot is not None and bot_fails and now - bot_started > 300:
                    bot_fails = 0

            time.sleep(1)

    except KeyboardInterrupt:
        say('stopping…')
        for child in [j['proc'] for j in JOBS] + [bot]:
            if child is not None and child.poll() is None:
                child.terminate()
        # Give them a moment to go quietly; a signal_alert mid-send should be
        # allowed to finish writing its state file, or it will send twice.
        deadline = time.monotonic() + 5
        for child in [j['proc'] for j in JOBS] + [bot]:
            if child is None:
                continue
            while child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            if child.poll() is None:
                child.kill()
        say('stopped')
        return 0


if __name__ == '__main__':
    sys.exit(main())
