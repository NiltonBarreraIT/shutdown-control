import base64
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import app


AREAS = ('Filtros', 'Molibdeno', 'Tranque', 'Flotación')


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


class SyncApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app.DB = Path(self.temp.name) / 'test.db'
        app.SYNC_TOKEN = 'token-de-prueba-no-real'
        app.USER = 'dashboard'
        app.PASSWORD = 'clave-de-prueba'
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
        auth = base64.b64encode(b'dashboard:clave-de-prueba').decode()
        request = urllib.request.Request(self.base + '/api/status',
                                         headers={'Authorization': 'Basic ' + auth})
        with urllib.request.urlopen(request) as response:
            result = json.load(response)
        self.assertEqual('Power Automate', result['last_sync']['source'])
        self.assertEqual(0, result['last_sync']['errors'])

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
