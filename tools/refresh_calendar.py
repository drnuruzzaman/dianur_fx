#!/usr/bin/env python
"""
Refresh data/calendar/history.json from every source, in order.

    python tools/refresh_calendar.py

Meant to be run unattended -- a Windows scheduled task runs it every Monday at
08:00. Safe to run by hand any time.

THE SCHEDULE LIVES IN configs/Scheduler/calendar_refresh.xml, not in this file
and not only in Windows. `--install-task` registers that XML; editing the task
in the Windows UI instead leaves the checked-in file lying about what runs.

ORDER MATTERS, AND IT IS THE POINT OF HAVING ONE SCRIPT RATHER THAN TWO
COMMANDS.

  1. xoomar   PRIMARY. FOMC decisions and the US releases with real timestamps,
              plus ECB/BOE/BOJ rate changes. Writes with --replace, so a stale
              entry that the source has corrected does not survive forever.
  2. FRED     Fills 2016-2019, which xoomar's calendar does not reach. MERGES,
              so it adds to what step 1 wrote.

Reversing them would let FRED's coarser day-only timestamps win the dedupe over
xoomar's minute-accurate ones for the years both cover.

FAILURE IS LOUD BUT NOT DESTRUCTIVE. The existing file is copied aside first; if
a step fails the copy is restored, so a network outage on a Monday morning
leaves last week's calendar in place rather than an empty chart. The exit code
is non-zero so Task Scheduler records the failure.
"""

import argparse
import datetime
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'calendar', 'history.json')
LOG = os.path.join(ROOT, 'logs', 'calendar_refresh.log')
TASK = 'DiaNurFx calendar refresh'
SCHED = os.path.join(ROOT, 'configs', 'Scheduler')
TEMPLATE = os.path.join(SCHED, 'calendar_refresh.xml')

STEPS = [
    ('xoomar', ['tools/fetch_xoomar_calendar.py', '--replace']),
    ('fred', ['tools/fetch_fred_calendar.py']),
]


def log(line, fh=None):
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msg = '%s  %s' % (stamp, line)
    print(msg)
    if fh:
        fh.write(msg + '\n')
        fh.flush()


def install_task(at='08:00'):
    """
    Register the weekly task FROM THE XML IN configs/Scheduler.

    Built from the checked-in template rather than from schtasks flags, so the
    schedule is a file you can read, diff and edit -- and so the settings that
    flags cannot express survive: run on battery, catch up a missed run, retry
    three times. A `/SC WEEKLY` command line silently accepts Windows' defaults
    for all three, and the battery default alone is enough to stop this ever
    running on a laptop.

    schtasks requires UTF-16 for /XML, so the template is written as UTF-8 for
    diffing and transcoded into a temporary file here.
    """
    if not os.path.exists(TEMPLATE):
        print('missing %s' % TEMPLATE, file=sys.stderr)
        return 2
    hh, _, mm = at.partition(':')
    xml = (open(TEMPLATE, encoding='utf-8').read()
           .replace('__TIME__', '%02d:%02d:00' % (int(hh), int(mm or 0)))
           .replace('__USER__', os.environ.get('USERNAME', ''))
           .replace('__PYTHON__', sys.executable)
           .replace('__SCRIPT__', os.path.join(ROOT, 'tools', 'refresh_calendar.py'))
           .replace('__ROOT__', ROOT))
    tmp = os.path.join(SCHED, '.task.tmp.xml')
    with open(tmp, 'w', encoding='utf-16') as fh:
        fh.write(xml)
    try:
        r = subprocess.run(['schtasks', '/Create', '/F', '/TN', TASK, '/XML', tmp],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        return r.returncode
    finally:
        os.remove(tmp)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--install-task', action='store_true',
                    help='register the Monday 08:00 Windows scheduled task and exit')
    ap.add_argument('--at', default='08:00', help='time for --install-task')
    args = ap.parse_args()

    if args.install_task:
        return install_task(args.at)

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    backup = OUT + '.prev'
    had = os.path.exists(OUT)
    if had:
        shutil.copy2(OUT, backup)

    with open(LOG, 'a', encoding='utf-8') as fh:
        log('refresh starting', fh)
        for name, argv in STEPS:
            r = subprocess.run([sys.executable] + argv, cwd=ROOT,
                               capture_output=True, text=True)
            tail = (r.stdout or r.stderr).strip().splitlines()[-3:]
            for t in tail:
                log('  [%s] %s' % (name, t), fh)
            if r.returncode != 0:
                log('  [%s] FAILED rc=%d' % (name, r.returncode), fh)
                if had:
                    shutil.copy2(backup, OUT)
                    log('  restored the previous calendar', fh)
                log('refresh FAILED', fh)
                return 1

        try:
            import json
            with open(OUT) as f:
                data = json.load(f)
            import collections
            by = collections.Counter(e['kind'] for e in data)
            log('refresh OK: %d events (%s)'
                % (len(data), ', '.join('%s %d' % kv for kv in sorted(by.items()))), fh)
        except Exception as exc:
            log('refresh wrote a file that will not parse: %s' % exc, fh)
            if had:
                shutil.copy2(backup, OUT)
                log('  restored the previous calendar', fh)
            return 1

    if os.path.exists(backup):
        os.remove(backup)
    return 0


if __name__ == '__main__':
    sys.exit(main())
