import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendSearchTest(unittest.TestCase):
    def test_search_index_includes_visible_ot_and_op_prefixes(self):
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        self.assertIn("`OT ${r.ot??''}`", javascript)
        self.assertIn("`OP ${r.activity??''}`", javascript)
        self.assertIn('searchText(r).includes(q)', javascript)

    def test_work_order_rows_include_mobile_card_labels(self):
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        stylesheet = (ROOT / 'style.css').read_text(encoding='utf-8')
        for label in ('Orden / actividad', 'Área / turno', 'Avance', 'Responsable', 'Estado'):
            self.assertIn(f'data-label="{label}"', javascript)
        self.assertIn('#orders #ot-table tr{', stylesheet)
        self.assertIn('content:attr(data-label)', stylesheet)
        self.assertIn('#orders thead{display:none}', stylesheet)


if __name__ == '__main__':
    unittest.main()
