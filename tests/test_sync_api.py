import base64
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import app


AREAS = ('Filtros', 'Molibdeno', 'Tranque', 'Flotación')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def row(record_id='101', area='Filtros', progress='25'):
    record = {
        'Id': record_id,
        'Hora de inicio': '2026-11-05T08:00:00Z',
        'Hora de finalización': '2026-11-05T08:10:00Z',
        'Correo electrónico': 'persona@example.invalid',
        'Nombre': 'Persona de prueba',
        '¿Cuál es tu área de trabajo?': area,
    }
    # Excel Online returns the columns for all four branches of the Form.
    for candidate in AREAS:
        suffix = f'({candidate})'
        selected = candidate == area
        fields = {
            f'¿Cuál es tu turno? {suffix}': 'DIA',
            f'¿Qué fecha corresponde al reporte? {suffix}': '2026-11-05',
            f'Ingresa el número de OT {suffix}': f'45000{AREAS.index(candidate) + 1}',
            f'Ingresa el número de actividad {suffix}': '10',
            f'¿Cuál es el porcentaje de avance? {suffix}': progress,
            f'¿Cuántos trabajadores participaron? {suffix}': '4',
            f'¿Cuántas horas fueron trabajadas? {suffix}': '6',
            f'Ingresa una reseña del trabajo realizado {suffix}': f'Reseña ficticia {candidate}',
            f'Carga una fotografía del ANTES del trabajo {suffix}': f'https://example.invalid/{candidate}/antes',
            f'Carga una fotografía del DURANTE del trabajo {suffix}': f'https://example.invalid/{candidate}/durante',
            f'Carga una fotografía del FINAL del trabajo {suffix}': f'https://example.invalid/{candidate}/final',
        }
        record.update({key.replace(' ', '\u00a0', 1) if candidate == 'Flotación' else key:
                       value if selected else '' for key, value in fields.items()})
    return record


def without_ot(record):
    return set_ot(record, '')


def set_ot(record, ot):
    area = record['¿Cuál es tu área de trabajo?']
    for key in tuple(record):
        if ' OT ' in key and f'({area})' in key:
            record[key] = ot
    return record


class SyncApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app.DB = Path(self.temp.name) / 'test.db'
        app.SYNC_TOKEN = 'token-de-prueba-no-real'
        app.USER = 'dashboard'
        app.PASSWORD = 'clave-de-prueba'
        app.COOKIE_SECURE = False
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, payload, token=None):
        token = app.SYNC_TOKEN if token is None else token
        body = json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base + '/api/sync/forms', data=body, method='POST',
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def login_cookie(self, username='dashboard', password='clave-de-prueba'):
        body = urllib.parse.urlencode({'username': username, 'password': password}).encode()
        request = urllib.request.Request(
            self.base + '/login', data=body, method='POST',
            headers={'Content-Type': 'application/x-www-form-urlencoded'})
        opener = urllib.request.build_opener(NoRedirect())
        try:
            opener.open(request)
        except urllib.error.HTTPError as exc:
            response = exc
        else:
            self.fail('El login debía responder con redirección')
        self.assertEqual(303, response.code)
        cookie = response.headers['Set-Cookie'].split(';', 1)[0]
        response.close()
        return cookie

    def dashboard_api(self, path, cookie, payload=None, method='GET'):
        body = None if payload is None else json.dumps(payload).encode()
        headers = {'Cookie': cookie}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base + path, data=body, method=method,
                                         headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            result = json.load(exc)
            return exc.code, result

    def test_requires_independent_bearer_token(self):
        status, result = self.request({'body': {'value': [row()]}}, 'incorrecto')
        self.assertEqual(401, status)
        self.assertNotIn('token', json.dumps(result).casefold())
        with app.database() as conn:
            self.assertEqual(0, conn.execute('SELECT count(*) FROM reports').fetchone()[0])

    def test_accepts_power_automate_body_value_and_upserts(self):
        status, result = self.request({'body': {'value': [row()]}})
        self.assertEqual((200, 1), (status, result['upserted']))
        status, result = self.request({'body': {'value': [row(progress='70')]}})
        self.assertEqual((200, 1), (status, result['upserted']))
        with app.database() as conn:
            reports = conn.execute('SELECT id,progress FROM reports').fetchall()
            self.assertEqual([('101', 70.0)], [tuple(item) for item in reports])
            self.assertEqual(2, conn.execute('SELECT count(*) FROM sync_log').fetchone()[0])

    def test_maps_real_headers_for_every_area(self):
        rows = [row(str(index), area) for index, area in enumerate(AREAS, 201)]
        status, result = self.request({'body': {'value': rows}})
        self.assertEqual(200, status)
        self.assertEqual({'status': 'success', 'atomic': True, 'upserted': 4},
                         {key: result[key] for key in ('status', 'atomic', 'upserted')})
        with app.database() as conn:
            stored = [dict(item) for item in conn.execute(
                '''SELECT id,start_at,submitted_at,area,shift,report_date,ot,activity,progress,workers,hours,
                          comment,photos FROM reports ORDER BY id''')]
        self.assertEqual(AREAS, tuple(item['area'] for item in stored))
        for item, area in zip(stored, AREAS):
            self.assertEqual(('2026-11-05T08:00:00Z', '2026-11-05T08:10:00Z'),
                             (item['start_at'], item['submitted_at']))
            self.assertEqual('DIA', item['shift'])
            self.assertEqual('2026-11-05', item['report_date'])
            self.assertEqual(f'45000{AREAS.index(area) + 1}', item['ot'])
            self.assertEqual(('10', 25.0, 4.0, 6.0),
                             (item['activity'], item['progress'], item['workers'], item['hours']))
            self.assertEqual(f'Reseña ficticia {area}', item['comment'])
            self.assertEqual([f'https://example.invalid/{area}/antes',
                              f'https://example.invalid/{area}/durante',
                              f'https://example.invalid/{area}/final'], json.loads(item['photos']))

    def test_ot_question_is_not_confused_with_progress_question(self):
        record = row('REAL-HEADERS', 'Molibdeno', progress='50')
        for key in tuple(record):
            if '(Molibdeno)' in key and ('número de OT' in key or 'porcentaje de avance' in key):
                del record[key]
        record['¿Cuál es la OT que vas a reportar?\u00a0(Molibdeno)'] = '621315053'
        record['¿Cuál es el avance de la OT?\u00a0(Molibdeno)'] = '50'
        status, result = self.request({'body': {'value': [record]}})
        self.assertEqual((200, 1), (status, result['upserted']))
        with app.database() as conn:
            stored = conn.execute(
                "SELECT ot,progress FROM reports WHERE id='REAL-HEADERS'").fetchone()
        self.assertEqual(('621315053', 50.0), tuple(stored))

    def test_activity_number_uses_the_question_for_the_selected_area(self):
        activity_headers = {
            'Filtros': '¿Cuál es el número de actividad? (Filtros)',
            'Molibdeno': '¿Cuál es el número de actividad de la OT? (Molibdeno)',
            'Tranque': '¿Cuál en el número de la actividad? (Tranque)',
            'Flotación': '¿Cuál es el número de la actividad? (Flotación)',
        }
        rows = []
        expected = []
        for index, area in enumerate(AREAS, 1):
            record = row(f'ACTIVITY-{index}', area)
            suffix = f'({area})'
            for key in tuple(record):
                if suffix in key and 'actividad' in key.casefold():
                    del record[key]
            activity = f'{index}020'
            record[activity_headers[area]] = activity
            rows.append(record)
            expected.append((area, f'45000{index}', activity))

        status, result = self.request({'body': {'value': rows}})
        self.assertEqual((200, 4), (status, result['upserted']))
        with app.database() as conn:
            stored = [tuple(item) for item in conn.execute(
                '''SELECT area,ot,activity FROM reports
                   WHERE id LIKE 'ACTIVITY-%' ORDER BY id''')]
        self.assertEqual(expected, stored)

    def test_repairs_progress_value_previously_saved_as_ot(self):
        self.request({'body': {'value': [set_ot(row('REPAIR-OT'), '100')]}})
        status, result = self.request({'body': {'value': [
            set_ot(row('REPAIR-OT', progress='100'), '62256002')]}})
        self.assertEqual((200, 0), (status, result['discrepancies']))
        with app.database() as conn:
            stored = conn.execute(
                "SELECT ot,progress,flags FROM reports WHERE id='REPAIR-OT'").fetchone()
        self.assertEqual(('62256002', 100.0, []),
                         (stored['ot'], stored['progress'], json.loads(stored['flags'])))

    def test_rejects_bad_structure_and_records_error(self):
        status, result = self.request({'value': [row()]})
        self.assertEqual(400, status)
        self.assertIn('body.value', result['error'])
        with app.database() as conn:
            log = conn.execute('SELECT records,errors FROM sync_log').fetchone()
            self.assertEqual((0, 1), tuple(log))

    def test_rejects_entire_batch_when_one_row_is_invalid(self):
        invalid = row('102')
        invalid['¿Cuál es tu área de trabajo?'] = 'Otra'
        status, result = self.request({'body': {'value': [row(), invalid]}})
        self.assertEqual(422, status)
        self.assertEqual(('rejected', True, 2, 0, 2, 1),
                         (result['status'], result['atomic'], result['processed'],
                          result['upserted'], result['rejected'], len(result['errors'])))
        with app.database() as conn:
            self.assertEqual(0, conn.execute('SELECT count(*) FROM reports').fetchone()[0])
            log = conn.execute('SELECT records,errors FROM sync_log').fetchone()
            self.assertEqual((0, 1), tuple(log))

    def test_rejects_duplicate_forms_id_inside_one_batch(self):
        status, result = self.request({'body': {'value': [row('DUP-1'), row('DUP-1')]}})
        self.assertEqual((422, 'rejected', 0, 0),
                         (status, result['status'], result['imported'], result['pending']))
        self.assertIn('Id duplicado', result['errors'][0]['error'])
        with app.database() as conn:
            self.assertEqual(0, conn.execute('SELECT count(*) FROM reports').fetchone()[0])
            self.assertEqual(0, conn.execute('SELECT count(*) FROM pending_reports').fetchone()[0])

    def test_imports_58_and_tracks_2_pending_without_ot(self):
        rows = [row(str(1000 + index), AREAS[index % len(AREAS)]) for index in range(60)]
        without_ot(rows[58])
        without_ot(rows[59])

        status, result = self.request({'body': {'value': rows}})
        self.assertEqual(200, status)
        self.assertEqual(('success_with_pending', 60, 58, 2, 0, []),
                         (result['status'], result['processed'], result['imported'],
                          result['pending'], result['rejected'], result['errors']))
        with app.database() as conn:
            self.assertEqual(58, conn.execute('SELECT count(*) FROM reports').fetchone()[0])
            pending = [tuple(item) for item in conn.execute(
                'SELECT id,reason FROM pending_reports ORDER BY id')]
            self.assertEqual([('1058', 'OT pendiente'), ('1059', 'OT pendiente')], pending)

        # The same Forms Id becomes a normal report when an OT is supplied later.
        status, result = self.request({'body': {'value': [row('1058', AREAS[58 % 4])]}})
        self.assertEqual((200, 'success', 1, 0),
                         (status, result['status'], result['imported'], result['pending']))
        with app.database() as conn:
            self.assertEqual(59, conn.execute('SELECT count(*) FROM reports').fetchone()[0])
            self.assertEqual(1, conn.execute('SELECT count(*) FROM pending_reports').fetchone()[0])
            self.assertEqual(1, conn.execute(
                "SELECT count(*) FROM reports WHERE id='1058'").fetchone()[0])

    def test_preserves_valid_history_when_existing_response_loses_ot(self):
        original = set_ot(row('HISTORY-1'), '450099')
        status, result = self.request({'body': {'value': [original]}})
        self.assertEqual((200, 'success', 1),
                         (status, result['status'], result['imported']))

        status, result = self.request({'body': {'value': [without_ot(row('HISTORY-1'))]}})
        self.assertEqual((200, 'success_with_pending', 0, 1),
                         (status, result['status'], result['imported'], result['pending']))
        with app.database() as conn:
            report = conn.execute(
                "SELECT ot,flags FROM reports WHERE id='HISTORY-1'").fetchone()
            pending = conn.execute(
                "SELECT reason FROM pending_reports WHERE id='HISTORY-1'").fetchone()
            audit = conn.execute(
                'SELECT detail FROM sync_log ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual('450099', report['ot'])
        self.assertIn('OT eliminada en origen: revisar', json.loads(report['flags']))
        self.assertIn('conserva reporte anterior', pending['reason'])
        self.assertIn('HISTORY-1', audit['detail'])

        recovered = set_ot(row('HISTORY-1', progress='80'), '450099')
        status, result = self.request({'body': {'value': [recovered]}})
        self.assertEqual((200, 'success', 1, 0),
                         (status, result['status'], result['imported'], result['pending']))
        with app.database() as conn:
            reports = conn.execute(
                "SELECT ot,progress,flags FROM reports WHERE id='HISTORY-1'").fetchall()
            pending_count = conn.execute(
                "SELECT count(*) FROM pending_reports WHERE id='HISTORY-1'").fetchone()[0]
        self.assertEqual(1, len(reports))
        self.assertEqual(('450099', 80.0, []),
                         (reports[0]['ot'], reports[0]['progress'], json.loads(reports[0]['flags'])))
        self.assertEqual(0, pending_count)

    def test_same_ot_450099_synced_twice_remains_450099(self):
        for progress in ('20', '20'):
            status, result = self.request({'body': {'value': [
                set_ot(row('IMMUTABLE-1', progress=progress), '450099')]}})
            self.assertEqual((200, 0), (status, result['discrepancies']))
        with app.database() as conn:
            reports = conn.execute(
                "SELECT ot FROM reports WHERE id='IMMUTABLE-1'").fetchall()
        self.assertEqual(1, len(reports))
        self.assertEqual('450099', reports[0]['ot'])

    def test_ot_450100_is_audited_but_does_not_replace_450099(self):
        self.request({'body': {'value': [set_ot(row('IMMUTABLE-2'), '450099')]}})
        status, result = self.request({'body': {'value': [
            set_ot(row('IMMUTABLE-2', progress='50'), '450100')]}})
        self.assertEqual((200, 'success_with_review', 1),
                         (status, result['status'], result['discrepancies']))
        with app.database() as conn:
            report = conn.execute(
                "SELECT ot,progress,flags FROM reports WHERE id='IMMUTABLE-2'").fetchone()
            discrepancy = conn.execute('''SELECT original_ot,received_ot,occurrences
              FROM ot_discrepancies WHERE form_id='IMMUTABLE-2' ''').fetchone()
        self.assertEqual(('450099', 50.0), (report['ot'], report['progress']))
        self.assertIn('OT discrepante en origen: revisar', json.loads(report['flags']))
        self.assertEqual(('450099', '450100', 1), tuple(discrepancy))

    def test_progress_changes_from_20_to_50_without_changing_ot(self):
        self.request({'body': {'value': [
            set_ot(row('IMMUTABLE-3', progress='20'), '450099')]}})
        self.request({'body': {'value': [
            set_ot(row('IMMUTABLE-3', progress='50'), '450099')]}})
        with app.database() as conn:
            report = conn.execute(
                "SELECT ot,progress FROM reports WHERE id='IMMUTABLE-3'").fetchone()
        self.assertEqual(('450099', 50.0), tuple(report))

    def test_operational_fields_update_while_ot_stays_immutable(self):
        self.request({'body': {'value': [
            set_ot(row('IMMUTABLE-4', progress='20'), '450099')]}})
        updated = set_ot(row('IMMUTABLE-4', progress='50'), '450099')
        for key in tuple(updated):
            if '(Filtros)' not in key:
                continue
            if 'trabajadores' in key:
                updated[key] = '7'
            elif 'horas' in key:
                updated[key] = '9'
            elif 'reseña' in key:
                updated[key] = 'Reseña ficticia actualizada'
            elif 'fotografía' in key:
                updated[key] = f'https://example.invalid/actualizada/{key.split()[4].lower()}'
        self.request({'body': {'value': [updated]}})
        with app.database() as conn:
            report = conn.execute('''SELECT ot,progress,workers,hours,comment,photos
              FROM reports WHERE id='IMMUTABLE-4' ''').fetchone()
        self.assertEqual(('450099', 50.0, 7.0, 9.0, 'Reseña ficticia actualizada'),
                         tuple(report)[:5])
        self.assertTrue(all('/actualizada/' in photo for photo in json.loads(report['photos'])))

    def test_pending_response_records_its_first_valid_ot(self):
        status, result = self.request({'body': {'value': [
            without_ot(row('FIRST-OT'))]}})
        self.assertEqual((200, 1), (status, result['pending']))
        status, result = self.request({'body': {'value': [
            set_ot(row('FIRST-OT'), '450099')]}})
        self.assertEqual((200, 'success', 0),
                         (status, result['status'], result['discrepancies']))
        with app.database() as conn:
            report = conn.execute(
                "SELECT ot FROM reports WHERE id='FIRST-OT'").fetchone()
            pending_count = conn.execute(
                "SELECT count(*) FROM pending_reports WHERE id='FIRST-OT'").fetchone()[0]
        self.assertEqual('450099', report['ot'])
        self.assertEqual(0, pending_count)

    def test_missing_sync_token_fails_closed_without_breaking_dashboard(self):
        token = app.SYNC_TOKEN
        app.SYNC_TOKEN = ''
        try:
            status, result = self.request({'body': {'value': [row()]}}, token='unused')
        finally:
            app.SYNC_TOKEN = token
        self.assertEqual(503, status)
        self.assertIn('no configurada', result['error'])

    def test_status_exposes_last_sync_to_dashboard(self):
        self.request({'body': {'value': [row()]}})
        request = urllib.request.Request(self.base + '/api/status',
                                         headers={'Cookie': self.login_cookie()})
        with urllib.request.urlopen(request) as response:
            result = json.load(response)
        self.assertEqual('Power Automate', result['last_sync']['source'])
        self.assertEqual(0, result['last_sync']['errors'])

    def test_dashboard_requires_login_session(self):
        request = urllib.request.Request(self.base + '/api/status')
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request)
        self.assertEqual(401, raised.exception.code)

    def test_login_creates_session_and_logout_revokes_it(self):
        cookie = self.login_cookie()
        request = urllib.request.Request(self.base + '/api/status', headers={'Cookie': cookie})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(200, response.status)
        logout = urllib.request.Request(self.base + '/logout', data=b'', method='POST',
                                        headers={'Cookie': cookie})
        opener = urllib.request.build_opener(NoRedirect())
        with self.assertRaises(urllib.error.HTTPError) as redirected:
            opener.open(logout)
        self.assertEqual(303, redirected.exception.code)
        redirected.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as unauthorized:
            urllib.request.urlopen(request)
        self.assertEqual(401, unauthorized.exception.code)

    def test_admin_creates_user_with_hashed_password(self):
        admin = self.login_cookie()
        status, result = self.dashboard_api('/api/users', admin, {
            'action': 'create', 'username': 'supervisor.prueba',
            'password': 'clave-temporal-segura', 'role': 'supervisor'}, 'POST')
        self.assertEqual(200, status)
        self.assertIn('supervisor.prueba', [user['username'] for user in result['users']])
        with app.database() as conn:
            stored = conn.execute('''SELECT password_hash,role FROM users
              WHERE username='supervisor.prueba' ''').fetchone()
        self.assertEqual('supervisor', stored['role'])
        self.assertNotIn('clave-temporal-segura', stored['password_hash'])
        self.assertTrue(stored['password_hash'].startswith('pbkdf2_sha256$'))
        self.assertTrue(self.login_cookie('supervisor.prueba', 'clave-temporal-segura'))

    def test_non_admin_cannot_manage_users(self):
        admin = self.login_cookie()
        self.dashboard_api('/api/users', admin, {
            'action': 'create', 'username': 'solo.lectura',
            'password': 'clave-temporal-segura', 'role': 'viewer'}, 'POST')
        viewer = self.login_cookie('solo.lectura', 'clave-temporal-segura')
        status, result = self.dashboard_api('/api/users', viewer)
        self.assertEqual(403, status)
        self.assertIn('permisos', result['error'])
    def test_rejects_request_over_configured_size_limit(self):
        original = app.MAX_SYNC_BODY
        app.MAX_SYNC_BODY = 10
        try:
            status, result = self.request({'body': {'value': [row()]}})
        finally:
            app.MAX_SYNC_BODY = original
        self.assertEqual(413, status)
        self.assertIn('grande', result['error'])


if __name__ == '__main__':
    unittest.main()
