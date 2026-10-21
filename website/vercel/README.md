# Upload this whole folder to Vercel

This is the complete HeatSafe deployment: frontend + Python API + original retrieval assistant + a read-only DuckDB snapshot.

1. Open https://vercel.com/drop and sign in.
2. Drag the entire `vercel-upload` folder into the upload area, or upload `heatsafe-vercel.zip`.
3. Let Vercel detect FastAPI. If settings are shown, use FastAPI, project root `.`, automatic dependency installation, and default build/output settings. Do not select `public` as the project root and do not use the static-only "Other" settings from the previous deployment.
4. Wait for the deployment to finish and open its URL.
5. Open `/api/health` on the same URL. `status: "ok"`, `storage.location: "vercel"` and `retrieval.status: "ready"` confirm the actual backend, bundled database, and retrieval index are running.
6. Ask "What is heat index and how does humidity affect it?". The response should contain the NOAA source citation.

No API key or database password is required. No GitHub repository or local server is required after deployment.

## Where the database runs

The file `warehouse/heatsafe_live.duckdb` is bundled inside the Python function deployment, outside `public/`. DuckDB runs in the Vercel Python process and opens this file in read-only mode. It is not a separate hosted database service, and it is not your computer's live database. The database file cannot be downloaded through this app's public routes.

The packaged database is fixed at upload time. The app does not run Prefect ingestion, retrain the model, write new forecasts, or update this database in the cloud. Existing freshness checks remain in place: stale forecasts are labeled, and the agent refuses to call them current. General source-backed guidance continues to work when forecast data is stale.

## Updating data or code

Update the original project on your computer, then run from `E:\heatsafe`:

```cmd
python website/prepare_vercel.py
```

Upload the regenerated package to Vercel. Vercel Drop may create a new project; to update an existing project through the CLI without GitHub:

```cmd
cd /d E:\heatsafe\vercel-upload
npx vercel@latest link
npx vercel@latest --prod
```

Select your existing Vercel project when linking. A continuously updating cloud warehouse requires an additional ingestion/storage design; it is not enabled by this package.

## Local check of this exact package

With Python 3.12, install `requirements.txt`, then run from this folder:

```cmd
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and http://127.0.0.1:8000/api/health.
