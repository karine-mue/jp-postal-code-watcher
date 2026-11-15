import argparse
import csv
import datetime
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

FIELDS = [
    "jis_code",
    "old_code",
    "postal_code",
    "prefecture_kana",
    "city_kana",
    "town_kana",
    "prefecture_name",
    "city_name",
    "town_name",
    "flag_multi_postal",
    "flag_koaza",
    "flag_chome",
    "flag_multi_town",
    "update_flag",
    "reason_code",
]

KEY_FIELDS = FIELDS[:13]

REASON_LABELS = {
    0: "変更なし",
    1: "市政・区政・町政・分区・政令指定都市施行",
    2: "住居表示の実施",
    3: "区画整理",
    4: "郵便区調整等",
    5: "訂正",
    6: "廃止",
}

def parse_csv_text(text: str) -> list[dict]:
    records = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row or (len(row) == 1 and row[0].strip() == ""):
            continue
        if len(row) != 15:
            raise ValueError(f"Expected 15 fields, got {len(row)}")
        records.append(dict(zip(FIELDS, row)))
    return records

def load_records(path) -> list[dict]:
    if str(path).endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8-sig") as f:
            return parse_csv_text(f.read())
    text = Path(path).read_text(encoding="utf-8-sig")
    return parse_csv_text(text)

def load_records_any(path, tmp_dir) -> list[dict]:
    """Load records from a .csv / .csv.gz path or a .zip holding one CSV."""
    if str(path).lower().endswith(".zip"):
        return load_records(_extract_single_csv(path, tmp_dir))
    return load_records(path)

def load_snapshot(path) -> list[dict]:
    p = Path(path)
    if p.is_dir():
        # Managed snapshots are part-*.csv; fall back to any *.csv only
        # when no part files exist, so stray CSVs are not read silently.
        parts = sorted(p.glob("part-*.csv")) or sorted(p.glob("*.csv"))
        records = []
        for part in parts:
            records.extend(load_records(part))
        return records
    return load_records(path)

SNAPSHOT_PART_LINES = 20000

def write_snapshot(source_csv_path, snapshot_path) -> None:
    target = Path(snapshot_path)
    if target.is_dir() or (not target.exists() and not str(snapshot_path).endswith((".csv", ".gz"))):
        target.mkdir(parents=True, exist_ok=True)
        # Split on "\n" only: str.splitlines() also breaks on \r, \x0b,
        # \u2028 etc., which would corrupt quoted CSV fields on round-trip.
        text = Path(source_csv_path).read_text(encoding="utf-8")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        chunks = [lines[i:i + SNAPSHOT_PART_LINES] for i in range(0, len(lines), SNAPSHOT_PART_LINES)] or [[]]
        for idx, chunk in enumerate(chunks, start=1):
            (target / f"part-{idx:03d}.csv").write_text(
                "".join(line + "\n" for line in chunk), encoding="utf-8")
        for stale in sorted(target.glob("part-*.csv")):
            m = re.fullmatch(r"part-(\d+)\.csv", stale.name)
            if m and int(m.group(1)) > len(chunks):
                stale.unlink()
    elif str(snapshot_path).endswith(".gz"):
        with open(source_csv_path, "rb") as src, gzip.open(snapshot_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
    else:
        shutil.copyfile(source_csv_path, snapshot_path)

def record_key(record: dict) -> tuple:
    return tuple(record[f] for f in KEY_FIELDS)

def _parse_reason(val: str) -> tuple[int | None, str | None]:
    if isinstance(val, str) and val.isdigit():
        code = int(val)
        return code, REASON_LABELS.get(code)
    return None, None

def check_new_data_sane(old_records, new_records) -> None:
    """Refuse to diff against an empty or drastically shrunken download.

    Without this guard, a failed or truncated download (0 records) turns
    into "everything removed" and --update-snapshot would wipe the stored
    snapshot. Postal data never legitimately loses half its records in a
    month, so a drop below 50% of the snapshot is treated as corruption.
    """
    if not new_records:
        raise ValueError(
            "New data has 0 records; refusing to diff (would report every "
            "record as removed)"
        )
    if old_records and len(new_records) * 2 < len(old_records):
        raise ValueError(
            f"New data has {len(new_records)} records but the snapshot has "
            f"{len(old_records)}; refusing to diff (possible truncated download)"
        )

def diff_snapshots(old_records, new_records, date: str) -> list[dict]:
    old_map = {record_key(r): r for r in old_records}
    new_map = {record_key(r): r for r in new_records}
    if len(old_map) != len(old_records):
        raise ValueError("Duplicate record keys in the snapshot")
    if len(new_map) != len(new_records):
        raise ValueError("Duplicate record keys in the new data")
    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())
    added_keys = new_keys - old_keys
    removed_keys = old_keys - new_keys
    events = []
    for key in added_keys:
        src = new_map[key]
        reason_code, reason_label = _parse_reason(src["reason_code"])
        events.append(
            {
                "date": date,
                "change_type": "added",
                "jis_code": src["jis_code"],
                "postal_code": src["postal_code"],
                "prefecture_name": src["prefecture_name"],
                "city_name": src["city_name"],
                "town_name": src["town_name"],
                "town_kana": src["town_kana"],
                "reason_code": reason_code,
                "reason_label": reason_label,
                "record": {f: src[f] for f in KEY_FIELDS},
            }
        )
    for key in removed_keys:
        src = old_map[key]
        events.append(
            {
                "date": date,
                "change_type": "removed",
                "jis_code": src["jis_code"],
                "postal_code": src["postal_code"],
                "prefecture_name": src["prefecture_name"],
                "city_name": src["city_name"],
                "town_name": src["town_name"],
                "town_kana": src["town_kana"],
                "reason_code": None,
                "reason_label": None,
                "record": {f: src[f] for f in KEY_FIELDS},
            }
        )
    # The full record key is the final tiebreaker so the output order is
    # fully deterministic (set iteration order depends on PYTHONHASHSEED).
    events.sort(key=lambda e: (e["postal_code"], e["jis_code"], e["town_kana"],
                               e["change_type"], record_key(e["record"])))
    return events

