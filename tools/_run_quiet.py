#!/usr/bin/env python
"""
_run_quiet.py — run another tool with no console window and no lost output.

    pythonw tools\\_run_quiet.py tools\\signal_alert.py [args...]

WHY THIS EXISTS. The alert tasks poll every MINUTE, and every poll opened a
console window on the desktop. Task Scheduler has no "hide the window" switch
that works for an interactive task: `<Hidden>` hides the task from the task
LIST, and "run whether the user is logged on or not" hides the window but
demands a stored password, which is a worse trade than a flashing window.

`pythonw.exe` is the switch that actually works -- it is the same interpreter
built as a GUI binary, so Windows never gives it a console. The catch is that
it also throws the output away: with no console, `sys.stdout` is None and the
first `print()` in the tool dies with AttributeError. A silent task that is
also BROKEN is worse than a visible one.

So this wrapper does the one thing pythonw cannot: it points stdout and stderr
at a log file BEFORE handing over. The tools are not modified at all -- they
keep printing exactly as they do from a terminal, and the printing lands in
`logs/<tool>.log` instead of a window that steals focus.

WHY runpy AND NOT subprocess. Spawning a child would put the console back: the
child is a fresh process and, launched from a GUI parent, Windows gives it one.
Running the target IN THIS process under `__main__` keeps the whole thing
console-free and preserves `if __name__ == '__main__'`, argparse and the exit
code, which Task Scheduler reports as Last Result.

THE EXIT CODE IS PRESERVED ON PURPOSE. It is the only signal left once the
window is gone: `schtasks /Query /V` shows Last Result, and the Settings modal
in the app reads it. A wrapper that swallowed a failure would make a dead
alerter look healthy, which is the exact failure this project has already had
once with the bot task.
"""
import io
import os
import runpy
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, 'logs')

#: Rotate at 2 MB, keeping one generation. A per-minute task writing a few
#: lines a run fills maybe a megabyte a month, and an unbounded log on a
#: trading machine is a disk that fills quietly at the worst moment.
MAX_BYTES = 2 * 1024 * 1024


def _rotate(path):
    try:
        if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
            old = path + '.1'
            if os.path.exists(old):
                os.remove(old)
            os.replace(path, old)
    except OSError:
        pass                    # a log that cannot rotate must not stop the run


def main():
    if len(sys.argv) < 2:
        return 2
    target = sys.argv[1]
    if not os.path.isabs(target):
        target = os.path.join(ROOT, target)
    if not os.path.exists(target):
        # Nowhere to print yet -- the exit code is the whole message.
        return 3

    os.makedirs(LOGS, exist_ok=True)
    name = os.path.splitext(os.path.basename(target))[0]
    path = os.path.join(LOGS, name + '.log')
    _rotate(path)

    fh = io.open(path, 'a', encoding='utf-8', errors='replace')
    sys.stdout = fh
    sys.stderr = fh

    # The tool must see the arguments it would have seen, with itself at [0].
    sys.argv = [target] + sys.argv[2:]
    os.chdir(ROOT)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    code = 0
    try:
        runpy.run_path(target, run_name='__main__')
    except SystemExit as exc:           # argparse and `raise SystemExit(main())`
        code = exc.code if isinstance(exc.code, int) else (0 if not exc.code else 1)
    except BaseException:               # noqa: BLE001 -- the log is the only witness
        traceback.print_exc(file=fh)
        code = 1
    finally:
        try:
            fh.flush()
            fh.close()
        except Exception:               # noqa: BLE001
            pass
    return code


if __name__ == '__main__':
    sys.exit(main())
