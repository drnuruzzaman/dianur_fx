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
import sys
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
# configs/, not data/: this is project configuration, and data/ is for things
# the app GENERATES (replays, downloads). Secrets never reach it -- see
# LOCAL_ONLY in util.js, which keeps the PIN and its recovery password in
# browser storage only. That matters more here than it did under data/,
# because configs/ is tracked by git and data/ is not.
WORKSPACE = os.path.join(ROOT, 'configs', 'workspace.json')
REPLAYS = os.path.join(ROOT, 'data', 'replays')
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
        return SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
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

    def do_PUT(self):
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
            data.update(body.get('set') or {})
            for k in (body.get('del') or []):
                data.pop(k, None)

            os.makedirs(os.path.dirname(WORKSPACE), exist_ok=True)
            tmp = WORKSPACE + '.tmp'
            with io.open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, indent=1, sort_keys=True)
            os.replace(tmp, WORKSPACE)          # atomic on the same volume
            return self._json(200, {'saved': len(data)})
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


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5173
    srv = ThreadingHTTPServer(('127.0.0.1', port), NoCacheHandler)
    print('* Nur AI on http://127.0.0.1:%d (no-store; Ctrl+C to stop)' % port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n* stopping')
    finally:
        srv.server_close()


if __name__ == '__main__':
    main()