def reconcile(events, official_add_records, official_del_records) -> dict:
    diff_add_keys = {record_key(ev["record"]) for ev in events if ev["change_type"] == "added"}
    diff_del_keys = {record_key(ev["record"]) for ev in events if ev["change_type"] == "removed"}
    off_add_keys = {record_key(r) for r in official_add_records}
    off_del_keys = {record_key(r) for r in official_del_records}
    off_del_map = {record_key(r): r for r in official_del_records}
    for ev in events:
        if ev["change_type"] != "removed":
            continue
        key = record_key(ev["record"])
        if key in off_del_map:
            del_rec = off_del_map[key]
            code, label = _parse_reason(del_rec["reason_code"])
            ev["reason_code"] = code
            ev["reason_label"] = label
    return {
        "added_match": diff_add_keys == off_add_keys,
        "removed_match": diff_del_keys == off_del_keys,
        "added_diff_only": len(diff_add_keys - off_add_keys),
        "added_official_only": len(off_add_keys - diff_add_keys),
        "removed_diff_only": len(diff_del_keys - off_del_keys),
        "removed_official_only": len(off_del_keys - diff_del_keys),
    }

class Applier:
    def apply(self, events) -> None:
        raise NotImplementedError

class NullApplier(Applier):
    def apply(self, events) -> None:
        pass

APPLIERS = {"none": NullApplier}

