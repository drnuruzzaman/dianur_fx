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

    GET  /workspace   ->  data/workspace.json, or {} when there is none
    PUT  /workspace   <-  {set: {...}, del: [...]}, merged into the file

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
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

WORKSPACE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'data', 'workspace.json')
MAX_BODY = 4 * 1024 * 1024        # settings are small; this is a sanity bound


class NoCacheHandler(SimpleHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
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
