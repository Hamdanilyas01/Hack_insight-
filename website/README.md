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
