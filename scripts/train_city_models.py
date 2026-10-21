"""Train and evaluate historical 24-hour heat-index forecasts for 3 new cities.

Run from HeatSafe project root:
    python scripts/train_city_models.py

This does not retrain or overwrite the existing Karachi model. Uses 2020-23
training, Jan-Jun 2024 validation, Jul-Dec 2024 interval calibration, and
2025 test, based on TARGET UTC time. Inputs are archived ERA5 reanalysis;
performance does not establish prospective operational forecast accuracy.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "city_era5_manifest.csv"
OUTPUT_MODELS = ROOT / "modeling" / "models"
OUTPUT_EVAL = ROOT / "modeling" / "evaluations"
OUTPUT_DQ = ROOT / "logs" / "multi_city_quality_summary.csv"
CITIES = ("Lahore", "Islamabad", "Multan")
FEATURES = [
    "temperature_2m", "relative_humidity_2m", "dew_point_2m",
    "wind_speed_10m", "heat_index_now_c", "hour_sin", "hour_cos",
    "year_sin", "year_cos", "month_sin", "month_cos",
]


def heat_index_c(temp_c: np.ndarray, humidity: np.ndarray) -> np.ndarray:
    """NOAA/NWS heat-index approximation with standard adjustments.

    This is a *weather-derived* apparent-heat metric, not direct-sun WBGT
    or an observed worker health outcome.
    """
    t = np.asarray(temp_c, dtype=float) * (9.0 / 5.0) + 32.0
    rh = np.asarray(humidity, dtype=float)
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    simple = (simple + t) / 2.0

    roth = (
        -42.379 + 2.04901523*t + 10.14333127*rh
        - 0.22475541*t*rh - 0.00683783*t*t
        - 0.05481717*rh*rh + 0.00122874*t*t*rh
        + 0.00085282*t*rh*rh - 0.00000199*t*t*rh*rh
    )
    low_rh = (rh < 13) & (t >= 80) & (t <= 112)
    low_adj = ((13-rh)/4) * np.sqrt(np.maximum(0, (17-abs(t-95))/17))
    high_rh = (rh > 85) & (t >= 80) & (t <= 87)
    high_adj = ((rh-85)/10) * ((87-t)/5)
    roth = roth - np.where(low_rh, low_adj, 0) + np.where(high_rh, high_adj, 0)
    hi_f = np.where(simple >= 80, roth, simple)
    return (hi_f - 32.0) * (5.0 / 9.0)


def risk_category(values_c: np.ndarray) -> np.ndarray:
    f = np.asarray(values_c, dtype=float) * 9.0 / 5.0 + 32.0
    return np.select(
        [f < 80, f < 90, f < 103, f < 125],
        ["Below Caution", "Caution", "Extreme Caution", "Danger"],
        default="Extreme Danger",
    )


def get_timestamp_column(df: pd.DataFrame) -> str:
    for candidate in ("time", "time_utc", "timestamp", "datetime"):
        if candidate in df.columns:
            return candidate
    raise ValueError("No hourly timestamp column found")


def validate_and_prepare(city: str, data_file: Path) -> tuple[pd.DataFrame, dict]:
    if not data_file.is_file():
        raise FileNotFoundError(f"Missing {city} data: {data_file}")

    data = pd.read_csv(data_file)
    required = ["temperature_2m", "relative_humidity_2m", "dew_point_2m", "wind_speed_10m"]
    missing_columns = [c for c in required if c not in data]
    if missing_columns:
        raise ValueError(f"{city}: missing columns {missing_columns}")

    ts_col = get_timestamp_column(data)
    time = pd.to_datetime(data[ts_col], utc=True, errors="coerce")
    numbers = data[required].apply(pd.to_numeric, errors="coerce")
    n_invalid_time = int(time.isna().sum())
    n_nulls = {c: int(numbers[c].isna().sum()) for c in required}
    n_duplicates = int(time.duplicated().sum())
    n_issues = {
        "humidity_outside_0_100": int((~numbers["relative_humidity_2m"].between(0, 100)).sum()),
        "negative_wind": int((numbers["wind_speed_10m"] < 0).sum()),
        "temperature_outside_minus50_60": int((~numbers["temperature_2m"].between(-50, 60)).sum()),
    }
    dq = {
        "city": city,
        "raw_rows": len(data),
        "invalid_timestamps": n_invalid_time,
        "duplicate_timestamps": n_duplicates,
        **{f"null_{col}": count for col, count in n_nulls.items()},
        **n_issues,
    }
    if n_invalid_time or n_duplicates or any(n_nulls.values()) or any(n_issues.values()):
        dq["status"] = "FAIL"
        raise ValueError(f"{city} failed data checks: {dq}")

    frame = numbers.copy()
    frame["time_utc"] = time
    frame = frame.sort_values("time_utc").reset_index(drop=True)
    differences = frame["time_utc"].diff().dropna()
    n_gaps = int((differences != pd.Timedelta(hours=1)).sum())
    dq["non_hourly_gaps"] = n_gaps
    dq["status"] = "PASS" if n_gaps == 0 else "FAIL"
    if n_gaps:
        raise ValueError(f"{city}: {n_gaps} missing/non-hourly intervals; not training on shifted rows")

    if len(frame) != 52608:
        raise ValueError(f"{city}: expected 52,608 historical hourly records, got {len(frame)}")
    if frame["time_utc"].iloc[0] != pd.Timestamp("2020-01-01", tz="UTC"):
        raise ValueError(f"{city}: unexpected beginning of historical period")
    if frame["time_utc"].iloc[-1] != pd.Timestamp("2025-12-31 23:00", tz="UTC"):
        raise ValueError(f"{city}: unexpected end of historical period")
    return frame, dq


def form_supervised(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df["heat_index_now_c"] = heat_index_c(
        df["temperature_2m"].to_numpy(), df["relative_humidity_2m"].to_numpy()
    )
    df["target_time_utc"] = df["time_utc"].shift(-24)
    df["target_heat_index_c"] = df["heat_index_now_c"].shift(-24)
    df = df.iloc[:-24].copy()
    if not ((df["target_time_utc"] - df["time_utc"]) == pd.Timedelta(hours=24)).all():
        raise ValueError("24-hour target alignment failed")

    local_time = df["time_utc"].dt.tz_convert("Asia/Karachi")
    hour = local_time.dt.hour.to_numpy()
    doy = local_time.dt.dayofyear.to_numpy()
    month = local_time.dt.month.to_numpy()
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    df["year_sin"] = np.sin(2 * np.pi * doy / 365.25)
    df["year_cos"] = np.cos(2 * np.pi * doy / 365.25)
    df["month_sin"] = np.sin(2 * np.pi * month / 12)
    df["month_cos"] = np.cos(2 * np.pi * month / 12)
    df["baseline_prediction_c"] = df["heat_index_now_c"]

    target = df["target_time_utc"]
    conditions = [
        target < pd.Timestamp("2024-01-01", tz="UTC"),
        target < pd.Timestamp("2024-07-01", tz="UTC"),
        target < pd.Timestamp("2025-01-01", tz="UTC"),
    ]
    df["split"] = np.select(conditions, ["train", "validation", "calibration"], default="test")
    df["month_pk"] = df["target_time_utc"].dt.tz_convert("Asia/Karachi").dt.month
    local_target_hour = df["target_time_utc"].dt.tz_convert("Asia/Karachi").dt.hour
    df["working_hour_group"] = np.where(local_target_hour.between(8, 18), "08-18 PKT", "19-07 PKT")
    return df


def train_city(city: str, df: pd.DataFrame, input_path: Path, dq: dict) -> dict:
    splits = {name: df.loc[df["split"] == name].copy() for name in (
        "train", "validation", "calibration", "test"
    )}
    for name, split in splits.items():
        if split.empty:
            raise ValueError(f"{city}: empty {name} split")

    model = HistGradientBoostingRegressor(
        learning_rate=0.06,
        max_iter=300,
        max_leaf_nodes=31,
        min_samples_leaf=25,
        l2_regularization=2.0,
        early_stopping=False,
        random_state=42,
    )
    training = splits["train"]
    model.fit(training[FEATURES], training["target_heat_index_c"])

    predictions = {
        name: model.predict(frame[FEATURES])
        for name, frame in splits.items() if name != "train"
    }
    calibration = splits["calibration"]
    absolute_errors = np.abs(
        calibration["target_heat_index_c"].to_numpy() - predictions["calibration"]
    )
    # Split conformal: finite-sample quantile with nominal marginal coverage 90%.
    nominal = 0.9
    k = min(len(absolute_errors), int(np.ceil((len(absolute_errors) + 1) * nominal)))
    half_width = float(np.partition(absolute_errors, k-1)[k-1])
    validation = splits["validation"]
    test = splits["test"]
    y_val = validation["target_heat_index_c"].to_numpy()
    y_test = test["target_heat_index_c"].to_numpy()
    yp_test = predictions["test"]
    base_val = mean_absolute_error(y_val, validation["baseline_prediction_c"])
    val_mae = mean_absolute_error(y_val, predictions["validation"])
    base_test = mean_absolute_error(y_test, test["baseline_prediction_c"])
    test_mae = mean_absolute_error(y_test, yp_test)
    risk_acc = float(np.mean(risk_category(y_test) == risk_category(yp_test)))
    coverage = float(np.mean(np.abs(y_test - yp_test) <= half_width))

    record = {
        "city": city,
        "model": "HistGradientBoosting",
        "baseline": "same_hour_previous_day_persistence",
        "target": "derived_heat_index_24h_ahead_c",
        "train_rows": len(training),
        "validation_rows": len(validation),
        "calibration_rows": len(calibration),
        "test_rows": len(test),
        "validation_model_mae_c": float(val_mae),
        "validation_baseline_mae_c": float(base_val),
        "test_model_mae_c": float(test_mae),
        "test_baseline_mae_c": float(base_test),
        "test_model_rmse_c": float(np.sqrt(mean_squared_error(y_test, yp_test))),
        "test_model_r2": float(r2_score(y_test, yp_test)),
        "model_beats_baseline": bool(test_mae < base_test),
        "risk_classification_accuracy": risk_acc,
        "interval_nominal_coverage": nominal,
        "interval_empirical_coverage": coverage,
        "interval_half_width_c": half_width,
        "source_csv": str(input_path.relative_to(ROOT)).replace("\\", "/"),
        "source_csv_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "dq_status": dq["status"],
        "limitations": "ERA5 reanalysis is not archived issued forecasts; heat index is not WBGT or observed worker illness; independent prospective validation needed.",
    }

    slug = city.lower()
    # Save non-Karachi models separately; existing artifacts remain untouched.
    joblib.dump({
        "estimator": model,
        "features": FEATURES,
        "city": city,
        "target": record["target"],
        "forecast_horizon_hours": 24,
        "interval_half_width_c": half_width,
        "data_source": record["source_csv"],
        "training_description": "2020-2023 ERA5 reanalysis, trained on time-before-target features",
    }, OUTPUT_MODELS / f"heat_index_24h_{slug}_historical.joblib")

    evidence = test[["time_utc", "target_time_utc", "target_heat_index_c", "baseline_prediction_c", "month_pk", "working_hour_group"]].copy()
    evidence["model_prediction_c"] = yp_test
    evidence["interval_lower_c"] = yp_test - half_width
    evidence["interval_upper_c"] = yp_test + half_width
    evidence["actual_risk_category"] = risk_category(y_test)
    evidence["predicted_risk_category"] = risk_category(yp_test)
    evidence.to_csv(OUTPUT_EVAL / f"{slug}_test_predictions.csv", index=False)

    groups = []
    for group_by in ("month_pk", "working_hour_group"):
        for val, selection in evidence.groupby(group_by):
            groups.append({
                "city": city, "group_by": group_by, "group": str(val),
                "rows": len(selection),
                "model_mae_c": float(mean_absolute_error(selection["target_heat_index_c"], selection["model_prediction_c"])),
                "baseline_mae_c": float(mean_absolute_error(selection["target_heat_index_c"], selection["baseline_prediction_c"])),
            })
    pd.DataFrame(groups).to_csv(OUTPUT_EVAL / f"{slug}_group_errors.csv", index=False)
    (OUTPUT_EVAL / f"{slug}_model_metrics.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def main() -> None:
    if not MANIFEST.is_file():
        raise FileNotFoundError("Missing data/city_era5_manifest.csv; run fetch_city_history.py first")
    manifest = pd.read_csv(MANIFEST)
    OUTPUT_MODELS.mkdir(parents=True, exist_ok=True)
    OUTPUT_EVAL.mkdir(parents=True, exist_ok=True)
    OUTPUT_DQ.parent.mkdir(parents=True, exist_ok=True)

    metrics = []
    dq_rows = []
    for city in CITIES:
        entry = manifest.loc[manifest["city"].str.casefold() == city.casefold()]
        if len(entry) != 1:
            raise ValueError(f"Expected one manifest entry for {city}; found {len(entry)}")
        path = ROOT / str(entry.iloc[0]["processed_csv"])
        print(f"\n{city}: checking ERA5 hourly data...")
        try:
            frame, dq = validate_and_prepare(city, path)
        except ValueError as ex:
            print("STOP:", ex)
            raise
        dq_rows.append(dq)
        supervised = form_supervised(frame)
        print(f"{city}: training HistGradientBoosting on {len(supervised):,} aligned examples...")
        result = train_city(city, supervised, path, dq)
        metrics.append(result)
        print(f"{city}: test model MAE {result['test_model_mae_c']:.3f} C; "
              f"baseline {result['test_baseline_mae_c']:.3f} C; "
              f"beats baseline: {result['model_beats_baseline']}; "
              f"90% interval observed coverage: {result['interval_empirical_coverage']:.1%}")

    pd.DataFrame(dq_rows).to_csv(OUTPUT_DQ, index=False)
    result_file = OUTPUT_EVAL / "multi_city_model_metrics.csv"
    pd.DataFrame(metrics).drop(columns=["limitations"]).to_csv(result_file, index=False)
    print(f"\nDONE: metrics saved to {result_file.relative_to(ROOT)}")
    print(f"DQ evidence saved to {OUTPUT_DQ.relative_to(ROOT)}")
    print("Note: only cities with model_beats_baseline=True can claim baseline improvement.")
    print("Note: this is a historical test; live inference and full historical DQ agent are separate steps.")


if __name__ == "__main__":
    main()
