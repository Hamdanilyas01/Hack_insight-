# HeatSafe Pakistan website

Karachi Pulse extends the reference-video-inspired evidence network with live modeled weather/AQI, line and area charts, hourly tables, daily weather icons, a single named KPI panel, and five persistent color themes. Network nodes/links/values are provided by the backend and rebuilt from each payload.

Private hosted snapshot: https://heatsafe-pakistan-observatory.goodsugar.chatgpt.site (ChatGPT sign-in required). The hosted version contains the saved evidence export; run locally for the warehouse and original agent integration.

## Run locally

From the repository root, with Python 3.11 or 3.12 and the project dependencies available:

```powershell
python website/server.py
```

Open http://127.0.0.1:8000. Change the port with `--port 8080`.

The local API reads `warehouse/heatsafe_live.duckdb` without modifying it. `/api/dashboard` combines the archived gold ML result, historical CAMS pollutant data, and live Open-Meteo weather/AQI responses. `/api/insight` uses server-side Gemini 3.5 Flash-Lite to explain current city weather/AQI, available history, model results, evaluations, and retrieved safety passages. It returns natural-language answers, source references, freshness context, and friendly fallbacks. No model retraining or Prefect pipeline execution is triggered by the website.

## Refresh behavior and data meaning

The visible page polls every five minutes. The backend caches successful upstream requests for 15 minutes per Python instance; failed sources use a 60-second retry backoff. Cold starts can fetch sooner. This is request-driven: no page/API traffic means no background updates. Vercel does not run the original `pipelines/serve_hourly.py` Prefect worker.

Weather and air-quality values are modeled estimates, not on-site sensor measurements. Top-right **Last source pull** reports a successful upstream fetch time. **Sources & updates** shows each feed's pull time, model valid time, backend response time, and the archived ML collection time separately. Missing/null values display as unavailable, never zero. Failed requests can show explicitly stale cache data or clearly dated archive data.

US AQI is requested directly from Open-Meteo's CAMS feed. The existing cleaned.cams_air_quality table supplies a separate historical monthly pollutant concentration chart. It is not converted to a fabricated current AQI. Live heat-index forecast lines are formula-based; the project's single +24-hour ML prediction remains a separate stored result.

The original `.venv` points to a Python installation on the previous developer’s machine. Use your working Python installation or recreate the environment. For this machine, DuckDB was installed in the ignored `.web-deps/` folder; the website adds that directory to its Python path. Other agent dependencies (NumPy, pandas, scikit-learn) use the active Python environment. A minimal dependency list is in `requirements.txt` in this directory.

## Full Python backend on Vercel

Prepare a complete upload package from the repository root:

```powershell
python website/prepare_vercel.py
```

Upload **the whole `E:\heatsafe\vercel-upload` folder** or **`E:\heatsafe\heatsafe-vercel.zip`** to https://vercel.com/drop. This package contains a FastAPI entrypoint, the existing Python dashboard code, original retrieval agent/index, model evaluation files, public frontend, and a read-only copy of the DuckDB warehouse. Use automatic FastAPI detection, root `.`, and default build/output settings. Do not select only `dist` or `public`, and do not apply static-only Vercel settings.

After deployment, `/api/health` checks the real database and knowledge index. Look for `status: "ok"`, `storage.location: "vercel"`, and `retrieval.status: "ready"`. The assistant now runs in the Python backend, rather than returning the static site's five saved topic responses.

**Database location:** `warehouse/heatsafe_live.duckdb` is private deployment data inside the Vercel Python function bundle. DuckDB runs within that process and opens it read-only. It does not reach back to your computer. Historical records and the stored ML result remain at their packaging snapshot; database updates require rebuilding and redeploying. Weather/AQI API requests happen separately and do not write to this file. No cloud Prefect worker or model execution is enabled.

The bundle does not include your virtual environment, original Git metadata, credentials, training datasets, or model binary. The trained model's existing forecast is read from DuckDB; no training dependencies beyond the retrieval agent's numerical libraries are needed. Detailed upload instructions are included in `vercel-upload/README.md`.

The package is generated from the maintained sources in `website/vercel/`, `website/server.py`, `website/dist/`, and the existing `rag/` and evaluation files. Edit those original sources and regenerate rather than editing the upload folder.

To verify the exact package in an environment with the deployment dependencies plus `httpx` installed:

```powershell
python website/verify_vercel.py
```

## Saved snapshot / static hosting

`dist/` is a buildless website. It works on any static HTTP server and includes an exported project snapshot and saved official guidance. It never exposes the Python server, DuckDB file or local project folders through static routes.

Regenerate the export from the local warehouse and the original retrieval agent:

```powershell
python website/server.py --export
```

Static hosting displays **Project snapshot** and uses topic-matched exported agent responses. It does not claim to run the Python agent or a live pipeline. Local mode uses the real agent and database. If the warehouse is unavailable, the interface falls back to the saved snapshot and labels it accordingly.

The formula-based hourly heat-index curve and the separate +24-hour ML point are deliberately labeled as different methods. All times are displayed in Asia/Karachi. Evidence older than three hours is marked stale. Historical prediction intervals are not a workplace safety guarantee.

## Files

- `dist/index.html`: application structure and SVG gradients
- `dist/style.css`: responsive layout, theme and reduced-motion support
- `dist/app.js`: evidence graph, panels, chart and assistant interactions
- `server.py`: read-only local API, static server and snapshot exporter
- `environment_data.py`: source validation, per-instance upstream cache, historical air queries and data-driven network schema
- `vercel/`: deployable FastAPI entrypoint, runtime requirements and instructions
- `test_server.py`: meaningful checks for evidence integrity, API behavior and safety boundaries

## Verification

```powershell
node --check website/dist/app.js
python -m unittest discover -s website -p 'test_*.py'
node --test website/test_frontend.cjs
```

No JavaScript dependency install or build step is required.

## Exploring the updated dashboard

- Overview is a full-width city summary combining weather and air outlooks. The top panel is explicitly labeled City now; its values and the central weather display remain the latest source conditions.
- Choose Last 7 days, Last 30 days, Last 6 months, or Custom range with sliders and precise date inputs. Reset to outlook returns to the next 24/48-hour outlook. The quick hour buttons also clear custom dates. Dates with no available data show an empty state. Network and Sources use current metadata and hide the slicer.
- Forecast has the only temperature/heat-index line chart, with a vertical model-valid boundary, shaded future region, dashed prediction lines, event pins and a visual hourly planning strip. Recent values are model estimates, not observed sensor measurements. The separate stored ML point is never presented as a continuously rerun prediction.
- Air quality has AQI, particle concentrations and a historical archive. Custom archive dates use daily means grouped by Karachi day; the default longer view uses the latest 12 monthly means. Animated dots are illustrative, not measurements. PM10 includes PM2.5; the values must not be added.
- Each tab changes the KPI panel. Selecting a network node highlights its direct incoming/outgoing links. Close the detail panel to clear the highlight. Animations respect reduced-motion preferences.
- The date selector filters available records; it does not trigger historical API backfills or change the read-only database/update schedule. Live responses include up to 12 recent model hours plus the future outlook.

## Gemini setup

The original local project loads `GEMINI_API_KEY` and `HEATSAFE_GEMINI_MODEL` from the ignored project-root `.env.local`, or process environment variables. Model: `gemini-3.5-flash-lite`. In Vercel, add those variables under Project Settings → Environment Variables and redeploy. Keys are excluded from frontend files and deployment packages. See `website/vercel/README.md` for configuration details. The assistant uses `/api/insight`; the original deterministic retrieval agent remains available for exports and compatibility.
