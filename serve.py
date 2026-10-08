#!/usr/bin/env python3
"""OpportunityMap static server — gzip compression + revalidation caching.

Usage:  python3 serve.py [port]     (default 8734)

Why not `python -m http.server`: that serves the ~45 MB data payload raw.
GeoJSON gzips ~7x, and this server also answers 304 Not Modified so a
reload only re-downloads files that actually changed.
"""
import gzip
import os
import sys
from email.utils import formatdate, parsedate_to_datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

COMPRESSIBLE = {'.html', '.css', '.js', '.json', '.geojson', '.svg', '.md'}
_gz_cache = {}   # path -> (mtime, gzipped bytes)


class Handler(SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        '.geojson': 'application/geo+json',
        '.js': 'text/javascript',
    }

    def send_head(self):
        path = self.translate_path(self.path)
        ext = os.path.splitext(path)[1].lower()
        wants_gz = 'gzip' in (self.headers.get('Accept-Encoding') or '')
        if not (ext in COMPRESSIBLE and wants_gz and os.path.isfile(path)):
            return super().send_head()

        mtime = os.path.getmtime(path)
        # 304 if the client already has this version
        ims = self.headers.get('If-Modified-Since')
        if ims:
            try:
                if parsedate_to_datetime(ims).timestamp() >= int(mtime):
                    self.send_response(304)
                    self.end_headers()
                    return None
            except (TypeError, ValueError):
                pass

        cached = _gz_cache.get(path)
        if not cached or cached[0] != mtime:
            with open(path, 'rb') as f:
                cached = (mtime, gzip.compress(f.read(), 6))
            _gz_cache[path] = cached

        body = cached[1]
        self.send_response(200)
        self.send_header('Content-Type', self.guess_type(path))
        self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Last-Modified', formatdate(mtime, usegmt=True))
        self.send_header('Cache-Control', 'no-cache')   # always revalidate → stale tabs impossible
        self.end_headers()
        import io
        return io.BytesIO(body)


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8734
    print(f'OpportunityMap → http://127.0.0.1:{port}  (gzip + 304 revalidation)')
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
