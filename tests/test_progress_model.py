import datetime as dt
import unittest

from progress_model import CHILE, curve, projections, summarize


def task(ot='100', op='0010', planned_hh=10, duration=10):
    return {'ot': ot, 'activity': op, 'name': f'OP {op}', 'area': 'Flotación',
            'shift': 'Día', 'start_at': '2026-11-05 08:00:00',
            'finish_at': '2026-11-05 18:00:00', 'planned_hh': planned_hh,
            'duration_hours': duration}


def report(report_id, progress, op='0010', submitted='2026-11-05 10:00:00',
           workers=2, hours=3):
    return {'id': report_id, 'ot': '100', 'activity': op, 'progress': progress,
            'submitted_at': submitted, 'report_date': submitted[:10],
            'workers': workers, 'hours': hours}


class ProgressModelTest(unittest.TestCase):
    cutoff = dt.datetime(2026, 11, 5, 18, tzinfo=CHILE)

    def test_case_a_accumulates_20_plus_80_to_100(self):
        result = summarize([task()], [report('A1', 20), report('A2', 80)], self.cutoff)
        self.assertEqual((100, 100, 2),
                         (result['actual'], result['operations'][0]['raw_actual'],
                          result['operations'][0]['report_count']))

    def test_case_b_duplicate_source_id_is_counted_once(self):
        duplicate = report('B1', 20)
        result = summarize([task()], [duplicate, dict(duplicate)], self.cutoff)
        self.assertEqual((20, 1), (result['actual'], result['operations'][0]['report_count']))

    def test_case_c_keeps_operations_independent(self):
        tasks = [task(op='0010'), task(op='0020')]
        reports = [report('C1', 50, '0010'), report('C2', 30, '0020')]
        result = summarize(tasks, reports, self.cutoff)
        values = {item['op']: item['actual'] for item in result['operations']}
        self.assertEqual({'0010': 50, '0020': 30}, values)

    def test_case_d_caps_display_and_preserves_120_anomaly(self):
        result = summarize([task()], [report('D1', 70), report('D2', 50)], self.cutoff)
        operation = result['operations'][0]
        self.assertEqual((100, 120, True, 1),
                         (operation['actual'], operation['raw_actual'], operation['over_100'],
                          result['counts']['over_100_ops']))

    def test_case_e_historical_cutoff_excludes_future_report(self):
        reports = [report('E1', 20, submitted='2026-11-05 09:00:00'),
                   report('E2', 80, submitted='2026-11-06 09:00:00')]
        points = curve([task()], reports, dt.datetime(2026, 11, 6, 10, tzinfo=CHILE))
        before = next(item for item in points if item['cutoff'].startswith('2026-11-05T08:00'))
        after = next(item for item in points if item['cutoff'].startswith('2026-11-06T08:00'))
        self.assertEqual((0, 20), (before['actual'], after['actual']))

    def test_case_f_weights_operations_by_planned_hh(self):
        tasks = [task(op='0010', planned_hh=90), task(op='0020', planned_hh=10)]
        reports = [report('F1', 100, '0010'), report('F2', 0, '0020')]
        result = summarize(tasks, reports, self.cutoff)
        self.assertEqual('planned_hh', result['method'])
        self.assertEqual(90, result['actual'])
        self.assertEqual(100, result['planned_hh'])

    def test_predictive_scenarios_are_ordered_and_documented(self):
        points = [
            {'cutoff': '2026-11-04T08:00:00-03:00', 'actual': 20},
            {'cutoff': '2026-11-05T08:00:00-03:00', 'actual': 30},
            {'cutoff': '2026-11-06T08:00:00-03:00', 'actual': 40},
        ]
        result = projections(points, '2026-11-10 08:00:00')
        self.assertLess(result['optimistic'], result['base'])
        self.assertLess(result['base'], result['conservative'])
        self.assertIn('últimos 3 cortes', result['assumption'])


if __name__ == '__main__':
    unittest.main()