def _extract_single_csv(zip_path: str | Path, dest_dir: str | Path) -> str:
    dest_dir = Path(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [
            name
            for name in zf.namelist()
            if name.lower().endswith(".csv") and not name.startswith("__MACOSX/")
        ]
        if len(csv_names) != 1:
            raise ValueError(f"Expected exactly one .csv in zip, found {len(csv_names)}")
        csv_name = csv_names[0]
        extracted = zf.extract(csv_name, path=dest_dir)
        return str(extracted)

def fetch_latest(url: str, dest_dir: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, "download.zip")
    req = urllib.request.Request(url, headers={"User-Agent": "jp-postal-code-watcher"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(zip_path, "wb") as out:
        shutil.copyfileobj(resp, out)
    return _extract_single_csv(zip_path, dest_dir)

def update_meta(meta_path, *, source, data_as_of, rows, csv_path) -> None:
    """Update data/meta.json for the snapshot that was just stored.

    Existing keys not managed here are preserved. The sha256 is taken over
    the full uncompressed CSV, matching how the file was first recorded.
    """
    p = Path(meta_path)
    meta: dict = {}
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except (ValueError, OSError):
            meta = {}
    h = hashlib.sha256()
    with open(csv_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    meta.update(
        {
            "source_url": source,
            "data_as_of": data_as_of,
            "fetched_on": datetime.date.today().isoformat(),
            "rows": rows,
            "sha256": h.hexdigest(),
        }
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="watch")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--snapshot", required=True)
    check_parser.add_argument("--new", required=True)
    check_parser.add_argument("--date")
    check_parser.add_argument("--events-json")
    check_parser.add_argument("--events-csv")
    check_parser.add_argument("--official-add")
    check_parser.add_argument("--official-del")
    check_parser.add_argument("--update-snapshot", action="store_true")
    check_parser.add_argument("--meta")
    check_parser.add_argument("--apply", default="none")
    args = parser.parse_args(argv)
    if args.subcommand == "check":
        if args.apply not in APPLIERS:
            available = ", ".join(sorted(APPLIERS.keys()))
            raise SystemExit(
                f"Unknown applier '{args.apply}'. Available appliers: {available}"
            )
        if bool(args.official_add) != bool(args.official_del):
            raise SystemExit(
                "--official-add and --official-del must be given together"
            )
        # SPEC: the event date is the new data's date; when the runner
        # does not supply one, fall back to the execution date.
        run_date = args.date or datetime.date.today().isoformat()
        with tempfile.TemporaryDirectory() as tmp_dir:
            new_arg = args.new
            if new_arg.startswith("http://") or new_arg.startswith("https://"):
                new_csv_path = fetch_latest(new_arg, tmp_dir)
            elif new_arg.lower().endswith(".zip"):
                new_csv_path = _extract_single_csv(new_arg, tmp_dir)
            else:
                new_csv_path = new_arg
            snapshot_records = load_snapshot(args.snapshot)
            new_records = load_records(new_csv_path)
            try:
                check_new_data_sane(snapshot_records, new_records)
            except ValueError as exc:
                raise SystemExit(f"error: {exc}") from exc
            events = diff_snapshots(snapshot_records, new_records, run_date)
            if args.official_add and args.official_del:
                off_add = load_records_any(args.official_add, tmp_dir)
                off_del = load_records_any(args.official_del, tmp_dir)
                rep = reconcile(events, off_add, off_del)
                print(
                    f"reconcile: added_match={rep['added_match']} removed_match={rep['removed_match']}"
                )
            applier_cls = APPLIERS[args.apply]
            applier = applier_cls()
            applier.apply(events)
            if args.events_json:
                with open(args.events_json, "w", encoding="utf-8") as f:
                    json.dump(events, f, ensure_ascii=False, indent=2)
            if args.events_csv:
                with open(args.events_csv, "w", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f, lineterminator="\n")
                    writer.writerow(
                        [
                            "date",
                            "change_type",
                            "jis_code",
                            "postal_code",
                            "prefecture_name",
                            "city_name",
                            "town_name",
                            "town_kana",
                            "reason_code",
                            "reason_label",
                        ]
                    )
                    for ev in events:
                        writer.writerow(
                            [
                                ev["date"],
                                ev["change_type"],
                                ev["jis_code"],
                                ev["postal_code"],
                                ev["prefecture_name"],
                                ev["city_name"],
                                ev["town_name"],
                                ev["town_kana"],
                                "" if ev["reason_code"] is None else ev["reason_code"],
                                "" if ev["reason_label"] is None else ev["reason_label"],
                            ]
                        )
            if args.update_snapshot and events:
                write_snapshot(new_csv_path, args.snapshot)
                if args.meta:
                    update_meta(
                        args.meta,
                        source=new_arg,
                        data_as_of=run_date,
                        rows=len(new_records),
                        csv_path=new_csv_path,
                    )
            added = sum(1 for ev in events if ev["change_type"] == "added")
            removed = sum(1 for ev in events if ev["change_type"] == "removed")
            print(f"added={added} removed={removed}")
            print(f"HAS_CHANGES={1 if events else 0}")

if __name__ == "__main__":
    main()
