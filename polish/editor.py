"""Loopback-only editor server; serves bundled Excalidraw and Polish APIs."""
from dataclasses import asdict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import tempfile
import threading
from urllib.parse import unquote, urlsplit
import webbrowser

from .compiler import compile_source
from .simulator import simulate

MAX_BODY = 2 * 1024 * 1024
STARTER = '''architecture MySystem {
  compute_pool Servers { instances = 2 }
  service API {
    hosted_on Servers
    operation health { method = GET path = "/health" }
  }
}
scenario "Health check" {
  request { entry = API protocol = http method = GET path = "/health" }
  expect { outcome = success reaches API.health }
}
'''


def analyze(source, *, config_dir=None, run_scenarios=False):
    compilation = compile_source(source, config_dir=config_dir)
    payload = {'ok': compilation.ok, 'diagnostics': [d.to_dict() for d in compilation.diagnostics],
               'graph': None, 'results': []}
    if compilation.ok:
        arch = compilation.architecture
        payload['graph'] = {'name': arch.name, 'nodes': [asdict(n) for n in arch.nodes.values()],
                            'edges': [asdict(e) for e in arch.edges], 'scenarios': [s.name for s in arch.scenarios],
                            'kinds': list(arch.component_definitions)}
        if run_scenarios:
            payload['results'] = [simulate(arch, scenario).to_dict() for scenario in arch.scenarios]
            payload['ok'] = bool(arch.scenarios) and all(r['passed'] for r in payload['results'])
            if not arch.scenarios:
                payload['diagnostics'].append({'code': 'E_NO_SCENARIOS', 'message': 'Add a scenario to simulate this architecture.'})
    return payload


def digest(data):
    return hashlib.sha256(data).hexdigest()


class EditorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, file=None, *, port=8765, config_dir=None, static_dir=None):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('Editor port must be between 0 and 65535')
        self.file = Path(file).resolve() if file is not None else None
        data = self.file.read_bytes() if self.file else STARTER.encode('utf-8')
        self.source = data.decode('utf-8')
        self.revision = digest(data) if self.file else None
        self.config_dir = config_dir
        self.static_dir = Path(static_dir) if static_dir else Path(__file__).with_name('editor_static')
        if not (self.static_dir / 'index.html').is_file():
            raise ValueError('Editor assets are missing. Build them with npm ci and npm run build in editor-ui/.')
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        super().__init__(('127.0.0.1', port), EditorHandler)
        self.authorities = {f'127.0.0.1:{self.server_port}', f'localhost:{self.server_port}'}
        self.url = f'http://127.0.0.1:{self.server_port}/#token={self.token}'


class EditorHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass

    def respond(self, status, body, content_type='application/json; charset=utf-8'):
        data = json.dumps(body).encode() if content_type.startswith('application/json') else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def allowed(self, api=False):
        if self.headers.get('Host') not in self.server.authorities:
            self.respond(403, {'error': 'Unrecognized editor host'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{a}' for a in self.server.authorities}:
            self.respond(403, {'error': 'Cross-origin requests are not allowed'})
            return False
        if api and not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+self.server.token):
            self.respond(401, {'error': 'Open the editor using the URL printed by Polish'})
            return False
        return True

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if not self.allowed(api=path.startswith('/api/')):
            return
        if path == '/api/session':
            with self.server.lock:
                self.respond(200, {'source': self.server.source, 'revision': self.server.revision,
                    'filename': self.server.file.name if self.server.file else 'architecture.polishd',
                    'canSave': self.server.file is not None,
                    'documentId': digest(str(self.server.file or 'scratch').encode())})
            return
        if path.startswith('/api/'):
            self.respond(404, {'error': 'Unknown editor API'})
            return
        target = (self.server.static_dir / ('index.html' if path == '/' else path.lstrip('/'))).resolve()
        if not target.is_relative_to(self.server.static_dir.resolve()) or not target.is_file():
            self.respond(404, {'error': 'Not found'})
            return
        mime = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        if target.suffix == '.js':
            mime = 'text/javascript'
        self.respond(200, target.read_bytes(), mime)

    def do_POST(self):
        if not self.allowed(api=True):
            return
        path = urlsplit(self.path).path
        if path not in ('/api/check', '/api/simulate', '/api/save', '/api/canvas'):
            self.respond(404, {'error': 'Unknown editor API'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_BODY:
                self.respond(413, {'error': 'Specification exceeds the 2 MiB request limit'})
                return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.respond(415, {'error': 'Send application/json'})
                return
            payload = json.loads(self.rfile.read(size))
            source = payload.get('source') if isinstance(payload, dict) else None
            if not isinstance(source, str):
                raise ValueError('source must be a string')
        except (ValueError, UnicodeError):
            self.respond(400, {'error': 'Invalid specification request'})
            return
        if path == '/api/save':
            self.save(source, payload.get('revision'))
            return
        if path == '/api/canvas':
            from .editor_sync import apply_canvas, CanvasEditError
            try:
                candidate = apply_canvas(source, payload.get('nodes'), payload.get('edges'), config_dir=self.server.config_dir)
                result = analyze(candidate, config_dir=self.server.config_dir)
                result['source'] = candidate if result['ok'] else source
                self.respond(200, result)
            except (CanvasEditError, TypeError, KeyError) as exc:
                self.respond(400, {'error': str(exc) if isinstance(exc, CanvasEditError) else 'Invalid canvas edit'})
            return
        try:
            self.respond(200, analyze(source, config_dir=self.server.config_dir, run_scenarios=path == '/api/simulate'))
        except (ValueError, OSError):
            self.respond(400, {'error': 'Cannot evaluate the specification with this configuration'})

    def save(self, source, revision):
        with self.server.lock:
            file = self.server.file
            if file is None:
                self.respond(409, {'error': 'No file is attached. Download the specification instead.'})
                return
            temporary = None
            try:
                current = file.read_bytes()
                if revision != digest(current):
                    self.respond(409, {'error': 'The file changed on disk. Reopen the editor before saving; download your draft to keep it.'})
                    return
                with tempfile.NamedTemporaryFile(dir=file.parent, prefix='.polish-editor-', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(source.encode('utf-8'))
                os.chmod(temporary, file.stat().st_mode)
                os.replace(temporary, file)
                self.server.source = source
                self.server.revision = digest(source.encode('utf-8'))
                self.respond(200, {'revision': self.server.revision})
            except OSError:
                self.respond(400, {'error': 'Cannot save the attached file'})
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)


def serve(file=None, *, port=8765, config_dir=None, open_browser=True):
    with EditorServer(file, port=port, config_dir=config_dir) as server:
        print(f'Polish editor: {server.url}', flush=True)
        print('Local editor only. Press Ctrl+C to stop.', flush=True)
        if open_browser:
            webbrowser.open(server.url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
