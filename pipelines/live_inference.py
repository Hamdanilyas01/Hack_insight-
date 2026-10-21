
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

import duckdb
import joblib
import numpy as np
import pandas as pd
from prefect import task

ROOT = Path(__file__).resolve().parents[1]

MODEL_PATH = ROOT / "modeling/models/heat_index_24h_20261020T083434210943Z.joblib"
LIVE_DB = ROOT / "warehouse/heatsafe_live.duckdb"

WEATHER_COLUMNS = [
    "temperature_c",
    "humidity_pct",
    "dew_point_c",
    "wind_speed_ms"
]


def heat_index_c(temperature_c, humidity_pct):
    t = np.asarray(temperature_c, dtype=float) * 1.8 + 32
    rh = np.asarray(humidity_pct, dtype=float)

    simple = 0.5 * (
        t + 61 + (t - 68) * 1.2 + rh * 0.094
    )

    simple = (simple + t) / 2

    hi = (
        -42.379
        + 2.04901523 * t
        + 10.14333127 * rh
        - 0.22475541 * t * rh
        - 0.00683783 * t**2
        - 0.05481717 * rh**2
        + 0.00122874 * t**2 * rh
        + 0.00085282 * t * rh**2
        - 0.00000199 * t**2 * rh**2
    )

    dry = (rh < 13) & (t >= 80) & (t <= 112)

    dry_adjustment = (
        ((13 - rh) / 4)
        * np.sqrt(
            np.maximum(
                0,
                (17 - np.abs(t - 95)) / 17
            )
        )
    )

    hi = np.where(dry, hi - dry_adjustment, hi)

    humid = (rh > 85) & (t >= 80) & (t <= 87)

    humid_adjustment = (
        ((rh - 85) / 10)
        * ((87 - t) / 5)
    )

    hi = np.where(humid, hi + humid_adjustment, hi)
    hi = np.where(simple >= 80, hi, simple)

    return (hi - 32) / 1.8


def risk_category(heat_c):
    heat_f = heat_c * 1.8 + 32

    if heat_f < 80:
        return "Below Caution"
    if heat_f < 90:
        return "Caution"
    if heat_f < 103:
        return "Extreme Caution"
    if heat_f < 125:
        return "Danger"

    return "Extreme Danger"


