#!/usr/bin/env python
"""
A revision record for the bar archive: what was on disk, and when.

    python tools/bars_manifest.py --write        # snapshot the archive
    python tools/bars_manifest.py --check        # diff it against the last snapshot
    python tools/bars_manifest.py --check --strict   # exit 1 if anything drifted

WHY THIS EXISTS. A trendline result recorded -1.40 pp on 2011-2020; the same
harness reads -0.40 today. Most of that gap was traced to the universe being
different from the one the prose claimed, but a residual remains that sample
growth does not cover, and it cannot be closed because NOTHING RECORDED WHAT THE
ARCHIVE HELD AT THE TIME. `data/manifest.json` is a probe of what MetaTrader
reports, not a fingerprint of the local files, so an archive that is repaired,
backfilled or re-downloaded leaves no trace. Every frozen number in this project
is a number about data, and none of them can be re-derived without knowing which
data.

WHAT A ROW IS. One line per file: rows, first and last timestamp, the MODAL GAP
between consecutive bars, and a sha256 of the compressed bytes. The hash answers
"did this file change"; the gap answers "is it the timeframe its folder claims".

THE GAP CHECK IS NOT DECORATION. `data/bars/XAUUSD.a/{15m,1h,4h}/2010.csv.gz`
were byte-identical DAILY files -- three folders, one series, 86400s apart --
and every gold study before 2018 silently ran on dailies for it. That went
unnoticed for the life of the project because nothing ever compared a file's
spacing to its folder's name. `--check` now fails on it.

DELIBERATELY NOT A LOCK FILE. This does not stop the archive changing; MT5
backfills are legitimate and frequent. It makes a change VISIBLE and dateable,
so a result can be tied to the archive it was measured on.
"""

import argparse
import collections
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BARS = os.path.join(ROOT, 'data', 'bars')
OUT_DIR = os.path.join(ROOT, 'data', 'bars_revisions')
CURRENT = os.path.join(ROOT, 'data', 'bars_revision.json')

#: Seconds between bars, by folder name. A file whose MODAL gap is not this is
#: not the timeframe it is filed under -- see the gold note in the docstring.
TF_SECONDS = {
    '1m': 60, '5m': 300, '15m': 900, '30m': 1800,
    '1h': 3600, '4h': 14400, '1d': 86400, '1w': 604800,
}

#: How long a run of another timeframe's spacing has to be before it is a
#: mislabelled section rather than a data hole. The known-bad 15m/2017 opened
#: with 2,591 consecutive hourly bars; genuine holes in this archive run to a
#: handful. 200 sits far above the holes and far below the mislabelling.
MIXED_RUN = 200


def longest_wrong_run(gaps, want):
    """Longest CONTIGUOUS run of a single non-nominal gap.

    THE SHARE OF WRONG GAPS DOES NOT SEPARATE THE TWO FAILURE MODES. A holiday
    leaves a legitimate hole, and 86400s in a 4h file is both "a coarser
    timeframe" and "six missing bars" -- 4h/2016 scores 2% either way. What a
    MISLABELLED STRETCH has that a hole does not is length: `15m/2017` opened
    with 2,591 consecutive hourly bars before the real 15m data began.

    LENGTH ALONE IS NOT ENOUGH EITHER, and the first version of this got it
    wrong. The 1m files carry runs of 259-406 bars spaced 120s in 2010 -- thin
    liquidity, every other minute simply absent. That is SPARSITY, and no
    threshold on run length separates it from a mislabelled section, because
    both are long and both are exact multiples of the nominal.

    What separates them is that a mislabelled section came FROM ANOTHER
    TIMEFRAME, and the other timeframes have known spacings. 3600s inside a 15m
    file is the 1h series; 120s inside a 1m file is not any series this broker
    serves. So only runs whose gap is itself a TF_SECONDS value count.
    """
    nominals = set(TF_SECONDS.values())
    best, best_gap, run, prev = 0, None, 0, None
    for g in gaps:
        if g == want or g not in nominals:
            run, prev = 0, None
            continue
        run = run + 1 if g == prev else 1
        prev = g
        if run > best:
            best, best_gap = run, g
    return best, best_gap


def scan_file(path):
    """rows, first/last ts, modal gap, longest wrong run, and hash."""
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    ts = []
    with gzip.open(path, 'rt') as fh:
        fh.readline()                       # header
        for line in fh:
            if line.strip():
                ts.append(int(line.split(',', 1)[0]))
    seq = [b - a for a, b in zip(ts, ts[1:])]
    gaps = collections.Counter(seq)
    want = TF_SECONDS.get(os.path.basename(os.path.dirname(path)))
    run, run_gap = longest_wrong_run(seq, want) if want else (0, None)
    return {
        'rows': len(ts),
        'first_ts': ts[0] if ts else None,
        'last_ts': ts[-1] if ts else None,
        # The mode, not the mean: weekends and holidays put large gaps in every
        # series, and a mean would report 15m gold as roughly hourly.
        'modal_gap': gaps.most_common(1)[0][0] if gaps else None,
        'longest_wrong_run': run,
        'longest_wrong_gap': run_gap,
        'sha256': h.hexdigest(),
    }


