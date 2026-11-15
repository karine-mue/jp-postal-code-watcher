"""Integration tests against the real Japan Post files.

Expects data/utf_ken_all.csv, data/utf_add_2610.csv, data/utf_del_2610.csv
(2026年10月30日更新分). Skipped when the files are absent.
Run: python3 test_real_data.py
"""
import os
import unittest

from watch import diff_snapshots, load_records, record_key, reconcile

DATA = os.path.join(os.path.dirname(__file__), "data")
KEN_ALL = os.path.join(DATA, "utf_ken_all.csv")
ADD = os.path.join(DATA, "utf_add_2610.csv")
DEL = os.path.join(DATA, "utf_del_2610.csv")
HAVE = all(os.path.exists(p) for p in (KEN_ALL, ADD, DEL))


@unittest.skipUnless(HAVE, "real data files not present in data/")
class TestRealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current = load_records(KEN_ALL)
        cls.official_add = load_records(ADD)
        cls.official_del = load_records(DEL)
        add_keys = {record_key(r) for r in cls.official_add}
        cur_keys = {record_key(r) for r in cls.current}
        # Reconstruct the September snapshot: current - added + deleted
        cls.september = [r for r in cls.current if record_key(r) not in add_keys]
        cls.september += [r for r in cls.official_del
                          if record_key(r) not in cur_keys]

    def test_row_counts(self):
        self.assertEqual(len(self.current), 124527)
        self.assertEqual(len(self.official_add), 6)
        self.assertEqual(len(self.official_del), 5)

    def test_keys_unique(self):
        keys = [record_key(r) for r in self.current]
        self.assertEqual(len(keys), len(set(keys)))

    def test_self_diff_is_empty(self):
        self.assertEqual(diff_snapshots(self.current, self.current, "2026-10-30"), [])

    def test_diff_matches_official(self):
        events = diff_snapshots(self.september, self.current, "2026-10-30")
        added = {record_key(e["record"]) for e in events if e["change_type"] == "added"}
        removed = {record_key(e["record"]) for e in events if e["change_type"] == "removed"}
        self.assertEqual(added, {record_key(r) for r in self.official_add})
        self.assertEqual(removed, {record_key(r) for r in self.official_del})

    def test_reconcile_with_official(self):
        events = diff_snapshots(self.september, self.current, "2026-10-30")
        report = reconcile(events, self.official_add, self.official_del)
        self.assertTrue(report["added_match"])
        self.assertTrue(report["removed_match"])
        removed = [e for e in events if e["change_type"] == "removed"]
        self.assertTrue(all(e["reason_code"] == 6 for e in removed))


if __name__ == "__main__":
    unittest.main(verbosity=2)
