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
        self.assertIn('plan.html', dockerfile)
        self.assertIn('plan.js', dockerfile)
        self.assertIn('progress_model.py', dockerfile)

    def test_modules_share_one_compact_navigation(self):
        dashboard = (ROOT / 'index.html').read_text(encoding='utf-8')
        users = (ROOT / 'users.html').read_text(encoding='utf-8')
        for page in (dashboard, users):
            self.assertIn('PLATAFORMA · BHP SPENCE', page)
            self.assertIn('Centro operacional', page)
            self.assertIn('Usuarios', page)
            self.assertIn('Planificación y Gantt', page)
            self.assertIn('Sincronización operativa', page)
        self.assertNotIn('Vista general</a>', dashboard)
        self.assertNotIn('Órdenes de trabajo</a>', dashboard)
        self.assertNotIn('Bitácora técnica</a>', dashboard)

    def test_mobile_layout_keeps_navigation_visible(self):
        stylesheet = (ROOT / 'style.css').read_text(encoding='utf-8')
        self.assertIn('@media(max-width:700px)', stylesheet)
        self.assertIn('.sidebar nav{display:flex', stylesheet)
        self.assertIn('.table-scroll table{min-width:760px}', stylesheet)
        self.assertIn('@media(max-width:420px)', stylesheet)

    def test_plan_phase_one_exposes_operational_controls(self):
        page = (ROOT / 'plan.html').read_text(encoding='utf-8')
        script = (ROOT / 'plan.js').read_text(encoding='utf-8')
        stylesheet = (ROOT / 'style.css').read_text(encoding='utf-8')
        for element_id in ('plan-overdue', 'plan-active', 'plan-upcoming',
                           'plan-completed', 'plan-area', 'plan-shift',
                           'plan-state', 'plan-date'):
            self.assertIn(f'id="{element_id}"', page)
        self.assertIn('<details id="plan-import"', page)
        self.assertIn('isOverdue', script)
        self.assertIn('isUpcoming', script)
        self.assertIn('Sin reporte iniciado', script)
        self.assertIn('.filters.plan-filters input{min-width:0', stylesheet)

    def test_plan_phase_two_exposes_plan_vs_actual(self):
        page = (ROOT / 'plan.html').read_text(encoding='utf-8')
        script = (ROOT / 'plan.js').read_text(encoding='utf-8')
        for element_id in ('plan-planned', 'plan-actual', 'plan-variance',
                           'plan-forecast', 'plan-health'):
            self.assertIn(f'id="{element_id}"', page)
        for calculation in ('plannedProgress', 'programMetrics', 'comparisonState'):
            self.assertIn(calculation, script)
        self.assertIn('Cálculo backend acumulado por OT/OP', page)

    def test_plan_phase_three_exposes_executive_curve(self):
        page = (ROOT / 'plan.html').read_text(encoding='utf-8')
        script = (ROOT / 'plan.js').read_text(encoding='utf-8')
        for element_id in ('plan-curve', 'plan-spi', 'plan-risk-area',
                           'plan-lookahead', 'plan-area-table'):
            self.assertIn(f'id="{element_id}"', page)
        self.assertIn('curveSeries', script)
        self.assertIn('actualAt', script)
        self.assertIn('Curva S y control ejecutivo', page)


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
            plan_columns = {item['name'] for item in conn.execute('PRAGMA table_info(plan_tasks)')}
            tables = {item['name'] for item in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(('LEGACY-1', '450099', 20.0, 'Registro ficticio previo'),
                         tuple(report))
        self.assertEqual((1, 'Versión anterior'), tuple(old_log))
        self.assertTrue({'errors', 'detail', 'pending', 'discrepancies', 'conflicts'} <= columns)
        self.assertTrue({'shift', 'journey', 'planned_hh'} <= plan_columns)
        self.assertTrue({'reports', 'sync_log', 'pending_reports',
                         'ot_discrepancies', 'users', 'sessions',
                         'login_attempts', 'plan_versions', 'plan_tasks',
                         'report_conflicts', 'operation_anomalies'} <= tables)


if __name__ == '__main__':
    unittest.main()
