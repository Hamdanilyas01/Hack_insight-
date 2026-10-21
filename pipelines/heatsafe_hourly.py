
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

import numpy as np
import pandas as pd
import requests
from prefect import flow, task
from live_warehouse import update_live_warehouse
from live_inference import run_live_inference
ROOT = Path(__file__).resolve().parents[1]

API_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_COLUMNS = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "wind_speed_10m"
]


@task(name="Collect Karachi weather", retries=2, retry_delay_seconds=10)
def collect_weather():
    params = {
        "latitude": 24.8607,
        "longitude": 67.0011,
        "hourly": ",".join(WEATHER_COLUMNS),
        "past_hours": 72,
        "forecast_hours": 48,
        "timezone": "UTC",
        "temperature_unit": "celsius",
        "wind_speed_unit": "ms"
    }

    response = requests.get(API_URL, params=params, timeout=60)
    response.raise_for_status()

    payload = response.json()

    if payload.get("error"):
        raise RuntimeError(str(payload))

    print("Weather API response received.")

    return {
        "payload": payload,
        "raw_bytes": response.content,
        "request_url": response.url,
        "collected_at_utc": datetime.now(timezone.utc).isoformat()
    }


@task(name="Validate live weather")
def validate_weather(snapshot):
    data = snapshot["payload"]

    hourly = data.get("hourly", {})
    units = data.get("hourly_units", {})

    if "time" not in hourly:
        raise ValueError("Weather timestamps missing.")

    timestamps = pd.to_datetime(
        hourly["time"],
        utc=True,
        errors="coerce"
    )

    if timestamps.isna().any():
        raise ValueError("Invalid weather timestamps.")

    if len(timestamps) < 96:
        raise ValueError("Insufficient hourly weather history.")

    if timestamps.has_duplicates:
        raise ValueError("Duplicate hourly timestamps detected.")

    if not timestamps.is_monotonic_increasing:
        raise ValueError("Weather timestamps are not sorted.")

    gaps = pd.Series(timestamps).diff().dropna()

    if not gaps.eq(pd.Timedelta(hours=1)).all():
        raise ValueError("Missing or irregular hourly timestamps.")

    if data.get("utc_offset_seconds") != 0:
        raise ValueError("Expected UTC timestamps.")

    if units.get("wind_speed_10m") != "m/s":
        raise ValueError("Wind speed unit is not m/s.")

    values = {}

    for column in WEATHER_COLUMNS:
        if column not in hourly:
            raise ValueError(f"Missing column: {column}")

        if len(hourly[column]) != len(timestamps):
            raise ValueError(f"Column length mismatch: {column}")

        values[column] = np.asarray(hourly[column], dtype=float)

        if not np.isfinite(values[column]).all():
            raise ValueError(f"Missing or invalid values: {column}")

    humidity = values["relative_humidity_2m"]
    wind = values["wind_speed_10m"]

    if ((humidity < 0) | (humidity > 100)).any():
        raise ValueError("Humidity outside 0–100%.")

    if (wind < 0).any():
        raise ValueError("Negative wind speed detected.")

    future_hours = int(
        (timestamps > pd.Timestamp.now(tz="UTC")).sum()
    )

    if future_hours < 24:
        raise ValueError("Fewer than 24 future forecast hours.")

    result = {
        "status": "PASS",
        "rows": len(timestamps),
        "future_hours": future_hours,
        "first_timestamp": timestamps[0].isoformat(),
        "last_timestamp": timestamps[-1].isoformat()
    }

    print("Weather quality validation passed.")
    print(result)

    return result


@task(name="Save raw weather and provenance")
def save_weather(snapshot, quality):
    if not (ROOT / "manifest.csv").is_file():
        raise FileNotFoundError("Project manifest.csv not found.")

    output_dir = ROOT / "data" / "raw" / "open_meteo_live"
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    raw_path = output_dir / f"karachi_live_{stamp}.json"
    metadata_path = output_dir / f"karachi_live_{stamp}.metadata.json"

    raw_bytes = snapshot["raw_bytes"]
    checksum = hashlib.sha256(raw_bytes).hexdigest()

    raw_path.write_bytes(raw_bytes)

    metadata = {
        "source": "Open-Meteo Forecast API",
        "location": "Karachi, Pakistan",
        "collected_at_utc": snapshot["collected_at_utc"],
        "request_url": snapshot["request_url"],
        "raw_file": str(raw_path.relative_to(ROOT)),
        "sha256": checksum,
        "quality": quality
    }

    metadata_path.write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8"
    )

    print("Raw weather saved:", raw_path)
    print("Metadata saved:", metadata_path)
    print("SHA-256:", checksum)

    return str(raw_path)


@flow(name="HeatSafe Karachi Live Pipeline", log_prints=True)
def heatsafe_hourly():
    snapshot = collect_weather()
    quality = validate_weather(snapshot)
    raw_path = save_weather(snapshot, quality)

    warehouse_result = update_live_warehouse(raw_path)
    inference_result = run_live_inference(raw_path)

    print("=" * 55)
    print("HEATSAFE LIVE PIPELINE COMPLETE")
    print("=" * 55)
    print("Quality:", quality["status"])
    print("Weather rows:", warehouse_result["rows"])
    print(
        "ML predicted heat index:",
        inference_result["model_prediction_c"],
        "°C"
    )
    print(
        "Heat risk:",
        inference_result["model_risk_category"]
    )

    return {
        "warehouse": warehouse_result,
        "inference": inference_result
    }


if __name__ == "__main__":
    heatsafe_hourly()
