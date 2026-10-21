# HeatSafe Pakistan — Model Card

**Artifact:** 24-hour-ahead heat-index regression and derived heat-risk classification
**Model:** HistGradientBoosting
**Evidence status:** Recorded local evaluations dated 20 October 2026; no independent replication performed in this document.

## 1. Purpose and scope

HeatSafe Pakistan estimates the **weather-derived heat index (°C) 24 hours ahead** to help illustrate potential heat exposure for outdoor work planning, initially for Karachi, Pakistan. The output is **not** a measured Wet Bulb Globe Temperature (WBGT), worker illness prediction, personal exposure measurement, or workplace safety certification. The target is calculated from historical temperature and relative humidity using the NWS heat-index formulation.

The model was selected by validation performance, with a **predeclared baseline** of same-hour previous-day persistence: predicted heat index at target time *t + 24 h* equals calculated heat index at issue time *t*.

## 2. Training and evaluation design

| Period | Target-time rule (UTC) | Samples |
|---|---|---:|
| Training | Before 2024-01-01 | 34,992 |
| Validation | 2024-01-01 to 2024-06-30 | 4,368 |
| Interval calibration | 2024-07-01 to 2024-12-31 | 4,416 |
| Held-out test | 2025-01-01 to 2025-12-31 | 8,760 |

These are time-ordered splits, not randomized cross-validation. **No claim of geographic out-of-sample validation is made.** The historical source uses Open-Meteo ERA5 reanalysis rather than archived operational forecasts.

## 3. Held-out results

| Regression metric | Model | Declared baseline |
|---|---:|---:|
| Validation MAE (°C) | 1.2638 | 1.3294 |
| 2025 test MAE (°C) | **1.0560** | 1.1074 |
| 2025 test RMSE (°C) | 1.4334 | Not reported |
| 2025 test R² | 0.9683 | Not reported |

The held-out test MAE improvement is **0.0515°C**, or **4.65% relative to the baseline**. This satisfies the predeclared criterion that ML test MAE be lower than baseline test MAE. A modest improvement should not be overstated as proof of workplace-specific effectiveness.

### Uncertainty

The evaluation uses a split-conformal absolute-error interval calibrated on H2 2024:

- **Nominal coverage:** 90%.
- **Observed test coverage:** 95.38% (2025 held-out data).
- **Half-width:** ±3.0065°C (total interval width approximately 6.0131°C).

The difference between nominal and empirical coverage must be reported transparently; the interval is not a guarantee and can behave differently on new cities or changing conditions.

### Derived risk categories

Categories are calculated from predicted heat index using documented Fahrenheit thresholds (80°F Caution; 90°F Extreme Caution; 103°F Danger; 125°F Extreme Danger), as referenced in the risk-evaluation JSON. For the **8,760 held-out 2025 rows**:

| Classification metric | Recorded value |
|---|---:|
| Accuracy | 0.8741 |
| Macro F1 | 0.8490 |
| Weighted F1 | 0.8751 |
| Baseline weighted F1 | 0.8726 |

These evaluate agreement with **derived heat-index bands**, not observed worker harm. Check confusion-matrix and per-class CSV files before making claims about performance on rare extreme categories.

## 4. Group-wise evaluation

Month and working-hour group are planned error-reporting dimensions. The saved artifact `modeling/evaluations/group_errors_20261020T083434210943Z.csv` contains the associated evaluation; its detailed values are **not reproduced here because they have not yet been inspected in this reporting step**. This check should be completed before final submission.

## 5. Appropriate and inappropriate uses

**Suitable:** Decision-support visualization of weather-derived heat-index forecasts, comparison of forecast risk bands, and general educational heat-safety guidance paired with evidence-based recommendations.

**Not suitable:** Diagnosis; direct assessment of a specific worker's heat strain; replacing employer heat-risk assessments, WBGT instrumentation, clinical advice, or official emergency alerts; claiming block-by-block temperatures without corresponding observations; transferring the model to new cities without validation.

Heat index does not fully capture direct sunlight, radiant heat, clothing/PPE, work intensity, individual susceptibility or workplace microclimates. Historical ERA5 features may differ from data genuinely available at forecast issue time, so deployment must avoid unavailable future information and be evaluated under operational inputs.

## 6. Provenance and reproducibility

- Predeclared target/baseline: `modeling/baseline_contract.json`.
- Regression evidence: `modeling/evaluations/model_metrics_20261020T083434210943Z.json`.
- Risk classification evidence: `modeling/evaluations/risk_metrics_20261020T092105Z.json`.
- Group errors: `modeling/evaluations/group_errors_20261020T083434210943Z.csv`.
- Optimization experiments: `modeling/optimization/validation_log_20261020T083434210943Z.csv`.
- Model artifact: `modeling/models/heat_index_24h_20261020T083434210943Z.joblib`.
- Evaluated dataset SHA-256: `c5c34adc4581d7655ee59c3fd047c8c822193db18711e82d2f37296e67c9c5eb`.
- Baseline contract SHA-256: `45476ed36c66e3c9502b0eede75ee8976b0d79f573d789ecab9ece5ebcd8fb3f`.

## 7. Evaluation status and next checks

The recorded numbers support **a 2025 held-out historical test result**, not external-site validation or prospective worker outcomes. Before further geographic expansion, report group errors, compare any new city to an appropriate city-specific baseline, and test operational forecast-input availability.