@task(name="Generate live ML prediction")
def run_live_inference(raw_path: str):
    raw_file = Path(raw_path).resolve()
    metadata_file = raw_file.with_name(
        raw_file.stem + ".metadata.json"
    )

    for path in [raw_file, metadata_file, MODEL_PATH, LIVE_DB]:
        if not path.is_file():
            raise FileNotFoundError(path)

    raw_bytes = raw_file.read_bytes()
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()

    metadata = json.loads(
        metadata_file.read_text(encoding="utf-8")
    )

    if raw_hash != metadata["sha256"]:
        raise ValueError("Raw weather SHA-256 mismatch.")

    if metadata["quality"]["status"] != "PASS":
        raise ValueError("Live weather quality validation failed.")

    raw = json.loads(raw_bytes)

    if raw.get("utc_offset_seconds") != 0:
        raise ValueError("Expected UTC weather timestamps.")

    if raw.get("hourly_units", {}).get("wind_speed_10m") != "m/s":
        raise ValueError("Expected wind speed in m/s.")

    bundle = joblib.load(MODEL_PATH)

    model = bundle["model"]
    feature_columns = list(bundle["feature_columns"])
    interval_radius_c = float(bundle["interval_radius_c"])

    if len(feature_columns) != 18:
        raise ValueError("Expected exactly 18 ML features.")

    if not np.isfinite(interval_radius_c) or interval_radius_c < 0:
        raise ValueError("Invalid uncertainty interval.")

    if hasattr(model, "feature_names_in_"):
        if list(model.feature_names_in_) != feature_columns:
            raise ValueError("Model feature order mismatch.")

    weather = pd.DataFrame(raw["hourly"])

    weather["time_utc"] = pd.to_datetime(
        weather["time"],
        utc=True,
        errors="raise"
    )

    weather = weather.rename(columns={
        "temperature_2m": "temperature_c",
        "relative_humidity_2m": "humidity_pct",
        "dew_point_2m": "dew_point_c",
        "wind_speed_10m": "wind_speed_ms"
    })

    weather = (
        weather.sort_values("time_utc")
        .set_index("time_utc")
    )

    if weather.index.has_duplicates:
        raise ValueError("Duplicate weather timestamps.")

    for column in WEATHER_COLUMNS:
        weather[column] = pd.to_numeric(
            weather[column],
            errors="raise"
        )

    collected_at = pd.Timestamp(
        metadata["collected_at_utc"]
    ).tz_convert("UTC")

    issue_time = collected_at.floor("h")
    target_time = issue_time + pd.Timedelta(hours=24)

    if issue_time not in weather.index:
        raise ValueError("Current issue hour unavailable.")

    if target_time not in weather.index:
        raise ValueError("Target forecast hour unavailable.")

    expected_history = pd.date_range(
        end=issue_time,
        periods=49,
        freq="h",
        tz="UTC"
    )

    history = weather.loc[:issue_time].copy()

    if not expected_history.isin(history.index).all():
        raise ValueError("Missing required 48-hour lag history.")

    recent_history = history.loc[expected_history]

    if not np.isfinite(
        recent_history[WEATHER_COLUMNS].to_numpy(dtype=float)
    ).all():
        raise ValueError("Invalid weather values in model history.")

    history["heat_index_now_c"] = heat_index_c(
        history["temperature_c"],
        history["humidity_pct"]
    )

    for lag in [1, 6, 24, 48]:
        history[f"hi_lag_{lag}"] = (
            history["heat_index_now_c"].shift(lag)
        )

    history["temperature_lag_24"] = (
        history["temperature_c"].shift(24)
    )

    history["humidity_lag_24"] = (
        history["humidity_pct"].shift(24)
    )

    history["hi_rolling_mean_24"] = (
        history["heat_index_now_c"]
        .rolling(24, min_periods=24)
        .mean()
    )

    history["hi_rolling_std_24"] = (
        history["heat_index_now_c"]
        .rolling(24, min_periods=24)
        .std(ddof=1)
    )

    history["temperature_rolling_mean_24"] = (
        history["temperature_c"]
        .rolling(24, min_periods=24)
        .mean()
    )

    target_pk = target_time.tz_convert("Asia/Karachi")
    row = history.loc[issue_time].to_dict()

    row["target_hour_sin"] = np.sin(
        2 * np.pi * target_pk.hour / 24
    )

    row["target_hour_cos"] = np.cos(
        2 * np.pi * target_pk.hour / 24
    )

    row["target_doy_sin"] = np.sin(
        2 * np.pi * target_pk.dayofyear / 365.25
    )

    row["target_doy_cos"] = np.cos(
        2 * np.pi * target_pk.dayofyear / 365.25
    )

    missing_features = [
        col for col in feature_columns if col not in row
    ]

    if missing_features:
        raise ValueError(
            f"Missing ML features: {missing_features}"
        )

    X_live = pd.DataFrame([
        {col: row[col] for col in feature_columns}
    ])

    if not np.isfinite(
        X_live.to_numpy(dtype=float)
    ).all():
        raise ValueError("Invalid engineered ML features.")

    ml_prediction_c = float(model.predict(X_live)[0])
    baseline_prediction_c = float(row["heat_index_now_c"])

    forecast_row = weather.loc[target_time]

    api_heat_index_c = float(
        heat_index_c(
            forecast_row["temperature_c"],
            forecast_row["humidity_pct"]
        )
    )

    interval_lower_c = ml_prediction_c - interval_radius_c
    interval_upper_c = ml_prediction_c + interval_radius_c

    model_hash = hashlib.sha256(
        MODEL_PATH.read_bytes()
    ).hexdigest()

    snapshot_id = raw_file.stem
    generated_at = datetime.now(timezone.utc)

    result = {
        "snapshot_id": snapshot_id,
        "issue_time_utc": issue_time.isoformat(),
        "target_time_utc": target_time.isoformat(),
        "target_time_pk": target_pk.isoformat(),
        "forecast_horizon_hours": 24,
        "model_name": bundle.get(
            "selected_model_name",
            type(model).__name__
        ),
        "model_prediction_c": round(ml_prediction_c, 4),
        "baseline_prediction_c": round(baseline_prediction_c, 4),
        "api_formula_heat_index_c": round(api_heat_index_c, 4),
        "historical_interval_lower_c": round(interval_lower_c, 4),
        "historical_interval_upper_c": round(interval_upper_c, 4),
        "model_risk_category": risk_category(ml_prediction_c),
        "api_formula_risk_category": risk_category(api_heat_index_c),
        "model_sha256": model_hash,
        "raw_snapshot_sha256": raw_hash,
        "input_source": "Open-Meteo Forecast API",
        "training_source": "ERA5 reanalysis",
        "generated_at_utc": generated_at.isoformat(),
        "interval_note": (
            "Interval calibrated on historical ERA5 data. "
            "Live forecast-input coverage is not yet verified."
        )
    }

    conn = duckdb.connect(str(LIVE_DB))

    try:
        source_record = conn.execute("""
            SELECT sha256, dq_status
            FROM raw.live_weather_runs
            WHERE snapshot_id = ?
        """, [snapshot_id]).fetchone()

        if source_record is None:
            raise ValueError("Live snapshot not found in DuckDB.")

        if source_record[0] != raw_hash or source_record[1] != "PASS":
            raise ValueError("DuckDB source verification failed.")

        conn.execute("BEGIN TRANSACTION")

        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed.live_ml_predictions (
                    snapshot_id VARCHAR PRIMARY KEY,
                    issue_time_utc TIMESTAMPTZ,
                    target_time_utc TIMESTAMPTZ,
                    model_prediction_c DOUBLE,
                    baseline_prediction_c DOUBLE,
                    api_formula_heat_index_c DOUBLE,
                    interval_lower_c DOUBLE,
                    interval_upper_c DOUBLE,
                    model_risk_category VARCHAR,
                    api_formula_risk_category VARCHAR,
                    model_sha256 VARCHAR,
                    raw_sha256 VARCHAR,
                    generated_at_utc TIMESTAMPTZ
                )
            """)

            existing = conn.execute("""
                SELECT COUNT(*)
                FROM processed.live_ml_predictions
                WHERE snapshot_id = ?
            """, [snapshot_id]).fetchone()[0]

            if not existing:
                conn.execute("""
                    INSERT INTO processed.live_ml_predictions
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    snapshot_id,
                    issue_time.to_pydatetime(),
                    target_time.to_pydatetime(),
                    ml_prediction_c,
                    baseline_prediction_c,
                    api_heat_index_c,
                    interval_lower_c,
                    interval_upper_c,
                    risk_category(ml_prediction_c),
                    risk_category(api_heat_index_c),
                    model_hash,
                    raw_hash,
                    generated_at
                ])

            conn.execute("""
                CREATE OR REPLACE VIEW gold.latest_live_ml AS
                SELECT
                    p.*,
                    timezone('Asia/Karachi', p.target_time_utc)
                        AS target_time_pk,
                    r.collected_at_utc,
                    r.dq_status,
                    p.snapshot_id = (
                        SELECT snapshot_id
                        FROM raw.live_weather_runs
                        ORDER BY collected_at_utc DESC
                        LIMIT 1
                    ) AS matches_latest_ingestion
                FROM processed.live_ml_predictions p
                JOIN raw.live_weather_runs r
                    ON p.snapshot_id = r.snapshot_id
                ORDER BY r.collected_at_utc DESC
                LIMIT 1
            """)

            conn.execute("COMMIT")

        except Exception:
            conn.execute("ROLLBACK")
            raise

    finally:
        conn.close()

    output_dir = ROOT / "data/processed/live_ml"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{snapshot_id}_ml_24h.json"

    output_path.write_text(
        json.dumps(result, indent=2),
        encoding="utf-8"
    )

    print("=" * 55)
    print("HEATSAFE LIVE ML INFERENCE COMPLETE")
    print("=" * 55)

    print("Issue time:", issue_time)
    print("Target time (Karachi):", target_pk)
    print(f"ML predicted heat index: {ml_prediction_c:.2f} °C")
    print(f"Persistence baseline: {baseline_prediction_c:.2f} °C")
    print(f"API formula heat index: {api_heat_index_c:.2f} °C")
    print(
        f"Historical interval: "
        f"{interval_lower_c:.2f} °C to "
        f"{interval_upper_c:.2f} °C"
    )
    print("ML risk:", risk_category(ml_prediction_c))
    print("Saved prediction:", output_path)

    return result
