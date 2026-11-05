import base64
import datetime as dt
import hmac
import io
import json
import os
import sqlite3
import unicodedata
from collections import Counter
from contextlib import contextmanager
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
MAX_SYNC_BODY = int(os.getenv('SYNC_MAX_BYTES', str(2 * 1024 * 1024)))
MAX_SYNC_RECORDS = int(os.getenv('SYNC_MAX_RECORDS', '5000'))
SYNC_TOKEN = os.getenv('FORMS_SYNC_TOKEN', '')
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
      id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, records INTEGER, source TEXT,
      errors INTEGER DEFAULT 0, detail TEXT)''')
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(sync_log)')}
    if 'errors' not in columns:
        conn.execute('ALTER TABLE sync_log ADD COLUMN errors INTEGER DEFAULT 0')
    if 'detail' not in columns:
        conn.execute('ALTER TABLE sync_log ADD COLUMN detail TEXT')
    return conn


@contextmanager
def database():
    conn = db_conn()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


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


def normalized_key(text):
    text = unicodedata.normalize('NFKD', value(text)).encode('ascii', 'ignore').decode()
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in text.casefold()).split())


CORE_ALIASES = {
    'id': ('id', 'identificador', 'response id', 'identificador de respuesta'),
    'start_at': ('start time', 'hora de inicio', 'fecha de inicio'),
    'submitted_at': ('completion time', 'hora de finalizacion', 'fecha de finalizacion',
                     'submitted at', 'fecha de envio'),
    'email': ('email', 'correo electronico', 'correo'),
    'reporter': ('name', 'nombre', 'respondent', 'respondedor'),
    'area': ('area', 'seleccione area', 'selecciona area', 'cual es tu area de trabajo'),
}
FIELD_RULES = {
    # Each inner tuple is a required semantic group; one alternative in every group must match.
    'shift': (('turno',),),
    'report_date': (('fecha',),),
    'ot': (('ot', 'orden de trabajo'),),
    'activity': (('actividad',),),
    'progress': (('avance', 'porcentaje'),),
    'workers': (('trabajadores', 'personal', 'dotacion'),),
    'hours': (('hora', 'horas'),),
    'comment': (('comentario', 'resena', 'observacion'),),
    'photo_before': (('foto', 'fotografia', 'imagen'), ('antes',)),
    'photo_during': (('foto', 'fotografia', 'imagen'), ('durante',)),
    'photo_after': (('foto', 'fotografia', 'imagen'), ('final', 'despues'),),
}


def find_value(index, aliases=(), groups=(), area=''):
    for alias in aliases:
        if normalized_key(alias) in index:
            return index[normalized_key(alias)]
    area_key = normalized_key(area)
    candidates = []
    for key, item in index.items():
        padded = f' {key} '
        if groups and all(any(f' {normalized_key(term)} ' in padded
                              for term in alternatives)
                          for alternatives in groups):
            candidates.append((area_key not in key, len(key), item))
    return min(candidates, default=(True, 0, None))[2]


def normalize_json_record(record):
    if not isinstance(record, dict):
        raise ValueError('el registro debe ser un objeto JSON')
    if len(record) > 200 or any(not isinstance(k, str) for k in record):
        raise ValueError('el registro contiene demasiadas columnas o nombres inválidos')
    for item in record.values():
        if isinstance(item, (dict, list)) or (isinstance(item, str) and len(item) > 10000):
            raise ValueError('el registro contiene un tipo o texto no permitido')
    index = {normalized_key(k): v for k, v in record.items()}
    core = {name: find_value(index, aliases=aliases) for name, aliases in CORE_ALIASES.items()}
    record_id = value(core['id'])
    if not record_id or len(record_id) > 200:
        raise ValueError('falta un Id válido de Microsoft Forms')
    area_raw = value(core['area'])
    area_lookup = {normalized_key(a): a for a in AREA_COLUMNS}
    area_lookup['tanque'] = 'Tranque'
    area = area_lookup.get(normalized_key(area_raw))
    if not area:
        raise ValueError('área inválida; use Filtros, Molibdeno, Tranque o Flotación')
    fields = {name: find_value(index, groups=groups, area=area)
              for name, groups in FIELD_RULES.items()}
    ot = value(fields['ot'])
    if not ot:
        raise ValueError('falta la OT del área seleccionada')
    progress, workers, hours = (number(fields[name]) for name in ('progress', 'workers', 'hours'))
    flags = []
    if progress is None or not 0 <= progress <= 100:
        flags.append('Avance inválido')
        progress = None
    if workers is None or hours is None or workers < 0 or hours < 0:
        flags.append('Recursos incompletos')
    activity = value(fields['activity'])
    if not activity or not activity.replace('-', '').strip().isdigit():
        flags.append('Actividad por revisar')
    comment = value(fields['comment'])
    if progress == 0 and comment:
        flags.append('Sin avance: revisar comentario')
    photos = [value(fields[name]) for name in ('photo_before', 'photo_during', 'photo_after')]
    return (record_id, date_text(core['start_at']), date_text(core['submitted_at']),
            value(core['reporter']), value(core['email']), area, value(fields['shift']),
            date_text(fields['report_date']), ot, activity, progress, workers, hours,
            comment, json.dumps(photos), json.dumps(flags),
            dt.datetime.now(dt.timezone.utc).isoformat())


UPSERT_REPORT = '''INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
  ON CONFLICT(id) DO UPDATE SET
  start_at=excluded.start_at, submitted_at=excluded.submitted_at,
  reporter=excluded.reporter, email=excluded.email, area=excluded.area,
  shift=excluded.shift, report_date=excluded.report_date, ot=excluded.ot,
  activity=excluded.activity, progress=excluded.progress,
  workers=excluded.workers, hours=excluded.hours, comment=excluded.comment,
  photos=excluded.photos, flags=excluded.flags, imported_at=excluded.imported_at'''


def log_sync(records, source, errors=0, detail=None):
    with database() as conn:
        conn.execute('INSERT INTO sync_log(at,records,source,errors,detail) VALUES (?,?,?,?,?)',
                     (dt.datetime.now(dt.timezone.utc).isoformat(), records, source,
                      errors, value(detail)[:1000] or None))


def sync_forms(data):
    if not isinstance(data, dict) or not isinstance(data.get('body'), dict):
        raise ValueError('se esperaba un objeto con body.value')
    rows = data['body'].get('value')
    if not isinstance(rows, list):
        raise ValueError('body.value debe ser una lista')
    if len(rows) > MAX_SYNC_RECORDS:
        raise ValueError(f'body.value supera el máximo de {MAX_SYNC_RECORDS} registros')
    reports, errors = [], []
    for position, row in enumerate(rows, 1):
        try:
            reports.append(normalize_json_record(row))
        except ValueError as exc:
            errors.append({'index': position, 'error': str(exc)})
    # The batch is atomic: a malformed row rejects every row, avoiding a silent partial sync.
    committed = 0 if errors else len(reports)
    with database() as conn:
        if not errors and reports:
            conn.executemany(UPSERT_REPORT, reports)
        conn.execute('INSERT INTO sync_log(at,records,source,errors,detail) VALUES (?,?,?,?,?)',
                     (dt.datetime.now(dt.timezone.utc).isoformat(), committed,
                      'Power Automate', len(errors),
                      json.dumps(errors[:20], ensure_ascii=False) if errors else None))
    return committed, errors


def import_xlsx(blob, source='Carga manual'):
    wb = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    rows = wb.active.iter_rows(values_only=True)
    next(rows, None)
    reports = [normalized for row in rows if (normalized := normalize(row))]
    if not reports:
        raise ValueError('No se encontraron reportes válidos con la estructura de Spence.')
    with database() as conn:
        conn.executemany(UPSERT_REPORT, reports)
        conn.execute('INSERT INTO sync_log(at,records,source) VALUES (?,?,?)',
                     (dt.datetime.now(dt.timezone.utc).isoformat(), len(reports), source))
    return len(reports)


def payload():
    with database() as conn:
        records = [dict(r) for r in conn.execute('SELECT * FROM reports ORDER BY submitted_at DESC, id DESC')]
        last = conn.execute('SELECT at,records,source,errors FROM sync_log ORDER BY id DESC LIMIT 1').fetchone()
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
    def log_message(self, format, *args):
        # Never include request headers or bodies: they may contain credentials or personal data.
        super().log_message(format, *args)

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

    def sync_authorized(self):
        if not SYNC_TOKEN:
            self.send(503, json.dumps({'error': 'API de sincronización no configurada'}))
            return False
        auth = self.headers.get('Authorization', '')
        if not auth.startswith('Bearer ') or not hmac.compare_digest(auth[7:], SYNC_TOKEN):
            self.send(401, json.dumps({'error': 'No autorizado'}))
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
        path = urlparse(self.path).path
        if path == '/api/sync/forms':
            return self.sync_forms_request()
        if not self.authorized():
            return
        if path != '/api/import':
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

    def sync_forms_request(self):
        if not self.sync_authorized():
            return
        try:
            content_type = self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            if content_type != 'application/json':
                log_sync(0, 'Power Automate', 1, 'Content-Type inválido')
                return self.send(415, json.dumps({'error': 'Content-Type debe ser application/json'}))
            try:
                length = int(self.headers.get('Content-Length', ''))
            except ValueError:
                length = 0
            if not 0 < length <= MAX_SYNC_BODY:
                log_sync(0, 'Power Automate', 1, 'Tamaño de solicitud inválido')
                return self.send(413, json.dumps({'error': 'Solicitud vacía o demasiado grande'}))
            raw = self.rfile.read(length)
            data = json.loads(raw.decode('utf-8'))
            imported, errors = sync_forms(data)
            status = 422 if errors else 200
            result = 'rejected' if errors else 'success'
            self.send(status, json.dumps({'status': result, 'atomic': True,
                                          'processed': len(data['body']['value']),
                                          'upserted': imported,
                                          'rejected': len(data['body']['value']) if errors else 0,
                                          'errors': errors[:20]},
                                         ensure_ascii=False))
        except (UnicodeDecodeError, json.JSONDecodeError):
            log_sync(0, 'Power Automate', 1, 'JSON inválido')
            self.send(400, json.dumps({'error': 'JSON inválido'}))
        except ValueError as exc:
            log_sync(0, 'Power Automate', 1, str(exc))
            self.send(400, json.dumps({'error': str(exc)}, ensure_ascii=False))


if __name__ == '__main__':
    db_conn().close()
    host = os.getenv('HOST', '127.0.0.1')
    port = int(os.getenv('PORT', '8080'))
    print(f'Shutdown Control en {host}:{port}')
    ThreadingHTTPServer((host, port), Handler).serve_forever()
