"""Load the raw USAspending award files into a SQLite database.

Usage, from the repo root:

    python src/load_awards.py

Reads every data/raw/awards_{year}_{code}.json, cleans the records and
rebuilds data/processed/procurement.db from schema.sql. The reasons behind
the cleaning decisions are in NOTES.md.

Nothing is dropped silently: any record that is incomplete, duplicated or
rejected by the schema stops the load with an error naming the file and the
award, and no database is left behind.
"""
import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "processed" / "procurement.db"
SCHEMA_PATH = ROOT / "schema.sql"

AGENCY = "National Aeronautics and Space Administration"


def parse_file_name(path):
    """Return (fiscal_year, extent_competed) from awards_{year}_{code}.json."""
    parts = path.stem.split("_")
    if len(parts) != 3 or not parts[1].isdigit():
        raise ValueError(f"{path.name}: expected a name like awards_2024_A.json")
    return int(parts[1]), parts[2]


def text(raw, field, where):
    """Return a text field with surrounding whitespace removed."""
    value = raw[field]
    if not isinstance(value, str):
        raise ValueError(f"{where}: {field} should be text, got {value!r}")
    return value.strip()


def clean_record(raw, year, code, where):
    """Turn one raw API record into a flat row, or raise ValueError."""
    try:
        naics = raw["NAICS"]
        if not isinstance(naics, dict):
            raise ValueError(f"{where}: NAICS should be an object, got {naics!r}")
        row = {
            "generated_internal_id": text(raw, "generated_internal_id", where),
            "award_id": text(raw, "Award ID", where),
            "vendor_uei": text(raw, "Recipient UEI", where),
            "vendor_name": text(raw, "Recipient Name", where),
            "naics_code": text(naics, "code", where),
            "naics_description": text(naics, "description", where),
            "award_amount": raw["Award Amount"],
            "start_date": text(raw, "Start Date", where),
            "contract_award_type": text(raw, "Contract Award Type", where),
            "fiscal_year": raw["fiscal_year"],
            "extent_competed": text(raw, "extent_competed", where),
            "where": where,
        }
        sub_agency = text(raw, "Awarding Sub Agency", where)
    except KeyError as missing:
        raise ValueError(f"{where}: missing field {missing}") from None

    # The sub agency is not stored, but anything other than NASA means the
    # fetch was scoped wrongly, so check it before throwing it away.
    if sub_agency != AGENCY:
        raise ValueError(f"{where}: unexpected sub agency {sub_agency!r}")

    # fiscal_year and extent_competed were stamped on by the fetch script from
    # the same values it used to name the file, so the two must agree.
    if row["fiscal_year"] != year or row["extent_competed"] != code:
        raise ValueError(
            f"{where}: record says fiscal year {row['fiscal_year']!r}, extent "
            f"competed {row['extent_competed']!r}, which does not match the file name"
        )
    return row


def read_awards(raw_dir):
    """Read and clean every raw file. Return (rows, files read, empty files)."""
    paths = sorted(raw_dir.glob("awards_*.json"))
    if not paths:
        raise ValueError(f"no awards_*.json files found in {raw_dir}")

    rows = []
    seen = {}  # generated_internal_id -> where it was first seen
    empty_files = 0
    for path in paths:
        year, code = parse_file_name(path)
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{path.name}: not valid JSON ({error})") from None
        if not isinstance(records, list):
            raise ValueError(f"{path.name}: expected a JSON list of awards")
        if not records:
            empty_files += 1

        for position, raw in enumerate(records, start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"{path.name}, record {position}: not a JSON object")
            award = raw.get("generated_internal_id", f"at position {position}")
            row = clean_record(raw, year, code, f"{path.name}, award {award}")
            key = row["generated_internal_id"]
            if key in seen:
                raise ValueError(f"{row['where']}: duplicate of {seen[key]}")
            seen[key] = row["where"]
            rows.append(row)
    return rows, len(paths), empty_files


def latest_per_key(rows, key):
    """Return {key value: the most recent award row carrying that value}.

    Used to pick one vendor name per UEI and one description per NAICS code.
    "Most recent" is by start date, with ties broken by the higher
    generated_internal_id so the choice does not depend on file order.
    """
    latest = {}
    for row in rows:
        current = latest.get(row[key])
        rank = (row["start_date"], row["generated_internal_id"])
        if current is None or rank > (current["start_date"], current["generated_internal_id"]):
            latest[row[key]] = row
    return latest


