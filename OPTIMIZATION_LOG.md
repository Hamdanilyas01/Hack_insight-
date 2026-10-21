# HeatSafe Pakistan — Model Optimization Log

**Source of record:** `modeling/optimization/validation_log_20261020T083434210943Z.csv` and `modeling/evaluations/model_metrics_20261020T083434210943Z.json`.
**Selection rule:** Lower validation MAE, followed by held-out 2025 test comparison against the **predeclared** persistence baseline.

## 1. Experiments confirmed in the provided log excerpt

| Candidate | Validation MAE (°C) | Validation baseline MAE (°C) | Difference from baseline | Key configuration shown |
|---|---:|---:|---:|---|
| HistGradientBoosting **(selected)** | **1.263785** | 1.329439 | −0.065654°C | learning_rate=0.06; max_iter=300; max_leaf_nodes=31; min_samples_leaf=25; l2_regularization=2.0; random_state=42; early_stopping=False |
| ExtraTrees | 1.282408 | 1.329439 | −0.047031°C | n_estimators=100; max_depth=18; max_features=0.9; min_samples_leaf=4; n_jobs=−1; random_state=42 |

HistGradientBoosting had a validation MAE **0.018623°C lower than ExtraTrees**, and improved on the validation baseline by approximately **4.94%**. ExtraTrees improved on the baseline by approximately **3.54%**.

The provided log excerpt shows **two** model configurations; no wider hyperparameter search or additional experiments are claimed. Refer to the CSV for exact full parameter dictionaries.

## 2. Final held-out check

The selected HistGradientBoosting model was evaluated on 8,760 held-out 2025 samples:

- **Test model MAE:** 1.055950°C.
- **Test baseline MAE:** 1.107406°C.
- **Absolute gain:** 0.051456°C; **relative gain:** 4.65%.
- **Test RMSE:** 1.433447°C.
- **Test R²:** 0.968304.

These numbers demonstrate improvement against the declared persistence baseline on the stated historical split, **not** a proven improvement for all cities or operating conditions.

## 3. Validation, uncertainty and risk categorization

- Train 34,992; validation 4,368; calibration 4,416; test 8,760 samples.
- Calibration: 90% nominal split-conformal interval, empirical held-out coverage **95.38%**, half-width **±3.00655°C**.
- Derived heat-index risk-category evaluation: accuracy **0.87409**, macro F1 **0.84896**, weighted F1 **0.87513** versus baseline weighted F1 **0.87260**.
- Group error evidence is saved at `modeling/evaluations/group_errors_20261020T083434210943Z.csv`; inspect its values before reporting which groups perform best or worst.

## 4. Responsible experiment record

- **What was optimized:** Validation MAE for 24-hour-ahead modeled heat index.
- **What was held out:** Calendar year 2025; no test tuning is asserted or recommended.
- **What was compared:** A predeclared, time-aligned persistence baseline.
- **What remains uncertain:** Independent city transfer, performance with operational forecast inputs rather than reanalysis, and workplace-level exposure prediction.

**Evidence artifacts:** `modeling/baseline_contract.json`, `modeling/optimization/validation_log_20261020T083434210943Z.csv`, `modeling/evaluations/model_metrics_20261020T083434210943Z.json`, `modeling/evaluations/risk_metrics_20261020T092105Z.json`.
