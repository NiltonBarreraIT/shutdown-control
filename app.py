import datetime as dt
import hashlib
import html
import hmac
import io
import json
import os
import secrets
import sqlite3
import unicodedata
from collections import Counter
from contextlib import contextmanager
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel

ROOT = Path(__file__).resolve().parent
DB = Path(os.getenv('DATABASE_PATH', str(ROOT / 'data' / 'shutdown.db')))
USER = os.getenv('DASHBOARD_USER', '')
PASSWORD = os.getenv('DASHBOARD_PASSWORD', '')
SESSION_IDLE_MINUTES = int(os.getenv('SESSION_IDLE_MINUTES', '30'))
LOGIN_MAX_ATTEMPTS = int(os.getenv('LOGIN_MAX_ATTEMPTS', '5'))
LOGIN_LOCK_MINUTES = int(os.getenv('LOGIN_LOCK_MINUTES', '15'))
COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'true').casefold() not in ('0', 'false', 'no')
BOOTSTRAPPED_DATABASES = set()
ROLES = ('admin', 'planner', 'supervisor', 'viewer')
ROLE_LABELS = {'admin': 'Administrador', 'planner': 'Planificador',
               'supervisor': 'Supervisor', 'viewer': 'Visualizador'}
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
      errors INTEGER DEFAULT 0, detail TEXT, pending INTEGER DEFAULT 0,
      discrepancies INTEGER DEFAULT 0)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS pending_reports (
      id TEXT PRIMARY KEY, area TEXT, reason TEXT,
      first_seen_at TEXT, updated_at TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS ot_discrepancies (
      id INTEGER PRIMARY KEY AUTOINCREMENT, form_id TEXT, original_ot TEXT,
      received_ot TEXT, source TEXT, first_seen_at TEXT, last_seen_at TEXT,
      occurrences INTEGER DEFAULT 1,
      UNIQUE(form_id, original_ot, received_ot))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'admin',
      active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
      last_login_at TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS sessions (
      token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
      created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS login_attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
      client_ip TEXT NOT NULL, failed_at TEXT NOT NULL)''')
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(sync_log)')}
    if 'errors' not in columns:
        conn.execute('ALTER TABLE sync_log ADD COLUMN errors INTEGER DEFAULT 0')
    if 'detail' not in columns:
        conn.execute('ALTER TABLE sync_log ADD COLUMN detail TEXT')
    if 'pending' not in columns:
        conn.execute('ALTER TABLE sync_log ADD COLUMN pending INTEGER DEFAULT 0')
    if 'discrepancies' not in columns:
        conn.execute('ALTER TABLE sync_log ADD COLUMN discrepancies INTEGER DEFAULT 0')
    database_key = str(DB.resolve())
    if database_key not in BOOTSTRAPPED_DATABASES and USER and PASSWORD:
        bootstrap_user = conn.execute(
            'SELECT id,password_hash FROM users WHERE username=?', (USER,)).fetchone()
        if not bootstrap_user:
            conn.execute('''INSERT INTO users(username,password_hash,role,active,created_at)
              VALUES (?,?,"admin",1,?)''',
                         (USER, hash_password(PASSWORD), utc_now()))
        elif not verify_password(PASSWORD, bootstrap_user['password_hash']):
            conn.execute('''UPDATE users SET password_hash=?,active=1
              WHERE id=?''', (hash_password(PASSWORD), bootstrap_user['id']))
            conn.execute('DELETE FROM sessions WHERE user_id=?', (bootstrap_user['id'],))
        BOOTSTRAPPED_DATABASES.add(database_key)
    conn.execute('DELETE FROM sessions WHERE expires_at<=?', (utc_now(),))
    conn.commit()
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


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def hash_password(password, salt=None):
    salt = secrets.token_bytes(16) if salt is None else salt
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 310000)
    return f'pbkdf2_sha256$310000${salt.hex()}${digest.hex()}'


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split('$', 3)
        if algorithm != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                     bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (AttributeError, TypeError, ValueError):
        return False


def number(v):
    try:
        return float(value(v).replace(',', '.').replace('%', ''))
    except ValueError:
        return None


def date_text(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat(sep=' ') if isinstance(v, dt.datetime) else v.isoformat()
    # Microsoft Forms/Excel can expose dates as an Excel serial number.
    # Convert plausible serials before persisting them instead of displaying
    # values such as 46331.1761689815 in the dashboard.
    try:
        serial = float(v)
        if 1 <= serial <= 2958465:
            converted = from_excel(serial)
            return (converted.isoformat(sep=' ') if isinstance(converted, dt.datetime)
                    else converted.isoformat())
    except (TypeError, ValueError, OverflowError):
        pass
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
FIELD_EXCLUDES = {
    # Forms includes "OT" in the activity and progress questions too. Those
    # columns must never be considered candidates for the parent work order.
    'ot': ('actividad', 'avance', 'porcentaje'),
}


class PendingReport(ValueError):
    def __init__(self, record_id, area):
        super().__init__('OT pendiente')
        self.record_id = record_id
        self.area = area


def find_value(index, aliases=(), groups=(), area='', excludes=()):
    for alias in aliases:
        if normalized_key(alias) in index:
            return index[normalized_key(alias)]
    area_key = normalized_key(area)
    candidates = []
    for key, item in index.items():
        padded = f' {key} '
        if any(f' {normalized_key(term)} ' in padded for term in excludes):
            continue
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
    fields = {name: find_value(index, groups=groups, area=area,
                               excludes=FIELD_EXCLUDES.get(name, ()))
              for name, groups in FIELD_RULES.items()}
    ot = value(fields['ot'])
    if not ot:
        raise PendingReport(record_id, area)
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


def preserve_registered_ots(conn, reports, source, observed_at):
    adjusted, discrepancies = [], []
    for report in reports:
        existing = conn.execute('SELECT ot FROM reports WHERE id=?', (report[0],)).fetchone()
        if existing and value(existing['ot']) and value(existing['ot']) != report[8]:
            received_ot = report[8]
            old_ot = value(existing['ot'])
            old_number = number(old_ot)
            received_text = value(received_ot)
            # Repair records created by the previous ambiguous header match,
            # which stored a progress percentage (0-100) as the OT. Normal OT
            # changes remain immutable and auditable below.
            if (old_number is not None and 0 <= old_number <= 100 and
                    received_text.isdigit() and len(received_text) >= 6):
                adjusted.append(report)
                continue
            report = list(report)
            report[8] = old_ot
            try:
                flags = json.loads(report[15] or '[]')
            except (TypeError, json.JSONDecodeError):
                flags = []
            alert = 'OT discrepante en origen: revisar'
            if alert not in flags:
                flags.append(alert)
            report[15] = json.dumps(flags)
            report = tuple(report)
            discrepancy = {'id': report[0], 'original_ot': report[8],
                           'received_ot': received_ot}
            discrepancies.append(discrepancy)
            conn.execute('''INSERT INTO ot_discrepancies
              (form_id,original_ot,received_ot,source,first_seen_at,last_seen_at,occurrences)
              VALUES (?,?,?,?,?,?,1)
              ON CONFLICT(form_id,original_ot,received_ot) DO UPDATE SET
              source=excluded.source, last_seen_at=excluded.last_seen_at,
              occurrences=ot_discrepancies.occurrences+1''',
                         (report[0], report[8], received_ot, source,
                          observed_at, observed_at))
        adjusted.append(report)
    return adjusted, discrepancies


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
    reports, pending, errors, seen_ids = [], [], [], set()
    for position, row in enumerate(rows, 1):
        try:
            report = normalize_json_record(row)
            record_id = report[0]
            if record_id in seen_ids:
                raise ValueError('Id duplicado dentro del lote')
            seen_ids.add(record_id)
            reports.append(report)
        except PendingReport as exc:
            if exc.record_id in seen_ids:
                errors.append({'index': position, 'error': 'Id duplicado dentro del lote'})
            else:
                seen_ids.add(exc.record_id)
                pending.append((exc.record_id, exc.area))
        except ValueError as exc:
            errors.append({'index': position, 'error': str(exc)})
    # Missing OT is an explicit pending state. Structural errors still reject the whole batch.
    committed = 0 if errors else len(reports)
    pending_count = 0 if errors else len(pending)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    pending_audit = []
    discrepancy_audit = []
    with database() as conn:
        if not errors:
            if reports:
                reports, discrepancy_audit = preserve_registered_ots(
                    conn, reports, 'Power Automate', now)
                conn.executemany(UPSERT_REPORT, reports)
                conn.executemany('DELETE FROM pending_reports WHERE id=?',
                                 ((report[0],) for report in reports))
            if pending:
                pending_rows = []
                for record_id, area in pending:
                    existing = conn.execute(
                        'SELECT flags FROM reports WHERE id=?', (record_id,)).fetchone()
                    reason = 'OT pendiente'
                    if existing:
                        reason = 'OT eliminada en origen; conserva reporte anterior para revisión'
                        try:
                            flags = json.loads(existing['flags'] or '[]')
                        except (TypeError, json.JSONDecodeError):
                            flags = []
                        review_flag = 'OT eliminada en origen: revisar'
                        if review_flag not in flags:
                            flags.append(review_flag)
                        conn.execute('UPDATE reports SET flags=? WHERE id=?',
                                     (json.dumps(flags), record_id))
                    pending_rows.append((record_id, area, reason, now, now))
                    pending_audit.append({'id': record_id, 'reason': reason})
                conn.executemany('''INSERT INTO pending_reports
                  (id,area,reason,first_seen_at,updated_at) VALUES (?,?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET area=excluded.area,
                  reason=excluded.reason, updated_at=excluded.updated_at''',
                                 pending_rows)
        review_detail = pending_audit + discrepancy_audit
        detail = errors[:20] if errors else review_detail[:20] if review_detail else None
        conn.execute('''INSERT INTO sync_log
          (at,records,source,errors,detail,pending,discrepancies)
          VALUES (?,?,?,?,?,?,?)''',
                     (now, committed, 'Power Automate', len(errors),
                      json.dumps(detail, ensure_ascii=False) if detail else None,
                      pending_count, len(discrepancy_audit)))
    return committed, pending_count, len(discrepancy_audit), errors


def import_xlsx(blob, source='Carga manual'):
    wb = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    rows = wb.active.iter_rows(values_only=True)
    next(rows, None)
    reports = [normalized for row in rows if (normalized := normalize(row))]
    if not reports:
        raise ValueError('No se encontraron reportes válidos con la estructura de Spence.')
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with database() as conn:
        reports, discrepancies = preserve_registered_ots(conn, reports, source, now)
        conn.executemany(UPSERT_REPORT, reports)
        conn.executemany('DELETE FROM pending_reports WHERE id=?',
                         ((report[0],) for report in reports))
        conn.execute('''INSERT INTO sync_log
          (at,records,source,detail,discrepancies) VALUES (?,?,?,?,?)''',
                     (now, len(reports), source,
                      json.dumps(discrepancies[:20], ensure_ascii=False) if discrepancies else None,
                      len(discrepancies)))
    return len(reports)


def payload():
    with database() as conn:
        records = [dict(r) for r in conn.execute('SELECT * FROM reports ORDER BY submitted_at DESC, id DESC')]
        last = conn.execute('''SELECT at,records,source,errors,pending,discrepancies
          FROM sync_log ORDER BY id DESC LIMIT 1''').fetchone()
        pending_count = conn.execute('SELECT count(*) FROM pending_reports').fetchone()[0]
    ot_latest = {}
    for r in records:
        ot_latest.setdefault(r['ot'], r)
        r['photos'] = json.loads(r['photos'])
        r['flags'] = json.loads(r['flags'])
    areas = Counter(r['area'] for r in records)
    return {'reports': records, 'ots': list(ot_latest.values()), 'areas': areas,
            'last_sync': dict(last) if last else None,
            'counts': {'reports': len(records), 'ots': len(ot_latest),
                       'flagged': sum(bool(r['flags']) for r in ot_latest.values()),
                       'pending': pending_count}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Never include request headers or bodies: they may contain credentials or personal data.
        super().log_message(format, *args)

    def send(self, status, content, mime='application/json', headers=None):
        out = content.encode('utf-8') if isinstance(content, str) else content
        self.send_response(status)
        self.send_header('Content-Type', f'{mime}; charset=utf-8')
        self.send_header('Content-Length', str(len(out)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        for name, item in (headers or {}).items():
            self.send_header(name, item)
        self.end_headers()
        self.wfile.write(out)

    def redirect(self, location, cookie=None):
        headers = {'Location': location}
        if cookie is not None:
            headers['Set-Cookie'] = cookie
        self.send(303, b'', 'text/plain', headers)

    def session_token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
        except Exception:
            return ''
        return cookie['shutdown_session'].value if 'shutdown_session' in cookie else ''

    def current_user(self):
        token = self.session_token()
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = dt.datetime.now(dt.timezone.utc)
        with database() as conn:
            row = conn.execute('''SELECT users.id,users.username,users.role,sessions.expires_at
              FROM sessions JOIN users ON users.id=sessions.user_id
              WHERE sessions.token_hash=? AND users.active=1''', (token_hash,)).fetchone()
            if not row or row['expires_at'] <= now.isoformat():
                conn.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash,))
                return None
            expires = (now + dt.timedelta(minutes=SESSION_IDLE_MINUTES)).isoformat()
            conn.execute('''UPDATE sessions SET last_seen_at=?,expires_at=?
              WHERE token_hash=?''', (now.isoformat(), expires, token_hash))
            return dict(row)

    def authorized(self, api=False):
        if not USER or not PASSWORD:
            self.send(503, 'Configure DASHBOARD_USER y DASHBOARD_PASSWORD.', 'text/plain')
            return False
        user = self.current_user()
        if not user:
            if api:
                self.send(401, json.dumps({'error': 'Sesión expirada o no iniciada'}))
            else:
                self.redirect('/login')
            return False
        self.user = user
        return True

    def role_allowed(self, *roles):
        if getattr(self, 'user', None) and self.user['role'] in roles:
            return True
        self.send(403, json.dumps({'error': 'No tiene permisos para realizar esta acción'},
                                  ensure_ascii=False))
        return False

    def read_json(self, maximum=16384):
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise ValueError('Content-Type debe ser application/json')
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if not 0 < length <= maximum:
            raise ValueError('Solicitud vacía o demasiado grande')
        try:
            data = json.loads(self.rfile.read(length).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('JSON inválido')
        if not isinstance(data, dict):
            raise ValueError('Se esperaba un objeto JSON')
        return data

    def users_payload(self):
        with database() as conn:
            users = [dict(row) for row in conn.execute('''SELECT id,username,role,active,
              created_at,last_login_at FROM users ORDER BY username COLLATE NOCASE''')]
        return {'users': users, 'roles': ROLE_LABELS}

    def users_request(self):
        if not self.role_allowed('admin'):
            return
        try:
            data = self.read_json()
            action = value(data.get('action'))
            with database() as conn:
                if action == 'create':
                    username = value(data.get('username'))
                    password = data.get('password', '')
                    role = value(data.get('role'))
                    if not 3 <= len(username) <= 100:
                        raise ValueError('El usuario debe tener entre 3 y 100 caracteres')
                    if not username.replace('.', '').replace('-', '').replace('_', '').isalnum():
                        raise ValueError('El usuario sólo admite letras, números, punto, guion y guion bajo')
                    if not isinstance(password, str) or len(password) < 12:
                        raise ValueError('La contraseña debe tener al menos 12 caracteres')
                    if role not in ROLES:
                        raise ValueError('Rol inválido')
                    try:
                        conn.execute('''INSERT INTO users
                          (username,password_hash,role,active,created_at)
                          VALUES (?,?,?,1,?)''',
                                     (username, hash_password(password), role, utc_now()))
                    except sqlite3.IntegrityError:
                        raise ValueError('El nombre de usuario ya existe')
                elif action == 'update':
                    user_id = int(data.get('user_id'))
                    role = value(data.get('role'))
                    active = 1 if data.get('active') is True else 0
                    if role not in ROLES:
                        raise ValueError('Rol inválido')
                    target = conn.execute('SELECT id,role,active FROM users WHERE id=?',
                                          (user_id,)).fetchone()
                    if not target:
                        raise ValueError('Usuario no encontrado')
                    if user_id == self.user['id'] and (not active or role != 'admin'):
                        raise ValueError('No puede quitar su propio acceso de administrador')
                    active_admins = conn.execute('''SELECT count(*) FROM users
                      WHERE role='admin' AND active=1''').fetchone()[0]
                    removes_admin = target['role'] == 'admin' and target['active'] and (
                        role != 'admin' or not active)
                    if removes_admin and active_admins <= 1:
                        raise ValueError('Debe existir al menos un administrador activo')
                    conn.execute('UPDATE users SET role=?,active=? WHERE id=?',
                                 (role, active, user_id))
                    if not active:
                        conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
                elif action == 'reset_password':
                    user_id = int(data.get('user_id'))
                    password = data.get('password', '')
                    if not isinstance(password, str) or len(password) < 12:
                        raise ValueError('La contraseña debe tener al menos 12 caracteres')
                    target = conn.execute('SELECT username FROM users WHERE id=?',
                                          (user_id,)).fetchone()
                    if not target:
                        raise ValueError('Usuario no encontrado')
                    if target['username'] == USER:
                        raise ValueError('Cambie la contraseña de la cuenta inicial en el archivo .env')
                    conn.execute('UPDATE users SET password_hash=? WHERE id=?',
                                 (hash_password(password), user_id))
                    conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
                else:
                    raise ValueError('Acción de usuarios inválida')
            self.send(200, json.dumps(self.users_payload(), ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            self.send(400, json.dumps({'error': str(exc)}, ensure_ascii=False))

    def login_page(self, error='', status=200):
        page = (ROOT / 'login.html').read_text(encoding='utf-8')
        page = page.replace('{{ERROR}}', html.escape(error))
        page = page.replace('{{ERROR_CLASS}}', '' if error else ' hidden')
        self.send(status, page, 'text/html')

    def login_request(self):
        if self.headers.get('Content-Type', '').split(';', 1)[0] != 'application/x-www-form-urlencoded':
            return self.login_page('Solicitud de acceso inválida.', 415)
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if not 0 < length <= 4096:
            return self.login_page('Solicitud de acceso inválida.', 400)
        try:
            form = parse_qs(self.rfile.read(length).decode('utf-8'), keep_blank_values=True)
        except UnicodeDecodeError:
            return self.login_page('Solicitud de acceso inválida.', 400)
        username = value(form.get('username', [''])[0])[:200]
        password = form.get('password', [''])[0]
        client_ip = self.client_address[0]
        now = dt.datetime.now(dt.timezone.utc)
        cutoff = (now - dt.timedelta(minutes=LOGIN_LOCK_MINUTES)).isoformat()
        with database() as conn:
            conn.execute('DELETE FROM login_attempts WHERE failed_at<?', (cutoff,))
            failures = conn.execute('''SELECT count(*) FROM login_attempts
              WHERE username=? AND client_ip=?''', (username, client_ip)).fetchone()[0]
            if failures >= LOGIN_MAX_ATTEMPTS:
                return self.login_page(
                    f'Demasiados intentos. Espere {LOGIN_LOCK_MINUTES} minutos.', 429)
            user = conn.execute('''SELECT id,username,password_hash FROM users
              WHERE username=? AND active=1''', (username,)).fetchone()
            if not user or not verify_password(password, user['password_hash']):
                conn.execute('''INSERT INTO login_attempts(username,client_ip,failed_at)
                  VALUES (?,?,?)''', (username, client_ip, now.isoformat()))
                return self.login_page('Usuario o contraseña incorrectos.', 401)
            conn.execute('DELETE FROM login_attempts WHERE username=? AND client_ip=?',
                         (username, client_ip))
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            expires = (now + dt.timedelta(minutes=SESSION_IDLE_MINUTES)).isoformat()
            conn.execute('''INSERT INTO sessions
              (token_hash,user_id,created_at,last_seen_at,expires_at)
              VALUES (?,?,?,?,?)''',
                         (token_hash, user['id'], now.isoformat(), now.isoformat(), expires))
            conn.execute('UPDATE users SET last_login_at=? WHERE id=?',
                         (now.isoformat(), user['id']))
        secure = '; Secure' if COOKIE_SECURE else ''
        cookie = (f'shutdown_session={token}; Path=/; HttpOnly; SameSite=Lax; '
                  f'Max-Age={SESSION_IDLE_MINUTES * 60}{secure}')
        self.redirect('/', cookie)

    def logout_request(self):
        token = self.session_token()
        if token:
            with database() as conn:
                conn.execute('DELETE FROM sessions WHERE token_hash=?',
                             (hashlib.sha256(token.encode()).hexdigest(),))
        secure = '; Secure' if COOKIE_SECURE else ''
        self.redirect('/login',
                      f'shutdown_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{secure}')

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
        path = urlparse(self.path).path
        if path == '/login':
            if self.current_user():
                return self.redirect('/')
            return self.login_page()
        if path == '/style.css':
            return self.send(200, (ROOT / 'style.css').read_bytes(), 'text/css')
        if path == '/img/logo.png':
            return self.send(200, (ROOT / 'img' / 'logo.png').read_bytes(), 'image/png')
        if not self.authorized(api=path.startswith('/api/')):
            return
        if path == '/api/status':
            result = payload()
            result['current_user'] = {'id': self.user['id'], 'username': self.user['username'],
                                      'role': self.user['role'],
                                      'role_label': ROLE_LABELS[self.user['role']]}
            return self.send(200, json.dumps(result, ensure_ascii=False))
        if path == '/api/users':
            if not self.role_allowed('admin'):
                return
            return self.send(200, json.dumps(self.users_payload(), ensure_ascii=False))
        if path == '/users':
            if not self.role_allowed('admin'):
                return
            return self.send(200, (ROOT / 'users.html').read_bytes(), 'text/html')
        if path == '/users.js':
            if not self.role_allowed('admin'):
                return
            return self.send(200, (ROOT / 'users.js').read_bytes(), 'application/javascript')
        if path == '/':
            return self.send(200, (ROOT / 'index.html').read_bytes(), 'text/html')
        if path == '/app.js':
            return self.send(200, (ROOT / 'app.js').read_bytes(), 'application/javascript')
        self.send(404, 'No encontrado', 'text/plain')

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/login':
            return self.login_request()
        if path == '/logout':
            if not self.authorized():
                return
            return self.logout_request()
        if path == '/api/sync/forms':
            return self.sync_forms_request()
        if not self.authorized(api=path.startswith('/api/')):
            return
        if path == '/api/users':
            return self.users_request()
        if path != '/api/import':
            return self.send(404, 'No encontrado', 'text/plain')
        if not self.role_allowed('admin', 'planner'):
            return
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
            imported, pending, discrepancies, errors = sync_forms(data)
            status = 422 if errors else 200
            result = ('rejected' if errors else
                      'success_with_review' if discrepancies else
                      'success_with_pending' if pending else 'success')
            self.send(status, json.dumps({'status': result, 'atomic': True,
                                          'processed': len(data['body']['value']),
                                          'imported': imported,
                                          'upserted': imported,
                                          'pending': pending,
                                          'discrepancies': discrepancies,
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
