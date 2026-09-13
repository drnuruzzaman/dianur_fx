#!/usr/bin/env python
"""
serve.py — static dev server that does not cache, plus the workspace file.

`python -m http.server` honours If-Modified-Since and happily serves a 304, so an
edited .js file can keep running from the browser cache. That produces confusing
symptoms (a function that plainly exists on disk reporting as undefined), so this
server sends no-store on everything instead.

    python serve.py            # http://127.0.0.1:5173
    python serve.py 8080

WORKSPACE PERSISTENCE. localStorage is scoped to the browser profile: clear the
site data, switch browser, or open the app from a different host and every
setting is gone. The workspace belongs to the PROJECT, so it lives in the
project folder:

    GET  /workspace   ->  configs/workspace.json, or {} when there is none
    PUT  /workspace   <-  {set: {...}, del: [...]}, merged into the file
    GET  /alerts      ->  configs/alerts.json, or {} when there is none
    PUT  /alerts      <-  the whole object, REPLACED not merged
    GET  /notify/status -> which alert channels have credentials (booleans)
    GET  /notify/resolve -> what a pasted chat link/@name becomes; sends nothing
    POST /alerts/test <-  {destination, text?}, sends ONE real test message

WHY ONE MERGES AND THE OTHER DOES NOT. The workspace is a scatter of
independent keys and a client must never assert what it lacks -- a browser with
cleared storage would otherwise overwrite the durable copy with its own
emptiness. `/alerts` is a short LIST the user edits AS a list, where removing a
row IS the edit, so a merge would make deletion impossible without inventing a
delete protocol for array elements. Its safety is a shape check instead: a body
with no `signals.watch` array is refused, and the previous file is kept as
`.prev` before every replace.

REPLAY RECORDINGS. A browser download would put the file wherever the browser
puts downloads, which is not the project, and the point of a recorded replay is
that it sits beside the runs it will be compared with:

    POST /record?name=x.mp4   <-  raw bytes (video), written as-is
    POST /record              <-  {name, payload}, written as JSON
    GET  /records             ->  the list of recordings on disk

Both land in data/replays/. The JSON body is the belief sidecar; anything with a
non-JSON content type is written byte for byte, which is what the video needs --
re-encoding a container the browser just produced would be work with no purpose.

The name is sanitised to one path segment. It is a dev server on 127.0.0.1, but
a write endpoint that accepts `../` is a write endpoint that can leave the
folder it was pointed at, and there is no reason to allow it.

The PUT MERGES. A client sends what it has and may explicitly delete, but
cannot assert what it lacks -- otherwise a browser with cleared storage
overwrites the durable copy with its own emptiness.

Written via a temp file and os.replace so a crash mid-write cannot leave a
half-parsed JSON file that would wipe the settings it was saving.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
# configs/, not data/: this is project configuration, and data/ is for things
# the app GENERATES (replays, downloads). Secrets never reach it -- see
# LOCAL_ONLY in util.js, which keeps the PIN and its recovery password in
# browser storage only. That matters more here than it did under data/,
# because configs/ is tracked by git and data/ is not.
WORKSPACE = os.path.join(ROOT, 'configs', 'workspace.json')
ALERTS = os.path.join(ROOT, 'configs', 'alerts.json')
REPLAYS = os.path.join(ROOT, 'data', 'replays')
NEWS = os.path.join(ROOT, 'data', 'news', 'quantgist.json')
NEWS_FETCH = os.path.join(ROOT, 'tools', 'fetch_quantgist_news.py')

#: One news fetch at a time. The tool rewrites `quantgist.json` with a temp file
#: and a replace, so two of them racing cannot corrupt it -- but they can burn
#: two API calls for one result, and a button that fires on every impatient
#: click would do exactly that.
_NEWS_JOB = {'running': False, 'started': 0.0, 'last': None}
# Soundtracks a replay recording can be muxed with. Files go in BY HAND -- there
# is no upload endpoint and there is not going to be one. Whatever sits here
# ends up inside a video that gets shared, so putting a track in this folder is
# an assertion that you have the right to distribute it, and that assertion has
# to be a deliberate act rather than a side effect of clicking something.
# The replay soundtrack lives here and is served by the static handler like any
# other file. There is no listing endpoint and no upload endpoint: the recorders
# fetch one fixed name, and changing the music means replacing that file.
AUDIO = os.path.join(ROOT, 'data', 'audio')
MAX_BODY = 4 * 1024 * 1024        # settings are small; this is a sanity bound
MAX_RECORD = 512 * 1024 * 1024    # a few minutes of screen video, not settings


def safe_name(name):
    """One path segment, no traversal, no surprises.

    `os.path.basename` alone is not enough on Windows, where a backslash is
    also a separator, and basename of a backslash-joined path returns the lot.
    """
    name = str(name or '').replace(chr(92), '/').split('/')[-1]
    keep = [c for c in name if c.isalnum() or c in '._-']
    out = ''.join(keep).strip('.') or 'replay'
    return out[:120]


class NoCacheHandler(SimpleHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split('?')[0] == '/records':
            try:
                names = sorted(os.listdir(REPLAYS)) if os.path.isdir(REPLAYS) else []
            except Exception as exc:
                return self._json(500, {'error': str(exc)})
            out = []
            for n in names:
                if not n.endswith('.json'):
                    continue
                try:
                    st = os.stat(os.path.join(REPLAYS, n))
                    out.append({'name': n, 'bytes': st.st_size,
                                'mtime_ms': int(st.st_mtime * 1000)})
                except OSError:
                    pass
            return self._json(200, {'records': out})
        if self.path.split('?')[0] == '/workspace':
            try:
                with io.open(WORKSPACE, encoding='utf-8') as fh:
                    return self._json(200, json.load(fh))
            except FileNotFoundError:
                return self._json(200, {})
            except Exception as exc:
                # a corrupt file must not look like "no settings": say so, and
                # let the client keep whatever it already has
                return self._json(500, {'error': str(exc)})
        if self.path.split('?')[0] == '/alerts':
            try:
                with io.open(ALERTS, encoding='utf-8') as fh:
                    body = json.load(fh)
                # THE VERSION THE CLIENT IS EDITING, so a Save can tell whether
                # the file moved underneath it. See _put_alerts.
                body['_version'] = self._alerts_version_impl()
                return self._json(200, body)
            except FileNotFoundError:
                return self._json(200, {})
            except Exception as exc:
                return self._json(500, {'error': str(exc)})
        if self.path.split('?')[0] == '/notify/resolve':
            """What a pasted address BECOMES, without sending anything.

            ONE IMPLEMENTATION, ASKED OVER HTTP, rather than a copy of the
            rules in JavaScript. `notify.normalize_target` is what the
            scheduled tools actually send through, so a second copy in the
            browser would be a second set of rules that agree until one of them
            is edited -- and the disagreement would surface as a message going
            somewhere nobody chose. The panel asks when a field is committed,
            not per keystroke, so this costs one request per edit.
            """
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            channel = (q.get('channel') or ['telegram'])[0]
            target = (q.get('target') or [''])[0]
            try:
                from tools import notify
                try:
                    out = {'ok': True,
                           'target': notify.normalize_target(channel, target)}
                    # THE CHAT'S OWN NAME, in the same round trip. The panel
                    # asks this once per row when it opens, so a second
                    # endpoint for the name would double that for one fact the
                    # first call already had the address for. A failed lookup
                    # is reported, never fatal: a destination with no readable
                    # name is still a destination.
                    who = notify.describe(channel, target)
                    if who.get('ok'):
                        out['title'] = who.get('title')
                        out['type'] = who.get('type')
                    else:
                        out['title_error'] = who.get('error')
                    return self._json(200, out)
                except Exception as exc:                # noqa: BLE001
                    # 200 with ok:false -- an unusable address is a normal
                    # answer to this question, and the reason is the payload.
                    return self._json(200, {'ok': False, 'error': str(exc)})
            except Exception as exc:                    # noqa: BLE001
                return self._json(500, {'error': str(exc)})
        if self.path.split('?')[0] == '/notify/status':
            """Which alert channels have credentials -- as booleans, never values.

            THE PAGE MUST NEVER SEE A TOKEN. Destinations are managed in the
            browser, so the panel has to say "WhatsApp has no token yet"
            without the token ever crossing the wire -- otherwise a settings
            panel becomes a way to read configs/secrets.env from any tab that
            can reach this port. `notify.credentials()` returns presence flags
            for exactly that reason, and this endpoint returns them verbatim.
            """
            try:
                from tools import notify
                return self._json(200, notify.credentials())
            except Exception as exc:                        # noqa: BLE001
                return self._json(500, {'error': str(exc)})
        if self.path.split('?')[0] == '/news/status':
            """How old the news file is, and whether a fetch is in flight.

            THE FILE'S OWN `fetchedAt` IS THE ANSWER, not the mtime. mtime moves
            when the file is copied, restored from a backup or touched by a sync;
            `fetchedAt` is written by the tool at the moment it spoke to the API,
            which is the thing a reader wants to know before trusting a headline.
            mtime is reported alongside it as a fallback and nothing more.
            """
            out = {'running': _NEWS_JOB['running'], 'last': _NEWS_JOB['last']}
            try:
                st = os.stat(NEWS)
                out['mtime_ms'] = int(st.st_mtime * 1000)
                out['bytes'] = st.st_size
                with io.open(NEWS, encoding='utf-8') as fh:
                    doc = json.load(fh)
                out['fetched_at'] = doc.get('fetchedAt')
                out['clusters'] = len(doc.get('clusters') or [])
                out['headlines'] = len(doc.get('headlines') or [])
            except FileNotFoundError:
                out['error'] = 'no news file yet'
            except Exception as exc:                        # noqa: BLE001
                out['error'] = str(exc)
            return self._json(200, out)
        if self.path.split('?')[0] == '/scheduler':
            # GET is READ-ONLY. Installing is a POST, so nothing a page loads by
            # accident can register a system task.
            return self._json(200, {'tasks': _sched_status()})
        return SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        if self.path.split('?')[0] == '/scheduler/install':
            try:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n).decode() or '{}') if n else {}
            except Exception as exc:                        # noqa: BLE001
                return self._json(400, {'error': 'bad body: %s' % exc})
            payload, code = _sched_install(body.get('task'))
            return self._json(code, payload)
        if self.path.split('?')[0] == '/alerts/test':
            """Send one test message to ONE destination, described in the body.

            THE DESTINATION COMES FROM THE REQUEST, NOT FROM DISK, and that is
            the point: the Test button sits next to a row that has not been
            saved yet, and a test that read the file would have quietly tested
            the OLD address while the reader watched the new one. Testing what
            is on screen is the only version of this button that answers the
            question it appears to answer.

            IT SENDS A REAL MESSAGE. Deliberately -- a dry run proves nothing
            about a chat id, a bot that was removed from a group, or a WhatsApp
            24-hour window. It is one message, only ever on an explicit click,
            and the text says what it is.
            """
            try:
                n = int(self.headers.get('Content-Length') or 0)
                if n <= 0 or n > MAX_BODY:
                    return self._json(400, {'error': 'bad length'})
                body = json.loads(self.rfile.read(n).decode('utf-8'))
                dest = body.get('destination') if isinstance(body, dict) else None
                if not isinstance(dest, dict) or not dest.get('target'):
                    return self._json(400, {'error': 'destination.target required'})
                from tools import notify
                text = body.get('text') or (
                    'DiaNurFx test - if you can read this, "%s" is wired up '
                    'correctly.' % (dest.get('label') or dest.get('target')))
                try:
                    notify.send_one(dest, text)
                except Exception as exc:                    # noqa: BLE001
                    # 200 WITH ok:false, not a 5xx. The send failing is a normal
                    # answer to this question -- wrong id, bot removed, window
                    # closed -- and the UI needs the API's own words for it,
                    # which an HTTP error status would reduce to a number.
                    return self._json(200, {'ok': False, 'error': str(exc)})
                return self._json(200, {'ok': True})
            except Exception as exc:                        # noqa: BLE001
                return self._json(500, {'error': str(exc)})
        if self.path.split('?')[0] == '/news/fetch':
            """Run the fetcher and answer with what the file says afterwards.

            SYNCHRONOUS, unlike the bridge's job runner. The whole call is one
            API round trip and takes a few seconds; a job id, a status endpoint
            and a polling loop would be more machinery than the thing being
            machined. The button waits, which is also the honest UI -- the news
            is either refreshed when it re-enables or it is not.

            THE KEY NEVER REACHES THE BROWSER. That is the entire reason this
            endpoint exists rather than the page calling QuantGist directly: the
            tool reads QUANTGIST_API_KEY from configs/secrets.env, server side,
            and the page only ever sees the file that comes out.
            """
            if _NEWS_JOB['running']:
                return self._json(409, {'error': 'a news fetch is already running'})
            _NEWS_JOB['running'] = True
            _NEWS_JOB['started'] = time.time()
            try:
                proc = subprocess.run(
                    [sys.executable, NEWS_FETCH], cwd=ROOT, timeout=180,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                tail = (proc.stdout or b'').decode('utf-8', 'replace').strip()
                ok = proc.returncode == 0
                _NEWS_JOB['last'] = {
                    'ok': ok, 'code': proc.returncode,
                    'ms': int((time.time() - _NEWS_JOB['started']) * 1000),
                    # the tail only: a fetcher that printed a stack trace should
                    # not push the useful last line off the top of a toast
                    'log': tail[-800:],
                }
            except subprocess.TimeoutExpired:
                _NEWS_JOB['last'] = {'ok': False, 'code': None, 'ms': 180000,
                                     'log': 'timed out after 180s'}
            except Exception as exc:                        # noqa: BLE001
                _NEWS_JOB['last'] = {'ok': False, 'code': None, 'ms': 0,
                                     'log': str(exc)}
            finally:
                _NEWS_JOB['running'] = False

            out = dict(_NEWS_JOB['last'])
            try:
                with io.open(NEWS, encoding='utf-8') as fh:
                    doc = json.load(fh)
                out['fetched_at'] = doc.get('fetchedAt')
                out['clusters'] = len(doc.get('clusters') or [])
                out['headlines'] = len(doc.get('headlines') or [])
            except Exception:                               # noqa: BLE001
                pass
            return self._json(200 if out.get('ok') else 500, out)
        if self.path.split('?')[0] != '/record':
            return self._json(404, {'error': 'not found'})
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if n <= 0 or n > MAX_RECORD:
                return self._json(400, {'error': 'bad length'})

            ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip()
            if ctype != 'application/json':
                # RAW BYTES, written exactly as received. The browser has already
                # produced a finished container; decoding and re-encoding it here
                # would be work with no purpose and one more thing to get wrong.
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                name = safe_name((qs.get('name') or ['replay'])[0])
                if '.' not in name:
                    name += '.bin'
                os.makedirs(REPLAYS, exist_ok=True)
                path = os.path.join(REPLAYS, name)
                tmp = path + '.tmp'
                # streamed in chunks: a whole video in memory is a whole video in
                # memory, and this runs beside the app on the same machine
                left = n
                with io.open(tmp, 'wb') as fh:
                    while left > 0:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            break
                        fh.write(chunk)
                        left -= len(chunk)
                os.replace(tmp, path)
                return self._json(200, {'saved': 'data/replays/' + name,
                                        'bytes': os.path.getsize(path)})

            body = json.loads(self.rfile.read(n).decode('utf-8'))
            if not isinstance(body, dict) or 'payload' not in body:
                return self._json(400, {'error': 'expected {name, payload}'})

            name = safe_name(body.get('name'))
            if not name.endswith('.json'):
                name += '.json'
            os.makedirs(REPLAYS, exist_ok=True)
            path = os.path.join(REPLAYS, name)
            # temp + replace, same as the workspace: a half-written recording is
            # worse than no recording, because it looks like one
            tmp = path + '.tmp'
            with io.open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(body['payload'], fh, indent=1)
            os.replace(tmp, path)
            return self._json(200, {'saved': 'data/replays/' + name,
                                    'bytes': os.path.getsize(path)})
        except Exception as exc:
            return self._json(500, {'error': str(exc)})

    @staticmethod
    def _alerts_version_impl():
        try:
            st = os.stat(ALERTS)
            return '%d-%d' % (st.st_mtime_ns, st.st_size)
        except OSError:
            return 'absent'

    def _put_alerts(self):
        """Replace configs/alerts.json wholesale.

        WHOLESALE, NOT MERGED, WHICH IS THE OPPOSITE OF /workspace. That file is
        a scatter of independent keys and a client must not assert what it
        lacks. This one is a SHORT LIST the user is editing as a list: removing
        a row IS the edit, and a merge would make deletion impossible without
        inventing a `del` protocol for array elements.

        The safety that matters here is different, so it is enforced instead:
        the body must contain a `scalper.watch` ARRAY, so a half-built or empty
        request cannot blank the file. And the previous version is kept as
        `.prev` before the replace, because the UI is the only writer and a bad
        save would otherwise be unrecoverable -- the same guard `/workspace`
        earned the hard way.

        IT GUARDS `scalper`, NOT `signals`, SINCE 2026-09-10. `signals` is the
        retired Donchian forward test -- still in the file, every cell disabled,
        kept for its pre-registered record -- and guarding it would have meant
        the check passed while the LIVE cell list went out empty. The guard has
        to name whichever list would actually stop the alerts if it vanished.
        """
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if n <= 0 or n > MAX_BODY:
                return self._json(400, {'error': 'bad length'})
            body = json.loads(self.rfile.read(n).decode('utf-8'))
            if not isinstance(body, dict):
                return self._json(400, {'error': 'expected an object'})
            watch = (body.get('scalper') or {}).get('watch')
            if not isinstance(watch, list):
                return self._json(400, {'error': 'scalper.watch must be an array'})

            # ---- LOST UPDATE, and it cost 18 measured cells. ----
            # This is a WHOLESALE replace of a file the panel loaded when it
            # opened. On 2026-09-10 a measurement script rewrote alerts.json
            # from 20 cells to 35 while the Settings modal was sitting open on
            # the older copy; the next Save posted that copy back and three
            # instruments lost every measured cell they had, replaced by the
            # stub `toggle()` creates for a frame it has never seen. Nothing
            # errored -- a wholesale write cannot tell a deletion from a stale
            # snapshot, which is exactly why it needs to be told.
            #
            # So GET stamps `_version` (mtime + size) and a PUT carrying a
            # stale one is refused with the current file, for the client to
            # reload and reapply. A PUT with no `_version` at all is still
            # accepted: a hand-rolled curl is a deliberate act, and the .prev
            # copy below is its safety net.
            sent = body.pop('_version', None)
            now = self._alerts_version_impl()
            if sent is not None and sent != now:
                return self._json(409, {
                    'error': 'configs/alerts.json changed since this panel '
                             'loaded it -- reopen Settings and redo the edit',
                    'version': now, 'yours': sent})

            try:
                if os.path.exists(ALERTS):
                    shutil.copyfile(ALERTS, ALERTS + '.prev')
            except OSError:
                pass                      # a missing backup is not a reason to refuse

            tmp = ALERTS + '.tmp'
            with io.open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(body, fh, indent=2, sort_keys=False)
                fh.write(chr(10))
            os.replace(tmp, ALERTS)
            return self._json(200, {'ok': True, 'cells': len(watch)})
        except Exception as exc:
            return self._json(500, {'error': str(exc)})

    def do_PUT(self):
        if self.path.split('?')[0] == '/alerts':
            return self._put_alerts()
        if self.path.split('?')[0] != '/workspace':
            return self._json(404, {'error': 'not found'})
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if n <= 0 or n > MAX_BODY:
                return self._json(400, {'error': 'bad length'})
            body = json.loads(self.rfile.read(n).decode('utf-8'))
            if not isinstance(body, dict):
                return self._json(400, {'error': 'expected an object'})

            # MERGE, never replace. A client sends the keys it HAS; it does not
            # get to assert what it lacks. A browser with cleared storage would
            # otherwise overwrite the durable copy with its own emptiness --
            # measured, before this: localStorage.clear() plus one save took the
            # file from 30 keys to 1. Removal is explicit, via `del`.
            data = {}
            try:
                with io.open(WORKSPACE, encoding='utf-8') as fh:
                    prev = json.load(fh)
                if isinstance(prev, dict):
                    data = prev
            except FileNotFoundError:
                pass
            except Exception:
                # a corrupt file is not a reason to refuse the write that would
                # replace it, but it is a reason not to merge into garbage
                data = {}
            # THE STRING "undefined" IS NOT A VALUE, IT IS A BUG ARRIVING.
            #
            # `localStorage.setItem(k, undefined)` stores the six characters
            # `undefined`, and the next save forwards them here as though they
            # were state. That is how a whole symbol's chart settings were
            # replaced by a string that parses as nothing: every later read
            # failed, fell back to defaults, and re-saved the corruption.
            #
            # A key whose incoming value is that string is dropped rather than
            # written, so the last good value survives on disk and the client
            # recovers on its next load.
            incoming = body.get('set') or {}
            rejected = [k for k, v in incoming.items()
                        if isinstance(v, str) and v in ('undefined', 'NaN')]
            for k in rejected:
                incoming.pop(k, None)
            data.update(incoming)
            for k in (body.get('del') or []):
                data.pop(k, None)

            os.makedirs(os.path.dirname(WORKSPACE), exist_ok=True)
            # ONE GENERATION OF HISTORY, kept before the replace. The write is
            # atomic, which protects against a half-written file and not at all
            # against a well-formed wrong one -- and a wrong one is what
            # actually happened. `.prev` is what makes that recoverable.
            if os.path.exists(WORKSPACE):
                try:
                    shutil.copy2(WORKSPACE, WORKSPACE + '.prev')
                except Exception:
                    pass
            tmp = WORKSPACE + '.tmp'
            with io.open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, indent=1, sort_keys=True)
            os.replace(tmp, WORKSPACE)          # atomic on the same volume
            return self._json(200, {'saved': len(data), 'rejected': rejected})
        except Exception as exc:
            return self._json(500, {'error': str(exc)})

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        SimpleHTTPRequestHandler.end_headers(self)

    def send_header(self, keyword, value):
        # drop the validator that lets browsers ask for a 304
        if keyword.lower() == 'last-modified':
            return
        SimpleHTTPRequestHandler.send_header(self, keyword, value)


#: The Windows tasks the alerting depends on, and the installer for each. A
#: FIXED MAP, not a name the client supplies: this endpoint runs a program, and
#: the one rule that makes that acceptable is that the browser can only pick
#: from this dict. Nothing here interpolates request data into a command.
SCHED_TASKS = {
    # THE LIVE SIGNAL SOURCE, and it was missing from this map until
    # 2026-09-10. The panel managed the retired Donchian alerter and the news
    # alerter and had no idea the Rayo Scalper existed -- so the one task that
    # actually sends signals could not be seen, checked or reinstalled from the
    # UI, while the retired one sat at the top of the list with a working
    # Reinstall button.
    'rayo_scalper': ('DiaNurFx-Rayo-Scalper', 'rayo_scalper_install.cmd',
                     'the Rayo Scalper: scores every enabled cell every 5 min, '
                     'journals all, messages the tradeable ones with a chart'),
    # 'Financial News Release Alert', with no DiaNurFx prefix: it warns about
    # the market's news, not about anything this project emits, and the old
    # 'Release Alert' read as a release of something we ship while sitting one
    # row from a trade-signal task it has nothing to do with. Nothing
    # enumerates tasks by the DiaNurFx prefix -- each is queried by the exact
    # name in this map -- so dropping it costs nothing.
    'event_alert': ('Financial News Release Alert', 'event_alert_install.cmd',
                    'warns before a macro news release (not trade signals)'),
}


def _sched_status():
    """What Task Scheduler currently holds for each task. Read-only.

    Reported per task rather than as one boolean, because the interesting
    failure is PARTIAL -- the bot task was missing for a day while the two alert
    tasks ran perfectly, and a single "scheduler ok" light would have hidden it.
    """
    out = {}
    for key, (name, _cmd, why) in SCHED_TASKS.items():
        row = {'task': name, 'why': why, 'registered': False}
        try:
            r = subprocess.run(['schtasks', '/Query', '/TN', name, '/FO', 'LIST', '/V'],
                               capture_output=True, text=True, timeout=20)
            if r.returncode == 0:
                row['registered'] = True
                for line in r.stdout.splitlines():
                    if ':' not in line:
                        continue
                    k, v = line.split(':', 1)
                    k, v = k.strip().lower(), v.strip()
                    if k == 'status':
                        row['status'] = v
                    elif k == 'last run time':
                        row['last_run'] = v
                    elif k == 'last result':
                        row['last_result'] = v
        except (OSError, subprocess.SubprocessError) as exc:
            row['error'] = str(exc)
        out[key] = row
    return out


def _sched_install(key):
    """Run one installer. `key` must be a key of SCHED_TASKS -- see the note there.

    NOT idempotent in the harmless sense: the .cmd uses `schtasks /Create /F`,
    which REPLACES an existing task. That is what makes it safe to re-run after
    the XML changes, and also why it briefly stops the task it is replacing.
    """
    entry = SCHED_TASKS.get(key)
    if entry is None:
        return {'error': 'unknown task %r' % key}, 400
    cmd = os.path.join(ROOT, 'configs', 'Scheduler', entry[1])
    if not os.path.exists(cmd):
        return {'error': 'installer missing: %s' % entry[1]}, 500
    try:
        r = subprocess.run(['cmd', '/c', cmd], capture_output=True, text=True,
                           timeout=120, cwd=ROOT)
    except (OSError, subprocess.SubprocessError) as exc:
        return {'error': str(exc)}, 500
    ok = r.returncode == 0 and 'SUCCESS' in (r.stdout or '')
    return {'ok': ok, 'code': r.returncode,
            'out': (r.stdout or '')[-1200:], 'err': (r.stderr or '')[-600:]}, 200


def news_interval_minutes():
    """`news.fetch_minutes` from configs/alerts.json, FOR THE BOOT LINE ONLY.

    The timer does not consult this and must not: the fetcher reads the same
    setting itself under --scheduled, and a second copy of the rule here would
    be a second place to change it. This exists so the server can say, once, on
    startup, what it is going to do -- a line that would otherwise only be
    discoverable by waiting to see whether anything happened.
    """
    try:
        with io.open(ALERTS, encoding='utf-8') as fh:
            news = (json.load(fh) or {}).get('news') or {}
        n = int(news.get('fetch_minutes') or 0)
    except (OSError, ValueError, TypeError):
        return 0
    # A floor, because this spends somebody's API quota. Below 5 minutes the
    # only thing that changes is the bill: the radar re-clusters in hours, and
    # the rail drops anything older than two days, so a one-minute poll would
    # fetch the same document sixty times an hour.
    return max(5, n) if n > 0 else 0


def news_timer():
    """Fetch the news on a schedule, while the app is open.

    WHY HERE RATHER THAN A SCHEDULED TASK. Until now nothing fetched the news
    automatically at all -- the file only changed when somebody pressed Fetch
    news now, so a rail that looked stale usually WAS stale, by days. This
    server is the process that is always up when the rail is being read, so it
    is the honest place for the timer.

    THE FETCHER DECIDES WHETHER IT IS DUE, via --if-older-than, and this loop
    only decides how often to ASK. That keeps one definition of "due" in one
    file, and it means this timer and alerts_daemon.py can both be running
    without pulling the feed twice as often as configured: whoever asks first
    satisfies the interval for both, because the question is about the age of
    the file rather than about either process's own clock.

    IT SHARES THE MANUAL FETCH'S FLAG so a timed run and a button press cannot
    overlap. Two fetchers writing one file through os.replace would not corrupt
    it, but they would spend two calls of quota to write the same document, and
    the second would clobber the `.prev` that the first had just made -- losing
    the one generation back that exists to recover a bad fetch.

    A DAEMON THREAD, so Ctrl+C on the server does not wait for it, and every
    exception is swallowed: a failed news fetch must never take down the page
    server. The tool reports its own failures into the log the panel reads.
    """
    while True:
        time.sleep(60)
        try:
            if _NEWS_JOB['running']:
                continue
            _NEWS_JOB['running'] = True
            started = time.time()
            try:
                proc = subprocess.run(
                    [sys.executable, NEWS_FETCH, '--scheduled'],
                    cwd=ROOT, timeout=180,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                tail = (proc.stdout or b'').decode('utf-8', 'replace').strip()
                # A "not due" run is not a fetch and must not be recorded as
                # one: `last` is what the Settings panel shows about the most
                # recent FETCH, and overwriting it every minute with "not due"
                # would erase the error from a fetch that actually failed.
                if not tail.startswith('not due') and not tail.startswith('automatic'):
                    _NEWS_JOB['last'] = {
                        'ok': proc.returncode == 0, 'code': proc.returncode,
                        'ms': int((time.time() - started) * 1000),
                        'log': tail[-800:], 'timed': True,
                    }
                    print('* news: timed fetch -> %s'
                          % ('ok' if proc.returncode == 0 else
                             'exit %d' % proc.returncode))
            finally:
                _NEWS_JOB['running'] = False
        except Exception as exc:                            # noqa: BLE001
            print('* news timer: %s' % exc)


def start_bot():
    """Start the Telegram command bot alongside the app. Returns the child.

    ON BY DEFAULT, because /status and /profit answering only when someone
    remembered to launch a second thing is a bot that is usually down -- which
    is exactly how it was found: dead, with no task registered and no sign of
    it. Set DNFX_BOT=0 to opt out.

    SAFE TO CALL WHEN A BOT IS ALREADY RUNNING. Telegram allows one getUpdates
    per token and two pollers fight rather than share, so this would once have
    been a real hazard. The bot now takes an exclusive lock at startup and a
    redundant copy exits immediately on its own. That check lives in the BOT
    precisely so every launcher -- this, the scheduled task, alerts_daemon.py,
    a terminal -- is safe without any of them coordinating.

    A SEPARATE PROCESS, NOT A THREAD. An unhandled exception in a thread kills
    the thread silently while the server keeps serving, so the bot would stop
    with nothing on screen to say so. A dead child is visible, and it cannot
    take the page server down with it.
    """
    if os.environ.get('DNFX_BOT') == '0':
        print('* telegram bot: skipped (DNFX_BOT=0)')
        return None
    script = os.path.join(ROOT, 'tools', 'telegram_bot.py')
    if not os.path.exists(script):
        return None
    try:
        child = subprocess.Popen([sys.executable, script], cwd=ROOT)
    except OSError as err:
        # Serving the page is the point of this process; a bot that will not
        # start must not stop the app from coming up.
        print('* telegram bot: could not start (%s)' % err)
        return None
    print('* telegram bot: started (pid %d) -- DNFX_BOT=0 to skip' % child.pid)
    return child


def stop_bot(bot):
    """Stopping the app stops the bot it started.

    Leaving it behind orphans a poller holding the token, and the next launch
    would find the lock held by a process nobody can see.
    """
    if bot is None or bot.poll() is not None:
        return
    bot.terminate()
    try:
        bot.wait(timeout=5)
    except subprocess.TimeoutExpired:
        bot.kill()
    print('* telegram bot: stopped')


def main():
    # PORT FIRST, then argv, then the default.
    #
    # The env var is what a launcher sets when it assigns a free port itself --
    # .claude/launch.json's `autoPort`, which exists because two sessions on one
    # machine cannot both hold 5173. argv still wins for a human typing
    # `python serve.py 8080`, and 5173 remains the default so the bare command
    # is unchanged.
    port = int(os.environ.get('PORT') or 0) or (
        int(sys.argv[1]) if len(sys.argv) > 1 else 5173)
    srv = ThreadingHTTPServer(('127.0.0.1', port), NoCacheHandler)
    # AFTER the bind, so a second `serve.py` on a taken port fails before it
    # spawns anything: the port is already the app's single-instance guard.
    # THE NEWS TIMER, started with the server for the same reason the bot is:
    # a refresh that only happens when somebody remembers to press a button is
    # a refresh that mostly does not happen. Off unless `news.fetch_minutes` is
    # set in configs/alerts.json -- Settings -> News.
    threading.Thread(target=news_timer, daemon=True, name='news').start()
    _iv = news_interval_minutes()
    print('* news fetch: %s'
          % ('every %d min' % _iv if _iv else 'manual only (Settings -> News)'))

    bot = start_bot()
    print('* Nur AI on http://127.0.0.1:%d (no-store; Ctrl+C to stop)' % port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n* stopping')
    finally:
        srv.server_close()
        stop_bot(bot)


if __name__ == '__main__':
    main()
