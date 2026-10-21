from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

model_path = ROOT / "modeling/models/heat_index_24h_20261020T083434210943Z.joblib"

dataset_path = ROOT / "modeling/heat_index_24h_dataset_20261020T082348729935Z.csv"

predictions_path = ROOT / "modeling/evaluations/test_predictions_20261020T083434210943Z.csv"

for path in [model_path, dataset_path, predictions_path]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing file: {path}")

artifact = joblib.load(model_path)

model = artifact["model"]
feature_columns = list(artifact["feature_columns"])

saved = pd.read_csv(dataset_path)
test = pd.read_csv(predictions_path)

saved["time_utc"] = pd.to_datetime(
    saved["time_utc"],
    utc=True
)

saved = (
    saved.sort_values("time_utc")
    .set_index("time_utc")
)

if saved.index.has_duplicates:
    raise ValueError("Duplicate timestamps in ML dataset.")

issue_times = pd.DatetimeIndex(
    pd.to_datetime(test["time_utc"], utc=True)
)

target_times = pd.to_datetime(
    test["target_time_utc"],
    utc=True
).dt.tz_convert("Asia/Karachi")

X = pd.DataFrame(index=test.index)

for col in [
    "temperature_c",
    "humidity_pct",
    "dew_point_c",
    "wind_speed_ms",
    "heat_index_now_c"
]:
    X[col] = (
        saved[col]
        .reindex(issue_times)
        .to_numpy(dtype=float)
    )

hi = saved["heat_index_now_c"]

for lag in [1, 6, 24, 48]:
    X[f"hi_lag_{lag}"] = (
        hi.shift(lag)
        .reindex(issue_times)
        .to_numpy(dtype=float)
    )

X["temperature_lag_24"] = (
    saved["temperature_c"]
    .shift(24)
    .reindex(issue_times)
    .to_numpy(dtype=float)
)

X["humidity_lag_24"] = (
    saved["humidity_pct"]
    .shift(24)
    .reindex(issue_times)
    .to_numpy(dtype=float)
)

X["hi_rolling_mean_24"] = (
    hi.rolling(24, min_periods=24)
    .mean()
    .reindex(issue_times)
    .to_numpy(dtype=float)
)

X["hi_rolling_std_24"] = (
    hi.rolling(24, min_periods=24)
    .std(ddof=1)
    .reindex(issue_times)
    .to_numpy(dtype=float)
)

X["temperature_rolling_mean_24"] = (
    saved["temperature_c"]
    .rolling(24, min_periods=24)
    .mean()
    .reindex(issue_times)
    .to_numpy(dtype=float)
)

hours = target_times.dt.hour.to_numpy(dtype=float)
days = target_times.dt.dayofyear.to_numpy(dtype=float)

X["target_hour_sin"] = np.sin(2 * np.pi * hours / 24)
X["target_hour_cos"] = np.cos(2 * np.pi * hours / 24)

X["target_doy_sin"] = np.sin(2 * np.pi * days / 365.25)
X["target_doy_cos"] = np.cos(2 * np.pi * days / 365.25)

if len(feature_columns) != 18:
    raise ValueError("Expected 18 model features.")

if not np.isfinite(X[feature_columns].to_numpy()).all():
    raise ValueError("Missing or invalid model features.")

reproduced = model.predict(X[feature_columns])

saved_predictions = test[
    "model_prediction_c"
].to_numpy(dtype=float)

actual = test[
    "target_heat_index_c"
].to_numpy(dtype=float)

baseline = test[
    "baseline_prediction_c"
].to_numpy(dtype=float)

differences = np.abs(reproduced - saved_predictions)

matching = int((differences < 1e-10).sum())
maximum_difference = float(differences.max())

model_mae = float(np.mean(np.abs(actual - reproduced)))
baseline_mae = float(np.mean(np.abs(actual - baseline)))

print("=" * 60)
print("HEATSAFE LOCAL MODEL VERIFICATION")
print("=" * 60)

print("Model:", type(model).__name__)
print("Features:", len(feature_columns))
print("Test rows:", len(test))
print("Matching predictions:", matching)
print("Maximum prediction difference:", maximum_difference)

print("\nMODEL PERFORMANCE")
print(f"ML MAE: {model_mae:.6f} °C")
print(f"Baseline MAE: {baseline_mae:.6f} °C")

reference_path = ROOT / "modeling/evaluations/heatsafe_reference_features.npz"

if not reference_path.is_file():
    raise FileNotFoundError(f"Missing reference file: {reference_path}")

with np.load(reference_path, allow_pickle=False) as reference:
    reference_columns = reference["feature_columns"].astype(str).tolist()
    reference_features = reference["features"].astype(np.float64)
    reference_predictions = reference["expected_predictions"].astype(np.float64)

if reference_columns != feature_columns:
    raise ValueError("Reference feature order does not match the model.")

local_features = X[feature_columns].to_numpy(dtype=np.float64)

if local_features.shape != reference_features.shape:
    raise ValueError("Local and reference feature shapes do not match.")

feature_differences = np.abs(local_features - reference_features)

feature_report = pd.DataFrame({
    "feature": feature_columns,
    "max_difference": feature_differences.max(axis=0),
    "mean_difference": feature_differences.mean(axis=0),
    "rows_different": (feature_differences > 1e-12).sum(axis=0)
}).sort_values("max_difference", ascending=False)

reference_X = pd.DataFrame(
    reference_features,
    columns=feature_columns
)

reference_reproduced = model.predict(reference_X)

reference_error = np.abs(
    reference_reproduced - reference_predictions
)

print("\n" + "=" * 60)
print("FEATURE ENGINEERING DIAGNOSTIC")
print("=" * 60)

print("\nDIFFERENCES BY FEATURE")
print(feature_report.to_string(index=False, float_format=lambda v: f"{v:.15g}"))

print("\nREFERENCE MODEL CHECK")
print("Reference matching:", int((reference_error < 1e-10).sum()), "/", len(reference_error))
print("Reference maximum difference:", float(reference_error.max()))

print("\nLOCAL RECONSTRUCTION CHECK")
print("Local matching:", matching, "/", len(test))
print("Local maximum prediction difference:", maximum_difference)
print("Maximum input feature difference:", float(feature_differences.max()))

different_rows = np.flatnonzero(differences >= 1e-10)

if len(different_rows):
    worst_rows = different_rows[np.argsort(differences[different_rows])[-5:][::-1]]

    print("\nFIVE LARGEST PREDICTION DIFFERENCES")

    for i in worst_rows:
        print(
            "Time:", test.iloc[i]["time_utc"],
            "| Prediction difference:", float(differences[i]),
            "| Largest feature difference:", float(feature_differences[i].max())
        )

if not np.all(reference_error < 1e-10):
    raise AssertionError("Reference model verification failed.")

if model_mae >= baseline_mae:
    raise AssertionError("ML model did not beat baseline.")

print("\nSAVED MODEL VERIFICATION: PASS")

if matching == len(test):
    print("LOCAL FEATURE RECONSTRUCTION: PASS")
else:
    print("LOCAL FEATURE RECONSTRUCTION: NEEDS ALIGNMENT")