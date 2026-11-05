import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


ROOT = Path(__file__).resolve().parents[1]


class DeploymentConfigurationTest(unittest.TestCase):
    def test_compose_binds_local_2000_to_container_8080(self):
        compose = (ROOT / 'compose.yaml').read_text(encoding='utf-8')
        dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
        self.assertIn('"127.0.0.1:2000:8080"', compose)
        self.assertNotIn('127.0.0.1:8086:8080', compose)
        self.assertIn('PORT: 8080', compose)
        self.assertIn('EXPOSE 8080', dockerfile)
        self.assertIn('login.html', dockerfile)
        self.assertIn('users.html', dockerfile)
        self.assertIn('users.js', dockerfile)


class MigrationSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_db = app.DB
        app.DB = Path(self.temp.name) / 'legacy.db'
        conn = sqlite3.connect(app.DB)
        conn.execute('''CREATE TABLE reports (
          id TEXT PRIMARY KEY, start_at TEXT, submitted_at TEXT, reporter TEXT, email TEXT,
          area TEXT, shift TEXT, report_date TEXT, ot TEXT, activity TEXT,
          progress REAL, workers REAL, hours REAL, comment TEXT, photos TEXT,
          flags TEXT, imported_at TEXT)''')
        conn.execute('''CREATE TABLE sync_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, records INTEGER, source TEXT)''')
        conn.execute('''INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                     ('LEGACY-1', '2026-01-01', '2026-01-01', 'Persona ficticia',
                      'legacy@example.invalid', 'Filtros', 'DIA', '2026-01-01',
                      '450099', '10', 20, 4, 6, 'Registro ficticio previo',
                      json.dumps([]), json.dumps([]), '2026-01-01'))
        conn.execute('INSERT INTO sync_log(at,records,source) VALUES (?,?,?)',
                     ('2026-01-01', 1, 'Versión anterior'))
        conn.commit()
        conn.close()

    def tearDown(self):
        app.DB = self.previous_db
        self.temp.cleanup()

    def test_additive_migration_preserves_existing_sqlite_data(self):
        app.db_conn().close()
        with app.database() as conn:
            report = conn.execute(
                "SELECT id,ot,progress,comment FROM reports WHERE id='LEGACY-1'").fetchone()
            old_log = conn.execute(
                "SELECT records,source FROM sync_log WHERE source='Versión anterior'").fetchone()
            columns = {item['name'] for item in conn.execute('PRAGMA table_info(sync_log)')}
            tables = {item['name'] for item in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(('LEGACY-1', '450099', 20.0, 'Registro ficticio previo'),
                         tuple(report))
        self.assertEqual((1, 'Versión anterior'), tuple(old_log))
        self.assertTrue({'errors', 'detail', 'pending', 'discrepancies'} <= columns)
        self.assertTrue({'reports', 'sync_log', 'pending_reports',
                         'ot_discrepancies', 'users', 'sessions',
                         'login_attempts'} <= tables)


if __name__ == '__main__':
    unittest.main()
