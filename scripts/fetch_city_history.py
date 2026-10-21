"""Download and validate ERA5 hourly history for HeatSafe's three new cities.

Matches the existing Karachi weather source in manifest.csv. Does not train
models, alter Karachi data, modify Prefect, or claim full DQ validation.

Run from repository root: python scripts/fetch_city_history.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "city_era5"
CSV_DIR = ROOT / "data" / "processed" / "city_era5"
MANIFEST_FILE = ROOT / "data" / "city_era5_manifest.csv"

CITIES = {
    "Lahore": (31.5204, 74.3587),
    "Islamabad": (33.6844, 73.0479),
    "Multan": (30.1575, 71.5249),
}
VARIABLES = ["temperature_2m", "relative_humidity_2m", "dew_point_2m", "wind_speed_10m"]
API_URL = "https://archive-api.open-meteo.com/v1/archive"
START, END = "2020-01-01", "2025-12-31"
EXPECTED_TIMES = pd.date_range(START, "2026-01-01", freq="h", inclusive="left", tz="UTC")


def retrieve(name: str, lat: float, lon: float) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": START,
        "end_date": END,
        "hourly": ",".join(VARIABLES),
        "models": "era5",
        "timezone": "GMT",
        "wind_speed_unit": "ms",
    }
    last_error = None
    for attempt in range(1, 4):
        try:
            response = requests.get(API_URL, params=params, timeout=120)
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                raise RuntimeError(str(data.get("reason", data)))
            return {
                "data": data,
                "raw": response.content,
                "url": response.url,
            }
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt != 3:
                print(f"  Retry {attempt}/3 after: {type(exc).__name__}", flush=True)
                time.sleep(2 * attempt)
    raise RuntimeError(f"Could not download {name}: {last_error}")


def process_city(name: str, lat: float, lon: float) -> dict:
    slug = name.lower().replace(" ", "_")
    print(f"Fetching {name} ({START} to {END})...", flush=True)
    response = retrieve(name, lat, lon)
    hourly = response["data"].get("hourly", {})
    missing = [v for v in ["time", *VARIABLES] if v not in hourly]
    if missing:
        raise ValueError(f"{name}: API response missing {missing}")
    lengths = {k: len(hourly[k]) for k in ["time", *VARIABLES]}
    if len(set(lengths.values())) != 1:
        raise ValueError(f"{name}: inconsistent hourly vector lengths: {lengths}")

    frame = pd.DataFrame({k: hourly[k] for k in ["time", *VARIABLES]})
    times = pd.to_datetime(frame["time"], utc=True, errors="raise")
    if len(times) != len(EXPECTED_TIMES) or not times.equals(pd.Series(EXPECTED_TIMES)):
        raise ValueError(
            f"{name}: expected {len(EXPECTED_TIMES)} complete chronological UTC hours; "
            f"got {len(times)}. Not writing unvalidated data."
        )
    for field in VARIABLES:
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
    nulls = {field: int(frame[field].isna().sum()) for field in VARIABLES}
    frame.insert(0, "city", name)
    frame["time_utc"] = times.dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{slug}_era5_2020_2025.json"
    csv_path = CSV_DIR / f"{slug}_era5_2020_2025.csv"
    raw_path.write_bytes(response["raw"])
    frame.to_csv(csv_path, index=False, encoding="utf-8")
    checksum = hashlib.sha256(response["raw"]).hexdigest()
    print(f"  {len(frame):,} hourly rows; nulls={nulls}", flush=True)
    return {
        "city": name,
        "start_date": START,
        "end_date": END,
        "rows": len(frame),
        "source": "Open-Meteo ERA5 historical reanalysis",
        "licence": "CC BY 4.0; verify and retain source attribution",
        "query": response["url"],
        "fetch_time_utc": datetime.now(timezone.utc).isoformat(),
        "raw_file": raw_path.relative_to(ROOT).as_posix(),
        "processed_csv": csv_path.relative_to(ROOT).as_posix(),
        "raw_sha256": checksum,
        "null_counts": json.dumps(nulls, sort_keys=True),
        "data_quality_status": "basic-hourly-checks-only; full DQ agent pending",
    }


def main() -> int:
    records = []
    for name, (lat, lon) in CITIES.items():
        try:
            records.append(process_city(name, lat, lon))
        except Exception as exc:
            print(f"FAILED {name}: {exc}", file=sys.stderr, flush=True)
            print("Other cities may already have downloaded successfully.", file=sys.stderr)
            return 1
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_FILE.open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(f"SUCCESS: {len(records)} cities, {sum(int(r['rows']) for r in records):,} total rows")
    print(f"Source manifest: {MANIFEST_FILE.relative_to(ROOT)}")
    print("NOTE: Historical data acquisition only; city-specific DQ, model training and evaluation still pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
