import datetime as dt
import unittest

import app


class DateTextTest(unittest.TestCase):
    def test_converts_excel_serial_datetime(self):
        self.assertEqual(app.date_text(46331.1761689815), '2026-11-05 04:13:41')

    def test_preserves_iso_datetime(self):
        value = dt.datetime(2026, 11, 5, 4, 13)
        self.assertEqual(app.date_text(value), '2026-11-05 04:13:00')

    def test_does_not_convert_arbitrary_numeric_text(self):
        self.assertEqual(app.date_text('0'), '0')
