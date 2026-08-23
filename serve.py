#!/usr/bin/env python
"""
serve.py — static dev server that does not cache.

`python -m http.server` honours If-Modified-Since and happily serves a 304, so an
edited .js file can keep running from the browser cache. That produces confusing
symptoms (a function that plainly exists on disk reporting as undefined), so this
server sends no-store on everything instead.

    python serve.py            # http://127.0.0.1:5173
    python serve.py 8080
"""

import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class NoCacheHandler(SimpleHTTPRequestHandler):
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
