import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendSearchTest(unittest.TestCase):
    def test_search_index_includes_visible_ot_and_op_prefixes(self):
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        self.assertIn("`OT ${r.ot??''}`", javascript)
        self.assertIn("`OP ${r.activity??''}`", javascript)
        self.assertIn('searchText(r).includes(q)', javascript)


if __name__ == '__main__':
    unittest.main()
