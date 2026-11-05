import base64
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import app


def row(record_id='101', progress='25'):
    return {
        'Id': record_id,
        'Hora de inicio': '2026-11-05T08:00:00Z',
        'Hora de finalización': '2026-11-05T08:10:00Z',
        'Correo electrónico': 'persona@example.invalid',
        'Nombre': 'Persona de prueba',
        'Área': 'Filtros',
        'Turno Filtros': 'DIA',
        'Fecha Filtros': '2026-11-05',
        'Número de OT Filtros': '450001',
        'Actividad Filtros': '10',
        'Avance Filtros': progress,
        'Trabajadores Filtros': '4',
        'Horas Filtros': '6',
        'Comentario Filtros': 'Dato ficticio',
    }


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

    def test_rejects_bad_structure_and_records_error(self):
        status, result = self.request({'value': [row()]})
        self.assertEqual(400, status)
        self.assertIn('body.value', result['error'])
        with app.database() as conn:
            log = conn.execute('SELECT records,errors FROM sync_log').fetchone()
            self.assertEqual((0, 1), tuple(log))

    def test_reports_invalid_rows_without_discarding_valid_ones(self):
        invalid = row('102')
        invalid['Área'] = 'Otra'
        status, result = self.request({'body': {'value': [row(), invalid]}})
        self.assertEqual(200, status)
        self.assertEqual((2, 1, 1), (result['processed'], result['upserted'], len(result['errors'])))

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
