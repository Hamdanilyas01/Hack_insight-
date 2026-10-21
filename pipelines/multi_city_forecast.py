"""HeatSafe Pakistan multi-city *provider weather forecast* feed.

This script deliberately does NOT apply the Karachi ML model to new cities.
Produces dashboard-ready JSON/CSV plus provenance and a raw response snapshot.
Run from the project root: python pipelines/multi_city_forecast.py
Uses only the Python standard library.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "multi_city"
DASHBOARD_DIR = ROOT / "dashboard_data"
ENDPOINT = "https://api.open-meteo.com/v1/forecast"
PKT = timezone(timedelta(hours=5))

# Approximate city-centre coordinates (not neighborhood-specific measurements).
# Each entry is (city, province/region, latitude, longitude).
CITIES = [
    ("Karachi", "Sindh", 24.8607, 67.0011),
    ("Lahore", "Punjab", 31.5204, 74.3587),
    ("Islamabad", "Islamabad Capital Territory", 33.6844, 73.0479),
    ("Rawalpindi", "Punjab", 33.5651, 73.0169),
    ("Faisalabad", "Punjab", 31.4504, 73.1350),
    ("Multan", "Punjab", 30.1575, 71.5249),
    ("Peshawar", "Khyber Pakhtunkhwa", 34.0151, 71.5249),
    ("Quetta", "Balochistan", 30.1798, 66.9750),
    ("Hyderabad", "Sindh", 25.3960, 68.3578),
    ("Gujranwala", "Punjab", 32.1877, 74.1945),
    ("Sialkot", "Punjab", 32.4945, 74.5229),
    ("Sukkur", "Sindh", 27.7139, 68.8369),
    ("Bahawalpur", "Punjab", 29.3956, 71.6836),
    ("Jacobabad", "Sindh", 28.2819, 68.4380),
    ("Sargodha", "Punjab", 32.0836, 72.6711),
    ("Abbottabad", "Khyber Pakhtunkhwa", 34.1463, 73.2117),
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_number(value, low: float, high: float):
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (ValueError, TypeError):
        return None
    return round(v, 2) if math.isfinite(v) and low <= v <= high else None


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def build_feed(raw_response: object, fetched_at: str) -> tuple[dict, list[dict]]:
    results = raw_response if isinstance(raw_response, list) else [raw_response]
    if not isinstance(results, list) or len(results) != len(CITIES):
        raise ValueError(f"Expected {len(CITIES)} city responses; got {len(results)}")

    feed = {
        "generated_at_utc": fetched_at,
        "source": "Open-Meteo weather forecast API",
        "source_url": ENDPOINT,
        "forecast_kind": "provider_weather_forecast",
        "is_heatsafe_ml_prediction": False,
        "note": "These are provider model weather forecasts, NOT independently validated HeatSafe city ML predictions. Apparent temperature is NOT the NWS heat index.",
        "timezone": "Asia/Karachi",
        "cities": [],
    }
    flat = []
    seen_cities = set()
    for (city, region, requested_lat, requested_lon), result in zip(CITIES, results):
        if not isinstance(result, dict) or "hourly" not in result:
            raise ValueError(f"No hourly forecast returned for {city}: {str(result)[:120]}")
        hourly = result["hourly"]
        keys = ("time", "temperature_2m", "relative_humidity_2m", "apparent_temperature")
        if any(not isinstance(hourly.get(key), list) for key in keys):
            raise ValueError(f"Missing required hourly arrays for {city}")
        length = len(hourly["time"])
        if length < 24 or len({len(hourly[key]) for key in keys}) != 1:
            raise ValueError(f"Incomplete or unequal forecast arrays for {city}")
        times = set()
        values = []
        for i in range(length):
            t = datetime.fromisoformat(hourly["time"][i])
            if t.tzinfo is not None:
                raise ValueError(f"Unexpected timezone-aware input for {city}")
            pkt_time = t.replace(tzinfo=PKT)
            utc_time = pkt_time.astimezone(timezone.utc)
            if pkt_time.isoformat() in times:
                raise ValueError(f"Duplicate forecast hour for {city}")
            times.add(pkt_time.isoformat())
            temp = safe_number(hourly["temperature_2m"][i], -60, 65)
            humid = safe_number(hourly["relative_humidity_2m"][i], 0, 100)
            apparent = safe_number(hourly["apparent_temperature"][i], -100, 100)
            failures = [label for label, val in (("temperature", temp), ("humidity", humid), ("apparent_temperature", apparent)) if val is None]
            entry = {
                "city": city,
                "region": region,
                "forecast_time_pk": pkt_time.isoformat(),
                "forecast_time_utc": utc_time.isoformat(),
                "temperature_2m_c": temp,
                "relative_humidity_pct": humid,
                "apparent_temperature_c": apparent,
                "dq_status": "FLAG" if failures else "PASS",
                "dq_reason": ",".join(failures),
            }
            values.append(entry)
            flat.append(entry)
        if city in seen_cities:
            raise ValueError(f"Duplicate city {city}")
        seen_cities.add(city)
        feed["cities"].append({
            "city": city,
            "region": region,
            "latitude": requested_lat,
            "longitude": requested_lon,
            "forecast_grid_latitude": result.get("latitude"),
            "forecast_grid_longitude": result.get("longitude"),
            "hours": values,
        })
    return feed, flat


def main() -> None:
    params = {
        "latitude": ",".join(str(c[2]) for c in CITIES),
        "longitude": ",".join(str(c[3]) for c in CITIES),
        "hourly": "temperature_2m,relative_humidity_2m,apparent_temperature",
        "forecast_hours": "48",
        "timezone": "Asia/Karachi",
    }
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "HeatSafePakistan/1.0 (hackathon research)"})
    print(f"Requesting hourly forecasts for {len(CITIES)} Pakistani cities...")
    try:
        with urllib.request.urlopen(request, timeout=75) as reply:
            raw_bytes = reply.read()
    except urllib.error.HTTPError as exc:
        details = exc.read(300).decode("utf-8", errors="replace")
        raise RuntimeError(f"Open-Meteo returned HTTP {exc.code}: {details}") from exc

    raw = json.loads(raw_bytes.decode("utf-8"))
    fetched = datetime.now(timezone.utc).isoformat()
    feed, rows = build_feed(raw, fetched)
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=[
        "city", "region", "forecast_time_pk", "forecast_time_utc",
        "temperature_2m_c", "relative_humidity_pct", "apparent_temperature_c",
        "dq_status", "dq_reason",
    ])
    writer.writeheader()
    writer.writerows(rows)
    csv_bytes = csv_buffer.getvalue().encode("utf-8")
    json_bytes = (json.dumps(feed, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    manifest = {
        "retrieved_at_utc": fetched,
        "source": ENDPOINT,
        "request_url": url,
        "city_count": len(CITIES),
        "forecast_rows": len(rows),
        "quality_flagged_rows": sum(r["dq_status"] != "PASS" for r in rows),
        "raw_response_sha256": sha256(raw_bytes),
        "dashboard_json_sha256": sha256(json_bytes),
        "dashboard_csv_sha256": sha256(csv_bytes),
        "limitation": "Weather-provider city forecasts; not HeatSafe ML model predictions or workplace exposure measurements.",
    }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    atomic_write(DATA_DIR / f"raw_openmeteo_multicity_{timestamp}.json", raw_bytes)
    atomic_write(DASHBOARD_DIR / "multi_city_forecast.json", json_bytes)
    atomic_write(DASHBOARD_DIR / "multi_city_forecast.csv", csv_bytes)
    atomic_write(DASHBOARD_DIR / "multi_city_manifest.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))

    print(f"Cities: {len(CITIES)} | Rows: {len(rows)} | DQ flagged rows: {manifest['quality_flagged_rows']}")
    print("Created dashboard_data/multi_city_forecast.json")
    print("Created dashboard_data/multi_city_forecast.csv")
    print("Created dashboard_data/multi_city_manifest.json")
    print("Note: these are weather-provider forecasts, not city-specific HeatSafe ML forecasts.")


if __name__ == "__main__":
    main()