def count_with_variants(rows, key, value):
    """Count the keys that appear with more than one distinct value."""
    variants = {}
    for row in rows:
        variants.setdefault(row[key], set()).add(row[value])
    return sum(len(values) > 1 for values in variants.values())


def outside_fiscal_year(row):
    """True if start_date is not within Oct 1 - Sep 30 of the row's fiscal year."""
    year = row["fiscal_year"]
    # ISO dates compare correctly as strings.
    return not (f"{year - 1}-10-01" <= row["start_date"] <= f"{year}-09-30")


def insert_rows(conn, sql, rows):
    """Insert rows one at a time so a rejected row can be named."""
    for row in rows:
        try:
            conn.execute(sql, row)
        except sqlite3.Error as error:
            raise ValueError(f"{row['where']}: {error}") from None


def build_database(db_path, schema_sql, rows, vendors, naics):
    """Create the database at db_path and return its total award dollars."""
    # isolation_level=None stops sqlite3 from managing transactions itself, so
    # the schema and every insert can sit inside one explicit BEGIN ... COMMIT.
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")  # has no effect inside a transaction
        conn.executescript("BEGIN;\n" + schema_sql)
        insert_rows(conn, "INSERT INTO vendors (uei, name) VALUES (:vendor_uei, :vendor_name)",
                    vendors.values())
        insert_rows(conn, "INSERT INTO naics (code, description) "
                          "VALUES (:naics_code, :naics_description)",
                    naics.values())
        insert_rows(conn, """
            INSERT INTO awards (generated_internal_id, award_id, vendor_uei, naics_code,
                                award_amount, start_date, contract_award_type,
                                fiscal_year, extent_competed)
            VALUES (:generated_internal_id, :award_id, :vendor_uei, :naics_code,
                    :award_amount, :start_date, :contract_award_type,
                    :fiscal_year, :extent_competed)""", rows)
        conn.execute("COMMIT")
        return conn.execute("SELECT SUM(award_amount) FROM awards").fetchone()[0]
    finally:
        conn.close()


def load(raw_dir, db_path, schema_path):
    # Remove the previous database first, so a failed run cannot leave an old
    # one behind looking like the result of this run.
    temp_path = db_path.with_name(db_path.name + ".tmp")
    db_path.unlink(missing_ok=True)
    temp_path.unlink(missing_ok=True)

    rows, files_read, empty_files = read_awards(raw_dir)
    vendors = latest_per_key(rows, "vendor_uei")
    naics = latest_per_key(rows, "naics_code")

    # Build under a temporary name and rename only once the load has committed.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        total_dollars = build_database(temp_path, schema_path.read_text(encoding="utf-8"),
                                       rows, vendors, naics)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    temp_path.replace(db_path)

    print(f"Loaded {db_path}")
    print(f"  files read:                       {files_read} ({empty_files} empty)")
    print(f"  awards:                           {len(rows):,}")
    print(f"  vendors:                          {len(vendors):,}")
    print(f"  naics codes:                      {len(naics):,}")
    print(f"  total dollars:                    {total_dollars or 0:,.2f}")
    print(f"  UEIs with more than one name:     "
          f"{count_with_variants(rows, 'vendor_uei', 'vendor_name')}")
    print(f"  NAICS codes with more than one description: "
          f"{count_with_variants(rows, 'naics_code', 'naics_description')}")
    print(f"  zero-dollar awards (kept):        "
          f"{sum(row['award_amount'] == 0 for row in rows):,}")
    print(f"  start date outside fiscal year (kept): "
          f"{sum(outside_fiscal_year(row) for row in rows):,}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR,
                        help="directory holding awards_{year}_{code}.json")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="database file to build")
    parser.add_argument("--schema", type=Path, default=SCHEMA_PATH, help="schema SQL file")
    args = parser.parse_args()
    try:
        load(args.raw_dir, args.db, args.schema)
    except (ValueError, OSError, sqlite3.Error) as error:
        raise SystemExit(f"error: {error}") from None


if __name__ == "__main__":
    main()