def scan(verbose=False):
    files = {}
    problems = []
    for sym in sorted(os.listdir(BARS)):
        sym_dir = os.path.join(BARS, sym)
        if not os.path.isdir(sym_dir) or sym.startswith('_'):
            continue
        for tf in sorted(os.listdir(sym_dir)):
            tf_dir = os.path.join(sym_dir, tf)
            if not os.path.isdir(tf_dir):
                continue
            for name in sorted(os.listdir(tf_dir)):
                if not name.endswith('.csv.gz'):
                    continue
                key = '%s/%s/%s' % (sym, tf, name)
                rec = scan_file(os.path.join(tf_dir, name))
                files[key] = rec
                want = TF_SECONDS.get(tf)
                got = rec['modal_gap']
                if want and got and got != want:
                    problems.append('%s: modal gap %ds, folder says %ds'
                                    % (key, got, want))
                # A long contiguous stretch of one wrong gap is a mislabelled
                # SECTION rather than a mislabelled file -- the modal check
                # cannot see it, because the rest of the file is correct.
                elif rec['longest_wrong_run'] >= MIXED_RUN:
                    problems.append(
                        '%s: %d consecutive bars at %ds inside a %ds file'
                        % (key, rec['longest_wrong_run'],
                           rec['longest_wrong_gap'], want))
                if verbose:
                    print('  %-34s rows=%-7d gap=%s' % (key, rec['rows'], got))
    return files, problems


def latest_record():
    if not os.path.isdir(OUT_DIR):
        return None, None
    names = sorted(f for f in os.listdir(OUT_DIR) if f.endswith('.json'))
    if not names:
        return None, None
    path = os.path.join(OUT_DIR, names[-1])
    with open(path, encoding='utf-8') as fh:
        return names[-1], json.load(fh)


def cmd_write(args):
    files, problems = scan(args.verbose)
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')
    rec = {
        'written_utc': datetime.now(timezone.utc).isoformat(),
        'note': args.note or '',
        'file_count': len(files),
        'total_rows': sum(f['rows'] for f in files.values()),
        'integrity_problems': problems,
        'files': files,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, stamp + '.json')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
    with open(CURRENT, 'w', encoding='utf-8') as fh:
        json.dump({'latest': stamp + '.json', 'written_utc': rec['written_utc'],
                   'file_count': rec['file_count'],
                   'total_rows': rec['total_rows'],
                   'integrity_problems': problems}, fh, indent=1)
    print('wrote %s' % os.path.relpath(path, ROOT))
    print('  %d files, %d rows' % (len(files), rec['total_rows']))
    if problems:
        print('  %d INTEGRITY PROBLEMS:' % len(problems))
        for p in problems:
            print('    ' + p)
    else:
        print('  no integrity problems: every file matches its folder')
    return 0


def cmd_check(args):
    name, prev = latest_record()
    if not prev:
        print('no revision record yet -- run --write first', file=sys.stderr)
        return 2
    files, problems = scan(args.verbose)
    old, new = prev['files'], files
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new)
                     if old[k]['sha256'] != new[k]['sha256'])
    print('against %s (%s)' % (name, prev['written_utc'][:19]))
    print('  added   %d' % len(added))
    print('  removed %d' % len(removed))
    print('  changed %d' % len(changed))
    for k in added[:20]:
        print('    + %s  rows=%d' % (k, new[k]['rows']))
    for k in removed[:20]:
        print('    - %s  rows=%d' % (k, old[k]['rows']))
    for k in changed[:20]:
        print('    ~ %s  rows %d -> %d' % (k, old[k]['rows'], new[k]['rows']))
    for lst, label in ((added, 'added'), (removed, 'removed'), (changed, 'changed')):
        if len(lst) > 20:
            print('    ... and %d more %s' % (len(lst) - 20, label))
    if problems:
        print('  INTEGRITY PROBLEMS (%d):' % len(problems))
        for p in problems:
            print('    ' + p)
    drift = bool(added or removed or changed or problems)
    if not drift:
        print('  archive is byte-identical to the record')
    return 1 if (drift and args.strict) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--write', action='store_true', help='snapshot the archive')
    ap.add_argument('--check', action='store_true', help='diff against the last snapshot')
    ap.add_argument('--strict', action='store_true', help='--check exits 1 on any drift')
    ap.add_argument('--note', default=None, help='why this snapshot was taken')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()
    if args.write:
        return cmd_write(args)
    if args.check:
        return cmd_check(args)
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
