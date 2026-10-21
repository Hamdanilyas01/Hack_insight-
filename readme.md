
# HeatSafe Pakistan

AI-powered heat-risk forecasting and workplace safety guidance for Pakistan.

## Project Overview

HeatSafe Pakistan is a HackInsight 2026 project focused on helping outdoor workers and decision-makers understand heat-related risks.

The initial implementation focuses on Karachi.

The system combines:
- Historical and forecast weather data
- Machine learning for heat-index prediction
- Data quality checks
- A DuckDB data warehouse
- Prefect pipeline automation
- A retrieval-based safety guidance assistant

## Main Features

### 1. Heat-Risk Forecasting
Predicts the heat index 24 hours ahead using a trained machine learning model.

The model is compared against a previous-day persistence baseline.

### 2. Data Warehouse
Uses DuckDB to manage raw, cleaned, processed and live evidence.

### 3. Data Quality
Validates weather and air-quality data and records quality decisions.

### 4. Insight Agent
Retrieves guidance from:
- CDC NIOSH
- UK Health and Safety Executive
- NOAA National Weather Service

The current agent provides cited guidance and can retrieve verified ML forecasts from DuckDB.

A generative AI layer is planned.

### 5. Prefect Automation
Includes a locally tested Prefect pipeline for weather ingestion, validation, warehouse updates and ML inference.

Cloud deployment is not yet completed.

## Technology Stack

- Python 3.11
- Pandas and NumPy
- Scikit-learn
- DuckDB
- Prefect
- Pandera
- Beautiful Soup
- TF-IDF retrieval
- Gradio (planned dashboard)

## Getting Started

### 1. Clone the Repository

```powershell
git clone https://github.com/Hamdanilyas01/Hack_insight-.git
cd Hack_insight-
```

### 2. Create a Virtual Environment

```powershell
py -3.11 -m venv .venv
```

### 3. Activate the Environment

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4. Install Dependencies

```powershell
python -m pip install -r requirements.txt
```

The dependency installation process still needs testing on a fresh computer.

## Test the Insight Agent

```powershell
python rag/insight_agent.py --question "How to be safe?"
```

The committed knowledge index allows guidance retrieval.

Live forecast questions also require an up-to-date local DuckDB warehouse, which is not included in Git.

## Important Files

- `pipelines/` - Prefect workflow and live data processing
- `rag/` - Safety guidance and Insight Agent
- `modeling/` - ML model and evaluation evidence
- `logs/` - Data quality and audit evidence
- `manifest.csv` - Data provenance records
- `requirements.txt` - Python dependencies

## Data Sources

Historical weather: ERA5 reanalysis accessed through Open-Meteo.

Air-quality information: CAMS modeled air-quality data accessed through Open-Meteo.

Safety guidance: CDC NIOSH, UK HSE and NOAA.

Weather-derived heat index is not a direct measurement of a worker's personal heat exposure or occupational WBGT.

## Reproducibility Notes

Generated raw datasets and live DuckDB files are excluded from Git.

Complete automated data rebuilding and clean-clone testing are still being finalized.

Original webpage snapshots are not included in the public repository. The committed RAG index includes source URLs and integrity metadata.

## Project Status

Core data processing, ML training, local Prefect execution and the initial Insight Agent have been developed.

Still in progress:
- Complete reproducibility testing
- Generative RAG enhancement
- Public Hugging Face dashboard
- Cloud-hosted hourly automation
- Final documentation and submission evidence

## Responsible Use

HeatSafe Pakistan provides informational heat-risk estimates and general safety guidance.

It does not replace official weather alerts, on-site risk assessments or professional medical advice.

## HackInsight 2026

Track: Climate and Heat-Health

Project: HeatSafe Pakistan
