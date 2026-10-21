# Upload this whole folder to Vercel

This is the complete Karachi Pulse deployment: frontend + Python API + live modeled weather/AQI + cited safety retrieval + a read-only DuckDB archive.

1. Open https://vercel.com/drop and sign in.
2. Drag the entire `vercel-upload` folder into the upload area, or upload `heatsafe-vercel.zip`.
3. Let Vercel detect FastAPI. If settings are shown, use FastAPI, project root `.`, automatic dependency installation, and default build/output settings. Do not select `public` as the project root and do not use the static-only "Other" settings from the previous deployment.
4. Wait for the deployment to finish and open its URL.
5. Open `/api/health` on the same URL. `status: "ok"`, `storage.location: "vercel"` and `retrieval.status: "ready"` confirm the actual backend, bundled database, and retrieval index are running.
6. Ask "What is heat index and how does humidity affect it?". The response should contain the NOAA source citation.
7. Open Air quality and ask "What is the current AQI?". Values come from the public CAMS / Open-Meteo API, with source timestamps and a clear fallback if unavailable.

No API key or database password is required. No GitHub repository or local server is required after deployment.

## Where the database runs

The file `warehouse/heatsafe_live.duckdb` is bundled inside the Python function deployment, outside `public/`. DuckDB runs in the Vercel Python process and opens this file in read-only mode. It is not a separate hosted database service, and it is not your computer's live database. The database file cannot be downloaded through this app's public routes.

The packaged database is fixed at upload time. The app does not run Prefect ingestion, retrain the model, write new forecasts, or update this database in the cloud. Existing freshness checks remain in place: stale forecasts are labeled, and the agent refuses to call them current. General source-backed guidance continues to work when forecast data is stale.

## What updates automatically

- While open and visible, the website polls `/api/dashboard` every five minutes.
- The Python API requests Karachi weather and CAMS air-quality forecasts from Open-Meteo. Successful results are cached for 15 minutes per running Python instance. A failed source is retried on a subsequent request after a 60-second backoff. Different instances and cold starts have separate caches.
- These requests are triggered by website traffic, not a cloud cron. With no requests, no source refresh occurs. Provider model runs have their own update schedules; requesting every 15 minutes does not imply new model data every 15 minutes.
- The displayed top-right timestamp is the last successful source pull, not the time the page rendered. Sources & updates lists weather/AQI pull times, their model valid times, and the last backend response separately.
- The sources are model estimates, not on-site sensors. The live hourly heat-index curve uses the NWS formula with API weather inputs. The saved 24-hour ML prediction is kept in a separate, dated card.
- DuckDB remains read-only and its historical records/ML forecast do not change. Its CAMS pollutant history is displayed as monthly concentrations, not invented historical AQI.

## Interface

Karachi Pulse groups temperature, heat index, US AQI, humidity and wind KPIs in one navbar panel. Overview, Forecast and Air quality provide line/area charts, 24/48-hour controls, Celsius/Fahrenheit units, daily weather icons and minimal hourly tables. The Network view renders the nodes and links returned by the backend; missing metric fields are omitted and category counts follow the payload.

Theme choices: Forest, beige/maroon, white/orange/black, brown/beige, and white/purple/blue. The selected theme persists in browser local storage.

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

## Gemini assistant configuration

In Vercel, open Project Settings → Environment Variables and add `GEMINI_API_KEY`
with your Google AI Studio key. Set `HEATSAFE_GEMINI_MODEL` to
`gemini-3.5-flash-lite`, then redeploy. The secret is deliberately excluded from
the upload folder and ZIP. It must never be added to public JavaScript or JSON.

For local development, place those two variables in the project-root `.env.local`
(which Git ignores), or set them in the process environment. The original project
has already been configured locally. A separately extracted package needs its own
environment variables or its own root `.env.local`.

The website's `/api/insight` now uses server-side Gemini generation, grounded in
the selected city's current/recent/forecast weather, AQI and pollutant values,
available particulate history, saved ML result with freshness and target date,
model evaluation, pipeline metadata, and retrieved official safety passages.
Recent chat messages and the selected date range support follow-up questions.
Questions naming other supported cities can retrieve their evidence for comparison.
Answers include validated source references. Missing evidence, provider outages,
quota limits and missing configuration produce friendly responses.

Date controls now provide Last 12 hours, Last 7 days, Last 30 days and Custom dates
controls. Custom dates opens one calendar: click a start date, click an end date, then Apply range. Last 12 hours is a rolling timestamp window; day presets use
actual Pakistan calendar dates; they filter available records and do not backfill
missing history. Reset to outlook restores the existing 24/48-hour forecast.

Model documentation: https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite

## Local check of this exact package

With Python 3.12, install `requirements.txt`, then run from this folder:

```cmd
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and http://127.0.0.1:8000/api/health.

## Exploring the updated dashboard

- Overview is a full-width city summary combining weather and air outlooks. The top panel is explicitly labeled City now; its values and the central weather display remain the latest source conditions.
- Choose a preset card or Custom dates for a single start/end calendar. Reset to outlook returns to the next 24/48-hour outlook. The quick hour buttons also clear custom dates. Dates with no available data show an empty state. Network and Sources use current metadata and hide the slicer.
- Forecast has the only temperature/heat-index line chart, with a vertical model-valid boundary, shaded future region, dashed prediction lines, event pins and a visual hourly planning strip. Recent values are model estimates, not observed sensor measurements. The separate stored ML point is never presented as a continuously rerun prediction.
- Air quality has AQI, particle concentrations and a historical archive. Custom archive dates use daily means grouped by Karachi day; the default longer view uses the latest 12 monthly means. Animated dots are illustrative, not measurements. PM10 includes PM2.5; the values must not be added.
- Each tab changes the KPI panel. Selecting a network node highlights its direct incoming/outgoing links. Close the detail panel to clear the highlight. Animations respect reduced-motion preferences.
- The date selector filters available records; it does not trigger historical API backfills or change the read-only database/update schedule. Live responses include up to 12 recent model hours plus the future outlook.

## Small-context assistant tools

The Gemini request starts with the question, selected city/dates and a few short recent messages. It does not include the warehouse or bulk hourly/history arrays. Gemini can call these server-side functions:

- `lookup_environment`: up to four requested metrics, using current/forecast data or a parameterized read-only database lookup for explicit historical dates. A historical day returns count, mean, minimum, maximum and their times; an optional hour returns that hour. Date ranges are capped at 366 days.
- `get_model_insights`: one saved ML result, freshness/target date and a small evaluation summary.
- `search_safety_guidance`: two short relevant official passages.
- `get_project_info`: sources, cities, update behavior and navigation.

Calls are validated against allowed cities, metrics and dates. Gemini cannot submit SQL or file paths. Current deployed history is Karachi-specific; other cities report unavailable historical records rather than reusing Karachi. A missing year can be clarified before querying.

Tool results are bounded to 3,000 characters. A query allows at most four tool calls and three model requests, with a 20,000-character per-request cap and an 800-token output cap per model request. Recent chat text is limited to four messages of at most 400 characters each. Provider-reported counts are returned in `/api/insight` under `usage`, summed across planning and answer requests; `tools_used` identifies the function calls. Token use varies with the question.

Beige/maroon is the default for new browser preferences. The other themes remain selectable, and saved theme choices are respected. The question-mark button beside the theme button opens a simple guide to the KPIs, charts, navigation, date filter and assistant.
