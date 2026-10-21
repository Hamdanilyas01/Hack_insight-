# HeatSafe Pakistan website

The reference-video-inspired interface is an interactive evidence network with animated nodes, category filters, draggable/zoomable views, a forecast explorer, model evaluation, and the existing cited Insight Agent.

Private hosted snapshot: https://heatsafe-pakistan-observatory.goodsugar.chatgpt.site (ChatGPT sign-in required). The hosted version contains the saved evidence export; run locally for the warehouse and original agent integration.

## Run locally

From the repository root, with Python 3.11 or 3.12 and the project dependencies available:

```powershell
python website/server.py
```

Open http://127.0.0.1:8000. Change the port with `--port 8080`.

The local API reads `warehouse/heatsafe_live.duckdb` without modifying it. `/api/dashboard` returns the gold forecast and weather evidence. `/api/insight` calls `rag.insight_agent.HeatSafeInsightAgent`, retaining its source-integrity and freshness checks. No model retraining or pipeline execution is triggered by the website.

The original `.venv` points to a Python installation on the previous developer’s machine. Use your working Python installation or recreate the environment. For this machine, DuckDB was installed in the ignored `.web-deps/` folder; the website adds that directory to its Python path. Other agent dependencies (NumPy, pandas, scikit-learn) use the active Python environment. A minimal dependency list is in `requirements.txt` in this directory.

## Full Python backend on Vercel

Prepare a complete upload package from the repository root:

```powershell
python website/prepare_vercel.py
```

Upload **the whole `E:\heatsafe\vercel-upload` folder** or **`E:\heatsafe\heatsafe-vercel.zip`** to https://vercel.com/drop. This package contains a FastAPI entrypoint, the existing Python dashboard code, original retrieval agent/index, model evaluation files, public frontend, and a read-only copy of the DuckDB warehouse. Use automatic FastAPI detection, root `.`, and default build/output settings. Do not select only `dist` or `public`, and do not apply static-only Vercel settings.

After deployment, `/api/health` checks the real database and knowledge index. Look for `status: "ok"`, `storage.location: "vercel"`, and `retrieval.status: "ready"`. The assistant now runs in the Python backend, rather than returning the static site's five saved topic responses.

**Database location:** `warehouse/heatsafe_live.duckdb` is private deployment data inside the Vercel Python function bundle. DuckDB runs within that process and opens it read-only. It is not a separate cloud database service and does not reach back to your computer. Forecasts and historical data remain at their packaging snapshot. Refresh rereads that copy; updates require rebuilding and redeploying. No cloud Prefect ingestion or model execution is added by this packaging change.

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
- `test_server.py`: meaningful checks for evidence integrity, API behavior and safety boundaries

## Verification

```powershell
node --check website/dist/app.js
python -m unittest discover -s website -p test_server.py
```

No JavaScript dependency install or build step is required.
