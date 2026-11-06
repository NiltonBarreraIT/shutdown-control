import datetime as dt
import hashlib
import html
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import contextmanager
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel
from progress_model import CHILE, curve as progress_curve, moment as progress_moment, projections, summarize

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
EMAIL_PATTERN = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
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
      discrepancies INTEGER DEFAULT 0, conflicts INTEGER DEFAULT 0)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS pending_reports (
      id TEXT PRIMARY KEY, area TEXT, reason TEXT,
      first_seen_at TEXT, updated_at TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS ot_discrepancies (
      id INTEGER PRIMARY KEY AUTOINCREMENT, form_id TEXT, original_ot TEXT,
      received_ot TEXT, source TEXT, first_seen_at TEXT, last_seen_at TEXT,
      occurrences INTEGER DEFAULT 1,
      UNIQUE(form_id, original_ot, received_ot))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS report_conflicts (
      id INTEGER PRIMARY KEY AUTOINCREMENT, form_id TEXT NOT NULL,
      existing_payload TEXT NOT NULL, received_payload TEXT NOT NULL,
      first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
      occurrences INTEGER DEFAULT 1,
      UNIQUE(form_id, existing_payload, received_payload))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS operation_anomalies (
      id INTEGER PRIMARY KEY AUTOINCREMENT, ot TEXT NOT NULL, activity TEXT NOT NULL,
      raw_progress REAL NOT NULL, detected_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL, resolved_at TEXT,
      UNIQUE(ot,activity))''')
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
    conn.execute('''CREATE TABLE IF NOT EXISTS plan_versions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL,
      source_format TEXT NOT NULL, imported_at TEXT NOT NULL,
      imported_by INTEGER NOT NULL, task_count INTEGER NOT NULL,
      active INTEGER NOT NULL DEFAULT 1,
      FOREIGN KEY(imported_by) REFERENCES users(id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS plan_tasks (
      id INTEGER PRIMARY KEY AUTOINCREMENT, version_id INTEGER NOT NULL,
      source_uid TEXT, wbs TEXT, ot TEXT NOT NULL, activity TEXT NOT NULL,
      name TEXT NOT NULL, area TEXT, shift TEXT, journey TEXT,
      start_at TEXT, finish_at TEXT,
      baseline_start TEXT, baseline_finish TEXT, duration_hours REAL, planned_hh REAL,
      weight REAL, critical INTEGER DEFAULT 0, milestone INTEGER DEFAULT 0,
      predecessors TEXT, UNIQUE(version_id,ot,activity,source_uid),
      FOREIGN KEY(version_id) REFERENCES plan_versions(id) ON DELETE CASCADE)''')
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(sync_log)')}
    if 'errors' not in columns:
        conn.execute('ALTER TABLE sync_log ADD COLUMN errors INTEGER DEFAULT 0')
    if 'detail' not in columns:
        conn.execute('ALTER TABLE sync_log ADD COLUMN detail TEXT')
    if 'pending' not in columns:
        conn.execute('ALTER TABLE sync_log ADD COLUMN pending INTEGER DEFAULT 0')
    if 'discrepancies' not in columns:
        conn.execute('ALTER TABLE sync_log ADD COLUMN discrepancies INTEGER DEFAULT 0')
    if 'conflicts' not in columns:
        conn.execute('ALTER TABLE sync_log ADD COLUMN conflicts INTEGER DEFAULT 0')
    plan_columns = {row['name'] for row in conn.execute('PRAGMA table_info(plan_tasks)')}
    if 'shift' not in plan_columns:
        conn.execute('ALTER TABLE plan_tasks ADD COLUMN shift TEXT')
    if 'journey' not in plan_columns:
        conn.execute('ALTER TABLE plan_tasks ADD COLUMN journey TEXT')
    if 'planned_hh' not in plan_columns:
        conn.execute('ALTER TABLE plan_tasks ADD COLUMN planned_hh REAL')
    # Normalize legacy SAP operation codes so Forms "10" and Gantt "0010"
    # resolve to the same OT/OP without deleting or merging source records.
    for row in conn.execute('SELECT id,activity FROM reports'):
        activity = str(row['activity'] or '').strip()
        if activity.isdigit() and activity != activity.zfill(4):
            conn.execute('UPDATE reports SET activity=? WHERE id=?',
                         (activity.zfill(4), row['id']))
    for row in conn.execute('SELECT id,activity FROM plan_tasks'):
        activity = str(row['activity'] or '').strip()
        if activity.isdigit() and activity != activity.zfill(4):
            try:
                conn.execute('UPDATE plan_tasks SET activity=? WHERE id=?',
                             (activity.zfill(4), row['id']))
            except sqlite3.IntegrityError:
                # Preserve both legacy rows if normalization would collide.
                pass
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


def operation_identifier(item):
    text = value(item)
    if re.fullmatch(r'\d+\.0+', text):
        text = text.split('.', 1)[0]
    return text.zfill(4) if text.isdigit() else text


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
    activity = operation_identifier(pick(3))
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
    activity = operation_identifier(fields['activity'])
    if not activity or not activity.replace('-', '').strip().isdigit():
        flags.append('Actividad por revisar')
    comment = value(fields['comment'])
    if progress == 0 and comment:
        flags.append('Sin avance: revisar comentario')
    start_at, submitted_at = date_text(core['start_at']), date_text(core['submitted_at'])
    if (progress_moment(start_at) and progress_moment(submitted_at) and
            progress_moment(submitted_at) < progress_moment(start_at)):
        flags.append('Fecha inconsistente: envío anterior al inicio')
    photos = [value(fields[name]) for name in ('photo_before', 'photo_during', 'photo_after')]
    return (record_id, start_at, submitted_at,
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

REPORT_FIELDS = ('id', 'start_at', 'submitted_at', 'reporter', 'email', 'area', 'shift',
                 'report_date', 'ot', 'activity', 'progress', 'workers', 'hours',
                 'comment', 'photos', 'flags')


def report_content(report):
    return {field: report[index] for index, field in enumerate(REPORT_FIELDS)}


def report_fingerprint(report):
    return json.dumps(report_content(report), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'))


def audit_report_conflicts(conn, reports, observed_at):
    accepted, conflicts, identical = [], [], 0
    for report in reports:
        existing = conn.execute('SELECT * FROM reports WHERE id=?', (report[0],)).fetchone()
        if not existing:
            accepted.append(report)
            continue
        existing_tuple = tuple(existing[field] for field in REPORT_FIELDS)
        existing_payload = report_fingerprint(existing_tuple)
        received_payload = report_fingerprint(report)
        if existing_payload == received_payload:
            identical += 1
            continue
        conn.execute('''INSERT INTO report_conflicts
          (form_id,existing_payload,received_payload,first_seen_at,last_seen_at,occurrences)
          VALUES (?,?,?,?,?,1)
          ON CONFLICT(form_id,existing_payload,received_payload) DO UPDATE SET
          last_seen_at=excluded.last_seen_at,occurrences=report_conflicts.occurrences+1''',
                     (report[0], existing_payload, received_payload,
                      observed_at, observed_at))
        conflicts.append({'id': report[0], 'type': 'same_id_different_content'})
        accepted.append(report)
    return accepted, conflicts, identical


def flag_unplanned_operations(conn, reports):
    version = conn.execute('''SELECT id FROM plan_versions WHERE active=1
      ORDER BY id DESC LIMIT 1''').fetchone()
    if not version:
        return reports, []
    planned = {(row['ot'], row['activity']) for row in conn.execute(
        'SELECT ot,activity FROM plan_tasks WHERE version_id=?', (version['id'],))}
    planned_ots = {item[0] for item in planned}
    adjusted, findings = [], []
    for report in reports:
        alert = None
        if report[8] not in planned_ots:
            alert = 'OT inexistente en planificación activa'
        elif (report[8], report[9]) not in planned:
            alert = 'OP inexistente en planificación activa'
        if alert:
            item = list(report)
            flags = json.loads(item[15] or '[]')
            if alert not in flags:
                flags.append(alert)
            item[15] = json.dumps(flags, ensure_ascii=False)
            report = tuple(item)
            findings.append({'id': report[0], 'type': alert})
        adjusted.append(report)
    return adjusted, findings


def refresh_progress_anomalies(conn, observed_at):
    excessive = list(conn.execute('''SELECT ot,activity,SUM(progress) AS raw_progress
      FROM reports WHERE progress IS NOT NULL AND ot<>'' AND activity<>''
      GROUP BY ot,activity HAVING SUM(progress)>100'''))
    active = {(row['ot'], row['activity']) for row in excessive}
    for row in excessive:
        conn.execute('''INSERT INTO operation_anomalies
          (ot,activity,raw_progress,detected_at,last_seen_at,resolved_at)
          VALUES (?,?,?,?,?,NULL)
          ON CONFLICT(ot,activity) DO UPDATE SET raw_progress=excluded.raw_progress,
          last_seen_at=excluded.last_seen_at,resolved_at=NULL''',
                     (row['ot'], row['activity'], row['raw_progress'], observed_at, observed_at))
    for row in conn.execute('''SELECT ot,activity FROM operation_anomalies
      WHERE resolved_at IS NULL'''):
        if (row['ot'], row['activity']) not in active:
            conn.execute('''UPDATE operation_anomalies SET resolved_at=?,last_seen_at=?
              WHERE ot=? AND activity=?''',
                         (observed_at, observed_at, row['ot'], row['activity']))
    return len(excessive)


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
    batch_duplicates = 0
    for position, row in enumerate(rows, 1):
        try:
            report = normalize_json_record(row)
            record_id = report[0]
            if record_id in seen_ids:
                # Forms Id is the report identity. Power Automate can occasionally
                # return the same table row twice after rows are deleted/reordered.
                # Keep the first occurrence so one duplicate cannot reject the
                # other valid reports in the batch or overwrite ambiguous data.
                batch_duplicates += 1
                continue
            seen_ids.add(record_id)
            reports.append(report)
        except PendingReport as exc:
            if exc.record_id in seen_ids:
                batch_duplicates += 1
            else:
                seen_ids.add(exc.record_id)
                pending.append((exc.record_id, exc.area))
        except ValueError as exc:
            errors.append({'index': position, 'error': str(exc)})
    # Missing OT is an explicit pending state. Invalid source rows are isolated so
    # one stale/deleted Excel table row does not discard the valid batch.
    committed = len(reports)
    pending_count = len(pending)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    pending_audit = []
    discrepancy_audit = []
    conflict_audit = []
    identical_count = 0
    with database() as conn:
        if reports:
            reports, discrepancy_audit = preserve_registered_ots(
                conn, reports, 'Power Automate', now)
            reports, membership_audit = flag_unplanned_operations(conn, reports)
            reports, conflict_audit, identical_count = audit_report_conflicts(
                conn, reports, now)
            conflict_audit.extend(membership_audit)
            committed = len(reports)
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
        if reports or pending:
            refresh_progress_anomalies(conn, now)
        review_detail = pending_audit + discrepancy_audit + conflict_audit
        detail = errors[:20] if errors else review_detail[:20] if review_detail else None
        conn.execute('''INSERT INTO sync_log
          (at,records,source,errors,detail,pending,discrepancies,conflicts)
          VALUES (?,?,?,?,?,?,?,?)''',
                     (now, committed, 'Power Automate', len(errors),
                      json.dumps(detail, ensure_ascii=False) if detail else None,
                      pending_count, len(discrepancy_audit), len(conflict_audit)))
    return (committed, pending_count, len(discrepancy_audit), len(conflict_audit),
            identical_count + batch_duplicates, errors)


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
        reports, membership = flag_unplanned_operations(conn, reports)
        reports, conflicts, _ = audit_report_conflicts(conn, reports, now)
        conflicts.extend(membership)
        conn.executemany(UPSERT_REPORT, reports)
        conn.executemany('DELETE FROM pending_reports WHERE id=?',
                         ((report[0],) for report in reports))
        refresh_progress_anomalies(conn, now)
        conn.execute('''INSERT INTO sync_log
          (at,records,source,detail,discrepancies) VALUES (?,?,?,?,?)''',
                     (now, len(reports), source,
                      json.dumps((discrepancies + conflicts)[:20], ensure_ascii=False)
                      if discrepancies or conflicts else None,
                      len(discrepancies)))
    return len(reports)


PLAN_ALIASES = {
    'uid': ('uid', 'id unico', 'unique id', 'id gantt', 'id'),
    'wbs': ('wbs', 'edt', 'outline number'),
    'ot': ('ot', 'orden de trabajo', 'work order'),
    'activity': ('op', 'actividad', 'activity', 'operation'),
    'name': ('nombre', 'nombre de tarea', 'task name', 'tarea', 'actividad gantt'),
    'area': ('area',),
    'shift': ('turno', 'shift'),
    'journey': ('jornada', 'work period'),
    'start': ('comienzo', 'inicio', 'start'),
    'finish': ('fin', 'termino', 'finish'),
    'baseline_start': ('comienzo previsto', 'baseline start', 'inicio linea base'),
    'baseline_finish': ('fin previsto', 'baseline finish', 'termino linea base'),
    'duration': ('duracion', 'duration', 'duracion horas', 'dur h'),
    'planned_hh': ('hh', 'hh planificadas', 'horas hombre', 'planned hh'),
    'weight': ('peso', 'ponderacion', 'weight'),
    'critical': ('critica', 'critico', 'critical'),
    'milestone': ('hito', 'milestone'),
    'predecessors': ('predecesoras', 'predecessors'),
}


def plan_boolean(item):
    return normalized_key(item) in ('1', 'si', 'yes', 'true', 'verdadero')


def plan_number(item):
    parsed = number(item)
    return parsed if parsed is not None and parsed >= 0 else None


def plan_identifier(item, minimum_digits=0):
    text = value(item)
    if re.fullmatch(r'\d+\.0+', text):
        text = text.split('.', 1)[0]
    return text.zfill(minimum_digits) if text.isdigit() else text


def plan_shift(item, start_at):
    explicit = value(item)
    if explicit:
        return explicit
    match = re.search(r'[T ](\d{2}):\d{2}', value(start_at))
    if not match:
        return ''
    hour = int(match.group(1))
    return 'Día' if 8 <= hour < 20 else 'Noche'


def plan_task(values):
    name = value(values.get('name'))
    ot = plan_identifier(values.get('ot'))
    activity = plan_identifier(values.get('activity'), 4)
    if not ot and name:
        match = re.search(r'\bOT\s*[:#-]?\s*(\d{4,})\b', name, re.IGNORECASE)
        ot = match.group(1) if match else ''
    if not activity and name:
        match = re.search(r'\b(?:OP|ACTIVIDAD)\s*[:#-]?\s*([A-Z0-9.-]+)\b',
                          name, re.IGNORECASE)
        activity = match.group(1) if match else ''
    activity = plan_identifier(activity, 4)
    if not name:
        return None
    duration = plan_number(values.get('duration'))
    area_raw = value(values.get('area'))
    area_lookup = {normalized_key(area): area for area in AREA_COLUMNS}
    area_lookup['flotacin'] = 'Flotación'
    area = area_lookup.get(normalized_key(area_raw), area_raw)
    start_at = date_text(values.get('start'))
    return {'source_uid': value(values.get('uid')), 'wbs': value(values.get('wbs')),
            'ot': ot, 'activity': activity, 'name': name,
            'area': area, 'shift': plan_shift(values.get('shift'), start_at),
            'journey': value(values.get('journey')),
            'start_at': start_at,
            'finish_at': date_text(values.get('finish')),
            'baseline_start': date_text(values.get('baseline_start')),
            'baseline_finish': date_text(values.get('baseline_finish')),
            'duration_hours': duration, 'planned_hh': plan_number(values.get('planned_hh')),
            'weight': plan_number(values.get('weight')),
            'critical': int(plan_boolean(values.get('critical'))),
            'milestone': int(plan_boolean(values.get('milestone'))),
            'predecessors': value(values.get('predecessors'))}


def parse_plan_xlsx(blob):
    workbook = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    rows = workbook.active.iter_rows(values_only=True)
    mapping = None
    for row_number, candidate in enumerate(rows, 1):
        index = {normalized_key(header): position for position, header in enumerate(candidate)
                 if value(header)}
        candidate_mapping = {}
        for field, aliases in PLAN_ALIASES.items():
            for alias in aliases:
                if normalized_key(alias) in index:
                    candidate_mapping[field] = index[normalized_key(alias)]
                    break
        if all(field in candidate_mapping for field in ('ot', 'activity', 'name')):
            mapping = candidate_mapping
            break
        if row_number >= 50:
            break
    if mapping is None:
        raise ValueError('No se encontró una fila de encabezados con OT, OP/actividad y nombre de tarea')
    tasks = []
    for row_number, row in enumerate(rows, row_number + 1):
        values = {field: row[position] if position < len(row) else None
                  for field, position in mapping.items()}
        values.setdefault('uid', str(row_number))
        task = plan_task(values)
        if task:
            tasks.append(task)
    return tasks


def xml_child(node, name):
    child = node.find(f'{{*}}{name}')
    return child.text if child is not None and child.text is not None else ''


def parse_plan_xml(blob):
    try:
        root = ET.fromstring(blob)
    except ET.ParseError as exc:
        raise ValueError(f'XML de Microsoft Project inválido: {exc}')
    aliases = {}
    for definition in root.findall('.//{*}ExtendedAttributes/{*}ExtendedAttribute'):
        field_id, alias = xml_child(definition, 'FieldID'), xml_child(definition, 'Alias')
        if field_id and alias:
            aliases[field_id] = normalized_key(alias)
    tasks = []
    for node in root.findall('.//{*}Tasks/{*}Task'):
        if xml_child(node, 'Summary') == '1':
            continue
        extended = {}
        for item in node.findall('./{*}ExtendedAttribute'):
            extended[aliases.get(xml_child(item, 'FieldID'), '')] = xml_child(item, 'Value')
        def custom(field):
            for alias in PLAN_ALIASES[field]:
                if normalized_key(alias) in extended:
                    return extended[normalized_key(alias)]
            return ''
        predecessors = ','.join(xml_child(item, 'PredecessorUID')
                                for item in node.findall('./{*}PredecessorLink')
                                if xml_child(item, 'PredecessorUID'))
        task = plan_task({'uid': xml_child(node, 'UID'), 'wbs': xml_child(node, 'WBS'),
                          'ot': custom('ot'), 'activity': custom('activity'),
                          'area': custom('area'), 'shift': custom('shift'),
                          'journey': custom('journey'), 'weight': custom('weight'),
                          'planned_hh': custom('planned_hh'),
                          'name': xml_child(node, 'Name'), 'start': xml_child(node, 'Start'),
                          'finish': xml_child(node, 'Finish'),
                          'baseline_start': xml_child(node.find('./{*}Baseline'), 'Start')
                          if node.find('./{*}Baseline') is not None else '',
                          'baseline_finish': xml_child(node.find('./{*}Baseline'), 'Finish')
                          if node.find('./{*}Baseline') is not None else '',
                          'critical': xml_child(node, 'Critical'),
                          'milestone': xml_child(node, 'Milestone'),
                          'predecessors': predecessors})
        if task:
            tasks.append(task)
    return tasks


def save_plan(tasks, filename, source_format, user_id):
    if not tasks:
        raise ValueError('No se encontraron tareas con OT, OP y nombre válidos')
    keys = [(task['ot'], task['activity'], task['source_uid']) for task in tasks]
    if len(keys) != len(set(keys)):
        raise ValueError('La Carta Gantt contiene filas duplicadas con la misma OT, OP e ID Gantt')
    with database() as conn:
        conn.execute('UPDATE plan_versions SET active=0')
        cursor = conn.execute('''INSERT INTO plan_versions
          (filename,source_format,imported_at,imported_by,task_count,active)
          VALUES (?,?,?,?,?,1)''', (filename[:255], source_format, utc_now(), user_id, len(tasks)))
        version_id = cursor.lastrowid
        conn.executemany('''INSERT INTO plan_tasks
          (version_id,source_uid,wbs,ot,activity,name,area,shift,journey,start_at,finish_at,
           baseline_start,baseline_finish,duration_hours,planned_hh,weight,critical,milestone,predecessors)
          VALUES (:version_id,:source_uid,:wbs,:ot,:activity,:name,:area,:shift,:journey,:start_at,:finish_at,
                  :baseline_start,:baseline_finish,:duration_hours,:planned_hh,:weight,:critical,:milestone,:predecessors)''',
                         ({**task, 'version_id': version_id} for task in tasks))
    return version_id


def plan_payload(filters=None):
    filters = filters or {}
    with database() as conn:
        version = conn.execute('''SELECT plan_versions.*,users.username AS imported_by_name
          FROM plan_versions JOIN users ON users.id=plan_versions.imported_by
          WHERE plan_versions.active=1 ORDER BY plan_versions.id DESC LIMIT 1''').fetchone()
        if not version:
            return {'version': None, 'tasks': [], 'history': [], 'counts': {'tasks': 0, 'matched': 0,
                    'unmatched': 0, 'missing_ids': 0, 'critical': 0}}
        tasks = [dict(row) for row in conn.execute('''SELECT plan_tasks.*
          FROM plan_tasks WHERE plan_tasks.version_id=?
          ORDER BY plan_tasks.wbs,plan_tasks.id''',
                                                   (version['id'],))]
        for task in tasks:
            task['shift'] = plan_shift(task.get('shift'), task.get('start_at'))
        history = [dict(row) for row in conn.execute('''SELECT r.id,r.ot,r.activity,r.progress,
          r.start_at,r.submitted_at,r.report_date,r.workers,r.hours,r.area,r.shift,r.comment,r.flags
          FROM reports r WHERE EXISTS (SELECT 1 FROM plan_tasks p
            WHERE p.version_id=? AND p.ot=r.ot AND p.activity=r.activity)
          ORDER BY r.submitted_at,r.id''', (version['id'],))]
    analysis_tasks = [task for task in tasks
                      if (not filters.get('area') or task.get('area') == filters['area'])
                      and (not filters.get('ot') or task.get('ot') == filters['ot'])
                      and (not filters.get('op') or task.get('activity') == filters['op'])
                      and (not filters.get('shift') or task.get('shift') == filters['shift'])]
    cutoff = progress_moment(filters.get('cutoff'), end_of_day=True) if filters.get('cutoff') else None
    analytics = summarize(analysis_tasks, history, cutoff)
    operation_map = {(item['ot'], item['op']): item for item in analytics['operations']}
    for task in tasks:
        operation = operation_map.get((task['ot'], task['activity']))
        reports = operation['reports'] if operation else []
        task['report_id'] = reports[-1]['id'] if reports else None
        task['actual_progress'] = operation['actual'] if operation else None
        task['planned_progress'] = operation['planned'] if operation else None
        task['progress_variance'] = (operation['actual'] - operation['planned']) if operation else None
        task['raw_actual_progress'] = operation['raw_actual'] if operation else None
        task['report_count'] = operation['report_count'] if operation else 0
        task['last_report_at'] = reports[-1]['submitted_at'] if reports else None
    finish = max((task['finish_at'] for task in analysis_tasks if task.get('finish_at')), default=None)
    curve = progress_curve(analysis_tasks, history, cutoff)
    forecast = projections(curve, finish)
    executive = {key: value for key, value in analytics.items() if key != 'operations'}
    executive['operations'] = [{key: value for key, value in operation.items() if key != 'reports'}
                               for operation in analytics['operations']]
    executive['curve'] = curve
    executive['projection'] = forecast
    executive['planned_finish'] = finish
    executive['reference_bhp'] = {
        'cutoff': '2026-11-06T08:00:00-03:00', 'planned': 66.90, 'actual': 52.31,
        'variance': -14.59, 'maximum_attainable': 66.72,
        'activities': 3449, 'planned_hh': 46524.5,
    }
    matched = sum(task['report_id'] is not None for task in tasks)
    missing_ids = sum(not task['ot'] or not task['activity'] for task in tasks)
    return {'version': dict(version), 'tasks': tasks, 'history': history,
            'analytics': executive,
            'counts': {'tasks': len(tasks), 'matched': matched,
                       'unmatched': len(tasks) - matched,
                       'missing_ids': missing_ids,
                       'critical': sum(task['critical'] for task in tasks)}}


def payload():
    with database() as conn:
        records = [dict(r) for r in conn.execute('SELECT * FROM reports ORDER BY submitted_at DESC, id DESC')]
        active_version = conn.execute('''SELECT id FROM plan_versions WHERE active=1
          ORDER BY id DESC LIMIT 1''').fetchone()
        active_tasks = ([dict(row) for row in conn.execute(
            'SELECT * FROM plan_tasks WHERE version_id=?', (active_version['id'],))]
                        if active_version else [])
        last = conn.execute('''SELECT at,records,source,errors,pending,discrepancies,conflicts
          FROM sync_log ORDER BY id DESC LIMIT 1''').fetchone()
        pending_count = conn.execute('SELECT count(*) FROM pending_reports').fetchone()[0]
    synthetic_tasks = []
    seen_operations = set()
    for record in records:
        key = (record['ot'], record['activity'])
        if all(key) and key not in seen_operations:
            seen_operations.add(key)
            synthetic_tasks.append({'ot': key[0], 'activity': key[1], 'name': '',
                                    'area': record['area'], 'shift': record['shift']})
    consolidated = summarize(active_tasks or synthetic_tasks, records)
    ot_progress = {item['ot']: item for item in consolidated['work_orders']}
    ot_latest = {}
    for r in records:
        ot_latest.setdefault(r['ot'], r)
        r['photos'] = json.loads(r['photos'])
        r['flags'] = json.loads(r['flags'])
    for ot, latest in ot_latest.items():
        if ot in ot_progress:
            latest['progress'] = ot_progress[ot]['progress']
            latest['progress_method'] = ot_progress[ot]['method']
            latest['operation_count'] = ot_progress[ot]['operations']
    areas = Counter(r['area'] for r in records)
    return {'reports': records, 'ots': list(ot_latest.values()), 'areas': areas,
            'last_sync': dict(last) if last else None,
            'counts': {'reports': len(records), 'ots': len(ot_latest),
                       'progress': consolidated['actual'],
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
                    username = value(data.get('username')).casefold()
                    password = data.get('password', '')
                    role = value(data.get('role'))
                    if len(username) > 254 or not EMAIL_PATTERN.fullmatch(username):
                        raise ValueError('Ingrese un correo electrónico válido')
                    if not isinstance(password, str) or len(password) < 12:
                        raise ValueError('La contraseña debe tener al menos 12 caracteres')
                    if role not in ROLES:
                        raise ValueError('Rol inválido')
                    if conn.execute('SELECT 1 FROM users WHERE lower(username)=lower(?)',
                                    (username,)).fetchone():
                        raise ValueError('El correo electrónico ya está registrado')
                    try:
                        conn.execute('''INSERT INTO users
                          (username,password_hash,role,active,created_at)
                          VALUES (?,?,?,1,?)''',
                                     (username, hash_password(password), role, utc_now()))
                    except sqlite3.IntegrityError:
                        raise ValueError('El correo electrónico ya está registrado')
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
        username = value(form.get('username', [''])[0]).casefold()[:254]
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
              WHERE lower(username)=lower(?) AND active=1''', (username,)).fetchone()
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
        request_url = urlparse(self.path)
        path = request_url.path
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
        if path == '/api/plan':
            return self.send(200, json.dumps(plan_payload(), ensure_ascii=False))
        if path == '/api/plan/analytics':
            query = parse_qs(request_url.query)
            filters = {name: value(query.get(name, [''])[0])
                       for name in ('area', 'ot', 'op', 'shift', 'cutoff')}
            result = plan_payload(filters)
            return self.send(200, json.dumps({'analytics': result.get('analytics')},
                                             ensure_ascii=False))
        if path == '/plan':
            return self.send(200, (ROOT / 'plan.html').read_bytes(), 'text/html')
        if path == '/plan.js':
            return self.send(200, (ROOT / 'plan.js').read_bytes(), 'application/javascript')
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
        if path == '/api/plan/import':
            if not self.role_allowed('admin', 'planner'):
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_UPLOAD:
                    return self.send(413, json.dumps({'error': 'Archivo vacío o mayor de 15 MB'}))
                content_type = self.headers.get('Content-Type', '').split(';', 1)[0].lower()
                filename = value(self.headers.get('X-Filename')) or 'carta-gantt'
                blob = self.rfile.read(length)
                if content_type == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
                    tasks, source_format = parse_plan_xlsx(blob), 'xlsx'
                elif content_type in ('application/xml', 'text/xml'):
                    tasks, source_format = parse_plan_xml(blob), 'project_xml'
                else:
                    return self.send(415, json.dumps({'error': 'Use Excel .xlsx o Microsoft Project XML'}))
                version_id = save_plan(tasks, filename, source_format, self.user['id'])
                self.send(200, json.dumps({'version_id': version_id, 'imported': len(tasks)}))
            except (ValueError, ET.ParseError) as exc:
                self.send(422, json.dumps({'error': str(exc)}, ensure_ascii=False))
            return
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
            imported, pending, discrepancies, conflicts, duplicates, errors = sync_forms(data)
            accepted = imported + pending
            status = 422 if errors and not accepted else 200
            result = ('rejected' if errors and not accepted else
                      'success_with_errors' if errors else
                      'success_with_review' if discrepancies or conflicts else
                      'success_with_pending' if pending else 'success')
            self.send(status, json.dumps({'status': result, 'atomic': not errors,
                                          'processed': len(data['body']['value']),
                                          'imported': imported,
                                          'upserted': imported,
                                          'pending': pending,
                                          'discrepancies': discrepancies,
                                          'conflicts': conflicts,
                                          'duplicates_ignored': duplicates,
                                          'rejected': len(errors),
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
