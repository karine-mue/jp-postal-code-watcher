"""Unit tests for watch.py. Run: python3 test_watch.py"""
import unittest

from watch import (
    APPLIERS,
    REASON_LABELS,
    check_new_data_sane,
    diff_snapshots,
    main,
    parse_csv_text,
    record_key,
    reconcile,
    update_meta,
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


class TestSnapshotDirectory(unittest.TestCase):
    def test_load_and_write_parts(self):
        import os
        import tempfile
        from watch import load_snapshot, write_snapshot
        text = row() + "\n" + row(postal="1000002", tn="丸の内", tk="マルノウチ") + "\n"
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "new.csv")
            with open(src, "w", encoding="utf-8") as f:
                f.write(text)
            snap_dir = os.path.join(d, "snapshot")
            write_snapshot(src, snap_dir)
            parts = sorted(os.listdir(snap_dir))
            self.assertEqual(parts, ["part-001.csv"])
            recs = load_snapshot(snap_dir)
            self.assertEqual(len(recs), 2)
            self.assertEqual(recs[1]["town_name"], "丸の内")


class TestAppliers(unittest.TestCase):
    def test_none_applier_registered(self):
        self.assertIn("none", APPLIERS)
        APPLIERS["none"]().apply([])  # must not raise


class TestReviewFixes(unittest.TestCase):
    """Fixes from the 2026-11-15 Claude review."""

    def test_empty_new_data_refused(self):
        old = parse_csv_text(row() + "\n")
        with self.assertRaises(ValueError):
            check_new_data_sane(old, [])

    def test_shrunken_new_data_refused(self):
        old = parse_csv_text(row() + "\n" + row(postal="1000002") + "\n"
                             + row(postal="1000003") + "\n" + row(postal="1000004") + "\n")
        new = parse_csv_text(row() + "\n")
        with self.assertRaises(ValueError):
            check_new_data_sane(old, new)

    def test_normal_change_passes_guard(self):
        old = parse_csv_text(row() + "\n" + row(postal="1000002") + "\n")
        new = parse_csv_text(row() + "\n" + row(postal="1000003") + "\n")
        check_new_data_sane(old, new)  # must not raise

    def test_duplicate_keys_detected(self):
        dup = parse_csv_text(row() + "\n" + row(upd="1", reason="5") + "\n")
        # Same key (cols 1-13) twice: parse keeps both, diff must refuse
        self.assertEqual(len(dup), 2)
        with self.assertRaises(ValueError):
            diff_snapshots(dup, dup, "2026-11-01")

    def test_event_order_deterministic(self):
        # Two towns sharing postal code / jis / town_kana, differing in
        # town_name: order must not depend on input order.
        a = row(tn="千代田一", tk="チヨダ")
        b = row(tn="千代田二", tk="チヨダ")
        old = parse_csv_text(row(postal="1000009", tn="旧町", tk="キュウ") + "\n")
        ev1 = diff_snapshots(old, parse_csv_text(a + "\n" + b + "\n"), "2026-11-01")
        ev2 = diff_snapshots(old, parse_csv_text(b + "\n" + a + "\n"), "2026-11-01")
        self.assertEqual([e["town_name"] for e in ev1], [e["town_name"] for e in ev2])

    def test_main_empty_new_exits_without_wiping_snapshot(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            snap = os.path.join(d, "snap.csv")
            with open(snap, "w", encoding="utf-8") as f:
                f.write(row() + "\n")
            empty = os.path.join(d, "empty.csv")
            with open(empty, "w", encoding="utf-8") as f:
                f.write("")
            with self.assertRaises(SystemExit):
                main(["check", "--snapshot", snap, "--new", empty,
                      "--date", "2026-11-01", "--update-snapshot"])
            with open(snap, encoding="utf-8") as f:
                self.assertIn("1000001", f.read())  # snapshot untouched

    def test_main_one_sided_official_args_rejected(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            snap = os.path.join(d, "snap.csv")
            new = os.path.join(d, "new.csv")
            for p in (snap, new):
                with open(p, "w", encoding="utf-8") as f:
                    f.write(row() + "\n")
            with self.assertRaises(SystemExit):
                main(["check", "--snapshot", snap, "--new", new,
                      "--date", "2026-11-01", "--official-add", new])

    def test_update_meta(self):
        import hashlib
        import json
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            csv_path = os.path.join(d, "new.csv")
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write(row() + "\n")
            meta_path = os.path.join(d, "meta.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump({"custom": "keep"}, f)
            update_meta(meta_path, source="https://example.invalid/x.zip",
                        data_as_of="2026-11-01", rows=1, csv_path=csv_path)
            with open(meta_path, encoding="utf-8") as f:
                meta = json.loads(f.read())
            self.assertEqual(meta["rows"], 1)
            self.assertEqual(meta["data_as_of"], "2026-11-01")
            self.assertEqual(meta["custom"], "keep")
            with open(csv_path, "rb") as f:
                self.assertEqual(meta["sha256"], hashlib.sha256(f.read()).hexdigest())


if __name__ == "__main__":
    unittest.main(verbosity=2)
