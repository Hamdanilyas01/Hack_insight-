
from pathlib import Path
from datetime import datetime
import hashlib
import json
import shutil

import duckdb
import pandas as pd
from prefect import task

ROOT = Path(__file__).resolve().parents[1]

HISTORICAL_DB = (
    ROOT
    / "warehouse"
    / "heatsafe_warehouse_20261020T075642918974Z.duckdb"
)

LIVE_DB = ROOT / "warehouse" / "heatsafe_live.duckdb"


@task(name="Update HeatSafe DuckDB warehouse")
def update_live_warehouse(raw_path: str):
    raw_file = Path(raw_path).resolve()

    if not raw_file.is_file():
        raise FileNotFoundError(raw_file)

    metadata_file = raw_file.with_name(
        raw_file.stem + ".metadata.json"
    )

    if not metadata_file.is_file():
        raise FileNotFoundError(metadata_file)

    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    raw_bytes = raw_file.read_bytes()

    actual_hash = hashlib.sha256(raw_bytes).hexdigest()

    if actual_hash != metadata["sha256"]:
        raise ValueError("Raw weather SHA-256 mismatch.")

    if metadata["quality"]["status"] != "PASS":
        raise ValueError("Weather quality status is not PASS.")

    payload = json.loads(raw_bytes)
    hourly = payload["hourly"]

    snapshot_id = raw_file.stem

    collected_at = datetime.fromisoformat(
        metadata["collected_at_utc"]
    )

    frame = pd.DataFrame({
        "time_utc": pd.to_datetime(hourly["time"], utc=True),
        "temperature_2m": hourly["temperature_2m"],
        "relative_humidity_2m": hourly["relative_humidity_2m"],
        "dew_point_2m": hourly["dew_point_2m"],
        "wind_speed_10m": hourly["wind_speed_10m"]
    })

    frame["snapshot_id"] = snapshot_id
    frame["dq_status"] = "PASS"

    if len(frame) != metadata["quality"]["rows"]:
        raise ValueError("Validated row count does not match.")

    if not LIVE_DB.is_file():
        if not HISTORICAL_DB.is_file():
            raise FileNotFoundError(HISTORICAL_DB)

        shutil.copy2(HISTORICAL_DB, LIVE_DB)
        print("Created working warehouse copy.")

    conn = duckdb.connect(str(LIVE_DB))

    try:
        conn.register("incoming_live_rows", frame)
        conn.execute("BEGIN TRANSACTION")

        try:
            for schema in ["raw", "cleaned", "processed", "gold"]:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS raw.live_weather_runs (
                    snapshot_id VARCHAR PRIMARY KEY,
                    collected_at_utc TIMESTAMPTZ,
                    raw_file VARCHAR,
                    sha256 VARCHAR,
                    row_count INTEGER,
                    dq_status VARCHAR
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS cleaned.live_weather_hourly (
                    snapshot_id VARCHAR,
                    time_utc TIMESTAMPTZ,
                    temperature_2m DOUBLE,
                    relative_humidity_2m DOUBLE,
                    dew_point_2m DOUBLE,
                    wind_speed_10m DOUBLE,
                    dq_status VARCHAR,
                    PRIMARY KEY (snapshot_id, time_utc)
                )
            """)

            existing = conn.execute(
                "SELECT COUNT(*) FROM raw.live_weather_runs WHERE snapshot_id = ?",
                [snapshot_id]
            ).fetchone()[0]

            if not existing:
                conn.execute("""
                    INSERT INTO raw.live_weather_runs
                    VALUES (?, ?, ?, ?, ?, ?)
                """, [
                    snapshot_id,
                    collected_at,
                    str(raw_file.relative_to(ROOT)),
                    actual_hash,
                    len(frame),
                    "PASS"
                ])

                conn.execute("""
                    INSERT INTO cleaned.live_weather_hourly (
                        snapshot_id,
                        time_utc,
                        temperature_2m,
                        relative_humidity_2m,
                        dew_point_2m,
                        wind_speed_10m,
                        dq_status
                    )
                    SELECT
                        snapshot_id,
                        time_utc,
                        temperature_2m,
                        relative_humidity_2m,
                        dew_point_2m,
                        wind_speed_10m,
                        dq_status
                    FROM incoming_live_rows
                """)

            conn.execute("""
                CREATE OR REPLACE VIEW processed.live_weather_hourly AS
                SELECT
                    w.snapshot_id,
                    w.time_utc,
                    timezone('Asia/Karachi', w.time_utc) AS time_pk,
                    w.temperature_2m AS temperature_c,
                    w.relative_humidity_2m AS humidity_pct,
                    w.dew_point_2m AS dew_point_c,
                    w.wind_speed_10m AS wind_speed_ms,
                    w.dq_status,
                    w.time_utc > r.collected_at_utc AS is_forecast
                FROM cleaned.live_weather_hourly w
                JOIN raw.live_weather_runs r
                    ON w.snapshot_id = r.snapshot_id
            """)

            conn.execute("""
                CREATE OR REPLACE VIEW gold.latest_live_weather AS
                SELECT *
                FROM processed.live_weather_hourly
                WHERE snapshot_id = (
                    SELECT snapshot_id
                    FROM raw.live_weather_runs
                    ORDER BY collected_at_utc DESC
                    LIMIT 1
                )
            """)

            conn.execute("""
                CREATE OR REPLACE VIEW gold.live_freshness AS
                SELECT
                    r.snapshot_id,
                    r.collected_at_utc,
                    r.dq_status,
                    COUNT(*) AS total_hours,
                    SUM(CASE WHEN w.is_forecast THEN 1 ELSE 0 END)
                        AS future_hours,
                    MAX(w.time_utc) AS latest_weather_time_utc
                FROM raw.live_weather_runs r
                JOIN processed.live_weather_hourly w
                    ON r.snapshot_id = w.snapshot_id
                WHERE r.snapshot_id = (
                    SELECT snapshot_id
                    FROM raw.live_weather_runs
                    ORDER BY collected_at_utc DESC
                    LIMIT 1
                )
                GROUP BY
                    r.snapshot_id,
                    r.collected_at_utc,
                    r.dq_status
            """)

            saved_count = conn.execute("""
                SELECT COUNT(*)
                FROM cleaned.live_weather_hourly
                WHERE snapshot_id = ?
            """, [snapshot_id]).fetchone()[0]

            if saved_count != len(frame):
                raise ValueError("Warehouse row count verification failed.")

            conn.execute("COMMIT")

        except Exception:
            conn.execute("ROLLBACK")
            raise

        latest = conn.execute("""
            SELECT *
            FROM gold.live_freshness
        """).df()

        print("=" * 55)
        print("HEATSAFE LIVE DUCKDB UPDATE COMPLETE")
        print("=" * 55)
        print("Database:", LIVE_DB)
        print("Snapshot:", snapshot_id)
        print("Rows stored:", saved_count)
        print("\nLIVE FRESHNESS")
        print(latest.to_string(index=False))

        return {
            "database": str(LIVE_DB),
            "snapshot_id": snapshot_id,
            "rows": saved_count
        }

    finally:
        conn.unregister("incoming_live_rows")
        conn.close()
