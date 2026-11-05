import base64
import datetime as dt
import hmac
import io
import json
import os
import sqlite3
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent
DB = Path(os.getenv('DATABASE_PATH', str(ROOT / 'data' / 'shutdown.db')))
USER = os.getenv('DASHBOARD_USER', '')
PASSWORD = os.getenv('DASHBOARD_PASSWORD', '')
MAX_UPLOAD = 15 * 1024 * 1024
AREA_COLUMNS = {
    'Filtros': (6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16),
    'Molibdeno': (17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27),
    'Tranque': (28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38),
    'Flotación': (39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49),
}


def db_conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE IF NOT EXISTS reports (
      id TEXT PRIMARY KEY, start_at TEXT, submitted_at TEXT, reporter TEXT, email TEXT,
      area TEXT, shift TEXT, report_date TEXT, ot TEXT, activity TEXT,
      progress REAL, workers REAL, hours REAL, comment TEXT, photos TEXT,
      flags TEXT, imported_at TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS sync_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, records INTEGER, source TEXT)''')
    return conn


def value(v):
    return '' if v is None else str(v).strip()


def number(v):
    try:
        return float(value(v).replace(',', '.').replace('%', ''))
    except ValueError:
        return None


def date_text(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat(sep=' ') if isinstance(v, dt.datetime) else v.isoformat()
    return value(v)


def normalize(row):
    area_raw = value(row[5])
    area = 'Tranque' if area_raw.casefold() in ('tanque', 'tranque') else area_raw
    if area not in AREA_COLUMNS:
        return None
    ix = AREA_COLUMNS[area]
    pick = lambda k: row[ix[k]] if ix[k] < len(row) else None
    ot = value(pick(2))
    if not ot:
        return None
    progress = number(pick(4))
    workers = number(pick(5))
    hours = number(pick(6))
    activity = value(pick(3))
    flags = []
    if progress is None or not 0 <= progress <= 100:
        flags.append('Avance inválido')
        progress = None
    if workers is None or hours is None or workers < 0 or hours < 0:
        flags.append('Recursos incompletos')
    if not activity or not activity.replace('-', '').strip().isdigit():
        flags.append('Actividad por revisar')
    comment = value(pick(7))
    if progress == 0 and comment:
        flags.append('Sin avance: revisar comentario')
    photos = [value(pick(i)) for i in (8, 9, 10)]
    # Links remain as references only; downloads require separate Microsoft permissions.
    return (value(row[0]), date_text(row[1]), date_text(row[2]), value(row[4]),
            value(row[3]), area, value(pick(0)), date_text(pick(1)), ot,
            activity, progress, workers, hours, comment, json.dumps(photos),
            json.dumps(flags), dt.datetime.now(dt.timezone.utc).isoformat())


def import_xlsx(blob, source='Carga manual'):
    wb = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    rows = wb.active.iter_rows(values_only=True)
    next(rows, None)
    reports = [normalized for row in rows if (normalized := normalize(row))]
    if not reports:
        raise ValueError('No se encontraron reportes válidos con la estructura de Spence.')
    with db_conn() as conn:
        conn.executemany('''INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(id) DO UPDATE SET
          start_at=excluded.start_at, submitted_at=excluded.submitted_at,
          reporter=excluded.reporter, email=excluded.email, area=excluded.area,
          shift=excluded.shift, report_date=excluded.report_date, ot=excluded.ot,
          activity=excluded.activity, progress=excluded.progress,
          workers=excluded.workers, hours=excluded.hours,
          comment=excluded.comment, photos=excluded.photos,
          flags=excluded.flags, imported_at=excluded.imported_at''', reports)
        conn.execute('INSERT INTO sync_log(at,records,source) VALUES (?,?,?)',
                     (dt.datetime.now(dt.timezone.utc).isoformat(), len(reports), source))
    return len(reports)


def payload():
    with db_conn() as conn:
        records = [dict(r) for r in conn.execute('SELECT * FROM reports ORDER BY submitted_at DESC, id DESC')]
        last = conn.execute('SELECT at,records,source FROM sync_log ORDER BY id DESC LIMIT 1').fetchone()
    ot_latest = {}
    for r in records:
        ot_latest.setdefault(r['ot'], r)
        r['photos'] = json.loads(r['photos'])
        r['flags'] = json.loads(r['flags'])
    areas = Counter(r['area'] for r in records)
    return {'reports': records, 'ots': list(ot_latest.values()), 'areas': areas,
            'last_sync': dict(last) if last else None,
            'counts': {'reports': len(records), 'ots': len(ot_latest),
                       'flagged': sum(bool(r['flags']) for r in ot_latest.values())}}


class Handler(BaseHTTPRequestHandler):
    def send(self, status, content, mime='application/json'):
        out = content.encode('utf-8') if isinstance(content, str) else content
        self.send_response(status)
        self.send_header('Content-Type', f'{mime}; charset=utf-8')
        self.send_header('Content-Length', str(len(out)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(out)

    def authorized(self):
        if not USER or not PASSWORD:
            self.send(503, 'Configure DASHBOARD_USER y DASHBOARD_PASSWORD.', 'text/plain')
            return False
        auth = self.headers.get('Authorization', '')
        expected = 'Basic ' + base64.b64encode(f'{USER}:{PASSWORD}'.encode()).decode()
        if not hmac.compare_digest(auth, expected):
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="Shutdown Control"')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        path = urlparse(self.path).path
        if path == '/api/status':
            return self.send(200, json.dumps(payload(), ensure_ascii=False))
        if path == '/':
            return self.send(200, (ROOT / 'index.html').read_bytes(), 'text/html')
        if path == '/app.js':
            return self.send(200, (ROOT / 'app.js').read_bytes(), 'application/javascript')
        if path == '/style.css':
            return self.send(200, (ROOT / 'style.css').read_bytes(), 'text/css')
        self.send(404, 'No encontrado', 'text/plain')

    def do_POST(self):
        if not self.authorized():
            return
        if urlparse(self.path).path != '/api/import':
            return self.send(404, 'No encontrado', 'text/plain')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_UPLOAD:
                return self.send(413, json.dumps({'error': 'Archivo vacío o mayor de 15 MB'}))
            if self.headers.get('Content-Type') != 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
                return self.send(415, json.dumps({'error': 'Seleccione un archivo .xlsx'}))
            count = import_xlsx(self.rfile.read(length))
            self.send(200, json.dumps({'imported': count}))
        except Exception as exc:
            self.send(400, json.dumps({'error': str(exc)}, ensure_ascii=False))


if __name__ == '__main__':
    db_conn().close()
    host = os.getenv('HOST', '127.0.0.1')
    port = int(os.getenv('PORT', '8080'))
    print(f'Shutdown Control en {host}:{port}')
    ThreadingHTTPServer((host, port), Handler).serve_forever()
