import io
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

import app


class PlanImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = (app.DB, app.USER, app.PASSWORD)
        app.DB = Path(self.temp.name) / 'plan.db'
        app.USER, app.PASSWORD = 'admin', 'clave-segura-prueba'
        app.db_conn().close()

    def tearDown(self):
        app.DB, app.USER, app.PASSWORD = self.previous
        self.temp.cleanup()

    def test_imports_excel_plan_and_versions_it(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['WBS', 'OT', 'OP', 'Nombre de tarea', 'Área', 'Inicio', 'Fin', 'Peso'])
        sheet.append(['1.1', '419433450', '020', 'Cambio de componente', 'Flotación',
                      '2026-11-05 08:00', '2026-11-05 12:00', 10])
        output = io.BytesIO()
        workbook.save(output)
        tasks = app.parse_plan_xlsx(output.getvalue())
        self.assertEqual(('419433450', '020', 'Cambio de componente'),
                         (tasks[0]['ot'], tasks[0]['activity'], tasks[0]['name']))
        with app.database() as conn:
            user_id = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        version_id = app.save_plan(tasks, 'gantt.xlsx', 'xlsx', user_id)
        result = app.plan_payload()
        self.assertEqual((version_id, 1, 0, 1),
                         (result['version']['id'], result['counts']['tasks'],
                          result['counts']['matched'], result['counts']['unmatched']))

    def test_imports_project_xml_custom_fields_and_name_fallback(self):
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
        <Project xmlns="http://schemas.microsoft.com/project">
          <ExtendedAttributes>
            <ExtendedAttribute><FieldID>188743731</FieldID><Alias>OT</Alias></ExtendedAttribute>
            <ExtendedAttribute><FieldID>188743732</FieldID><Alias>OP</Alias></ExtendedAttribute>
          </ExtendedAttributes>
          <Tasks>
            <Task><UID>1</UID><WBS>1.1</WBS><Name>Tarea principal</Name>
              <Start>2026-11-05T08:00:00</Start><Finish>2026-11-05T12:00:00</Finish>
              <ExtendedAttribute><FieldID>188743731</FieldID><Value>419433450</Value></ExtendedAttribute>
              <ExtendedAttribute><FieldID>188743732</FieldID><Value>020</Value></ExtendedAttribute>
            </Task>
            <Task><UID>2</UID><WBS>1.2</WBS><Name>OT 419433451 - OP 030 Inspeccion</Name></Task>
          </Tasks>
        </Project>'''
        tasks = app.parse_plan_xml(xml)
        self.assertEqual([('419433450', '020'), ('419433451', '030')],
                         [(task['ot'], task['activity']) for task in tasks])

    def test_rejects_duplicate_ot_activity(self):
        task = app.plan_task({'ot': '1', 'activity': '020', 'name': 'Tarea'})
        with app.database() as conn:
            user_id = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        with self.assertRaisesRegex(ValueError, 'duplicadas'):
            app.save_plan([task, dict(task)], 'duplicado.xlsx', 'xlsx', user_id)


if __name__ == '__main__':
    unittest.main()
