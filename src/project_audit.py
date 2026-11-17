"""Urban Pulse post-load audit.

Run from the project root:
    python src/project_audit.py

Creates timestamped JSON and Markdown validation reports in reports/.
This is an additional repeatable quality-control step beyond the loader.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mysql.connector
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)
load_dotenv(ROOT / ".env")


def connect_to_database():
    required = ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError("Missing .env settings: " + ", ".join(missing))
    return mysql.connector.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        connection_timeout=15,
        charset="utf8mb4",
    )


def fetch_one(cursor, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return row or {}


def build_audit() -> dict[str, Any]:
    connection = connect_to_database()
    try:
        cursor = connection.cursor(dictionary=True)
        try:
            main = fetch_one(
                cursor,
                """
                SELECT
                    COUNT(*) AS row_count,
                    COUNT(DISTINCT unique_key) AS unique_ids,
                    SUM(unique_key IS NULL) AS missing_ids,
                    SUM(duplicate_request_id = TRUE) AS duplicate_flagged_rows,
                    MIN(created_date) AS earliest_created,
                    MAX(created_date) AS latest_created,
                    SUM(is_closed = TRUE) AS recorded_closed,
                    SUM(is_closed = FALSE) AS no_recorded_closure,
                    SUM(closed_before_created = TRUE) AS chronology_issues,
                    SUM(invalid_coordinates = TRUE) AS invalid_coordinate_rows,
                    SUM(incomplete_coordinates = TRUE) AS incomplete_coordinate_rows,
                    SUM(resolution_hours IS NOT NULL AND resolution_hours >= 0) AS valid_durations,
                    SUM(resolution_hours > 72) AS over_72_hours,
                    SUM(is_closed = TRUE AND (status IS NULL OR status <> 'Closed')) AS closed_date_status_mismatch,
                    SUM(is_closed = FALSE AND status = 'Closed') AS closed_status_without_date
                FROM service_requests
                """,
            )
            staging = fetch_one(
                cursor,
                "SELECT COUNT(*) AS row_count, COUNT(DISTINCT unique_key) AS unique_ids "
                "FROM service_requests_staging",
            )
            expected = int(os.getenv("EXPECTED_RECORDS", "100000"))
            row_count = int(main.get("row_count") or 0)
            unique_ids = int(main.get("unique_ids") or 0)
            staging_rows = int(staging.get("row_count") or 0)

            checks = {
                "main_table_matches_expected_records": row_count == expected,
                "main_table_ids_are_unique": unique_ids == row_count,
                "main_table_has_no_missing_ids": int(main.get("missing_ids") or 0) == 0,
                "main_table_has_no_duplicate_flags": int(main.get("duplicate_flagged_rows") or 0) == 0,
                "staging_matches_main_row_count": staging_rows == row_count,
            }

            for key in ("earliest_created", "latest_created"):
                value = main.get(key)
                main[key] = value.isoformat(sep=" ") if value else None

            return {
                "project": "Urban Pulse",
                "audited_at_utc": datetime.now(timezone.utc).isoformat(),
                "database": os.getenv("MYSQL_DATABASE", "urban_pulse_db"),
                "expected_records": expected,
                "main_table": main,
                "staging_table": staging,
                "checks": checks,
                "all_checks_passed": all(checks.values()),
                "interpretation": [
                    "is_closed is based on a recorded closed_date, not the source status label.",
                    "Chronology issues and coordinate incompleteness are reported, not silently removed.",
                    "A recorded closure does not establish citizen satisfaction or independently verified resolution.",
                ],
            }
        finally:
            cursor.close()
    finally:
        connection.close()


def main() -> int:
    audit = build_audit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = REPORTS / f"database_audit_{stamp}.json"
    md_path = REPORTS / f"database_audit_{stamp}.md"
    json_path.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")

    lines = [
        "# Urban Pulse - Database Audit",
        "",
        f"Audit time (UTC): {audit['audited_at_utc']}",
        f"Database: `{audit['database']}`",
        "",
        "## Main table metrics",
        "",
    ]
    for key, value in audit["main_table"].items():
        lines.append(f"- **{key.replace('_', ' ').title()}**: {value}")
    lines.extend(["", "## Checks", ""])
    for key, passed in audit["checks"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} - {key.replace('_', ' ')}")
    lines.extend(["", "## Interpretation notes", ""])
    lines.extend(f"- {item}" for item in audit["interpretation"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("=" * 64)
    print("URBAN PULSE - POST-LOAD DATABASE AUDIT")
    print("=" * 64)
    print(f"Main rows: {audit['main_table']['row_count']:,}")
    print(f"Unique IDs: {audit['main_table']['unique_ids']:,}")
    print(f"Staging rows: {audit['staging_table']['row_count']:,}")
    print(f"All checks passed: {audit['all_checks_passed']}")
    for name, passed in audit["checks"].items():
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    return 0 if audit["all_checks_passed"] else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("DATABASE AUDIT FAILED:", exc)
        sys.exit(1)
