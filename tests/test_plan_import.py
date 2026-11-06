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
        self.assertEqual('Día', tasks[0]['shift'])
        with app.database() as conn:
            user_id = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        version_id = app.save_plan(tasks, 'gantt.xlsx', 'xlsx', user_id)
        with app.database() as conn:
            conn.execute('''INSERT INTO reports
              (id,submitted_at,ot,activity,progress,photos,flags)
              VALUES (?,?,?,?,?,?,?)''', ('HISTORY-1', '2026-11-05 10:00:00',
                                         '419433450', '020', 40, '[]', '[]'))
        result = app.plan_payload()
        self.assertEqual((version_id, 1, 1, 0),
                         (result['version']['id'], result['counts']['tasks'],
                          result['counts']['matched'], result['counts']['unmatched']))
        self.assertEqual(('419433450', '020', 40.0),
                         (result['history'][0]['ot'], result['history'][0]['activity'],
                          result['history'][0]['progress']))

    def test_detects_real_export_header_after_summary_and_allows_segments(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append([None, 'ACTIVIDADES E&I ITS POR DÍA Y ÁREA'])
        sheet.append([None, 'Shutdown nov-2026'])
        for _ in range(10):
            sheet.append([])
        sheet.append(['Inicio', 'Término', 'Turno', 'Jornada', 'Área', 'Sistema',
                      'Actividad (Gantt)', 'Equipo (TAG)', 'OT', 'Op.', 'Dot.',
                      'Dur (h)', 'HH', 'ID Gantt', 'Observación'])
        sheet.append(['2026-11-03 19:00', '2026-11-03 22:00', 'Día', '03-11 Día', 'Flotación',
                      '', 'Tramo T1', '', 419355062, '0020', 1, 3, 3, 2964, ''])
        sheet.append(['2026-11-04 05:00', '2026-11-04 08:00', 'Noche', '', 'Flotación',
                      '', 'Tramo T2', '', 419355062, '0020', 1, 3, 3, 2967, ''])
        sheet.append(['2026-11-04 09:00', '2026-11-04 10:00', 'Día', '', 'Molibdeno',
                      '', 'Actividad pendiente de identificación', '', None, None,
                      1, 1, 1, 3000, 'Sin OT ni operación en la Gantt'])
        output = io.BytesIO()
        workbook.save(output)
        tasks = app.parse_plan_xlsx(output.getvalue())
        self.assertEqual(3, len(tasks))
        self.assertEqual(('Tramo T1', '0020', 'Flotación'),
                         (tasks[0]['name'], tasks[0]['activity'], tasks[0]['area']))
        self.assertEqual(('Día', '03-11 Día'), (tasks[0]['shift'], tasks[0]['journey']))
        with app.database() as conn:
            user_id = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        app.save_plan(tasks, 'real.xlsx', 'xlsx', user_id)
        counts = app.plan_payload()['counts']
        self.assertEqual((3, 1), (counts['tasks'], counts['missing_ids']))

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

    def test_infers_shift_for_legacy_tasks_without_turno(self):
        night = app.plan_task({'name': 'Tarea noche', 'start': '2026-11-03 04:00'})
        day = app.plan_task({'name': 'Tarea día', 'start': '2026-11-03 13:00'})
        explicit = app.plan_task({'name': 'Excepción', 'start': '2026-11-03 13:00',
                                  'shift': 'Noche'})
        self.assertEqual(('Noche', 'Día', 'Noche'),
                         (night['shift'], day['shift'], explicit['shift']))


if __name__ == '__main__':
    unittest.main()
