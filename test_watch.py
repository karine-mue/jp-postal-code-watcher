"""Unit tests for watch.py. Run: python3 test_watch.py"""
import unittest

from watch import (
    APPLIERS,
    REASON_LABELS,
    diff_snapshots,
    parse_csv_text,
    record_key,
    reconcile,
)


def row(jis="13101", old="100  ", postal="1000001", pk="トウキョウト",
        ck="チヨダク", tk="チヨダ", pn="東京都", cn="千代田区", tn="千代田",
        f1="0", f2="0", f3="0", f4="0", upd="0", reason="0"):
    return ",".join(f'"{v}"' for v in
                    [jis, old, postal, pk, ck, tk, pn, cn, tn,
                     f1, f2, f3, f4, upd, reason])


class TestParseAndKey(unittest.TestCase):
    def test_parse_fields(self):
        recs = parse_csv_text(row() + "\n")
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["jis_code"], "13101")
        self.assertEqual(r["postal_code"], "1000001")
        self.assertEqual(r["town_name"], "千代田")
        self.assertEqual(r["update_flag"], "0")

    def test_key_ignores_update_flag_and_reason(self):
        a = parse_csv_text(row(upd="0", reason="0") + "\n")[0]
        b = parse_csv_text(row(upd="1", reason="5") + "\n")[0]
        self.assertEqual(record_key(a), record_key(b))

    def test_key_reacts_to_flag_change(self):
        a = parse_csv_text(row(f4="0") + "\n")[0]
        b = parse_csv_text(row(f4="1") + "\n")[0]
        self.assertNotEqual(record_key(a), record_key(b))


class TestDiff(unittest.TestCase):
    def test_no_change(self):
        old = parse_csv_text(row() + "\n")
        new = parse_csv_text(row(upd="1", reason="5") + "\n")
        self.assertEqual(diff_snapshots(old, new, "2026-11-01"), [])

    def test_added_with_reason(self):
        old = parse_csv_text(row() + "\n")
        new = parse_csv_text(row() + "\n" + row(postal="1000002", tn="丸の内",
                                                tk="マルノウチ", upd="1", reason="2") + "\n")
        events = diff_snapshots(old, new, "2026-11-01")
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e["change_type"], "added")
        self.assertEqual(e["postal_code"], "1000002")
        self.assertEqual(e["reason_code"], 2)
        self.assertEqual(e["reason_label"], "住居表示の実施")
        self.assertEqual(e["date"], "2026-11-01")

    def test_removed_reason_unknown_without_official(self):
        old = parse_csv_text(row() + "\n" + row(postal="1000002", tn="丸の内", tk="マルノウチ") + "\n")
        new = parse_csv_text(row() + "\n")
        events = diff_snapshots(old, new, "2026-11-01")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["change_type"], "removed")
        self.assertIsNone(events[0]["reason_code"])

    def test_replacement_is_removed_plus_added(self):
        old = parse_csv_text(row(f4="0") + "\n")
        new = parse_csv_text(row(f4="1", upd="1", reason="3") + "\n")
        events = diff_snapshots(old, new, "2026-11-01")
        self.assertEqual(sorted(e["change_type"] for e in events), ["added", "removed"])
        self.assertTrue(all(e["postal_code"] == "1000001" for e in events))


class TestReconcile(unittest.TestCase):
    def test_reconcile_fills_removed_reason_and_matches(self):
        old = parse_csv_text(row() + "\n" + row(postal="1000002", tn="丸の内", tk="マルノウチ") + "\n")
        new = parse_csv_text(row() + "\n" + row(postal="1000003", tn="大手町",
                                                tk="オオテマチ", upd="1", reason="5") + "\n")
        events = diff_snapshots(old, new, "2026-11-01")
        official_add = parse_csv_text(row(postal="1000003", tn="大手町", tk="オオテマチ",
                                          upd="1", reason="5") + "\n")
        official_del = parse_csv_text(row(postal="1000002", tn="丸の内", tk="マルノウチ",
                                          upd="2", reason="6") + "\n")
        report = reconcile(events, official_add, official_del)
        self.assertTrue(report["added_match"])
        self.assertTrue(report["removed_match"])
        removed = [e for e in events if e["change_type"] == "removed"][0]
        self.assertEqual(removed["reason_code"], 6)
        self.assertEqual(removed["reason_label"], REASON_LABELS[6])

    def test_reconcile_detects_mismatch(self):
        old = parse_csv_text(row() + "\n")
        new = parse_csv_text(row() + "\n" + row(postal="1000009", tn="大手町",
                                                tk="オオテマチ", upd="1", reason="5") + "\n")
        events = diff_snapshots(old, new, "2026-11-01")
        report = reconcile(events, [], [])
        self.assertFalse(report["added_match"])
        self.assertEqual(report["added_diff_only"], 1)


class TestLoadRecords(unittest.TestCase):
    def test_load_plain_and_gzip(self):
        import gzip
        import os
        import tempfile
        text = row() + "\n"
        with tempfile.TemporaryDirectory() as d:
            plain = os.path.join(d, "snap.csv")
            with open(plain, "w", encoding="utf-8") as f:
                f.write(text)
            zipped = os.path.join(d, "snap.csv.gz")
            with gzip.open(zipped, "wt", encoding="utf-8") as f:
                f.write(text)
            from watch import load_records
            self.assertEqual(load_records(plain), load_records(zipped))
            self.assertEqual(len(load_records(zipped)), 1)


class TestAppliers(unittest.TestCase):
    def test_none_applier_registered(self):
        self.assertIn("none", APPLIERS)
        APPLIERS["none"]().apply([])  # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
