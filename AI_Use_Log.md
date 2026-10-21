# HeatSafe Pakistan — AI Use Log

**Project:** HeatSafe Pakistan — HackInsight 2026
**Updated:** 21 October 2026 (PKT)
**Status:** Draft disclosure to be reviewed and completed by the team before final submission.

This log records material assistance from AI tools and separates generated suggestions from results actually verified by the team. Team members remain responsible for the code, source data, analysis, security, and claims in the submitted product.

| Tool / model | Task assisted | What was kept or changed | Human verification and limits | Team member who can explain |
|---|---|---|---|---|
| OpenAI ChatGPT (GPT-6; conversational assistant) | Project planning, Python data pipeline / DuckDB / Data Quality Agent development support, Prefect and Git troubleshooting, technical explanations | Suggestions and code were adapted into project files across the `pipelines/`, `rag/` and documentation folders; exact implementation history is in Git commits | Team executed scripts locally, inspected log outputs, kept baseline and raw/cleaned/gold provenance separately; specific scripts must be reviewed in the final repository | **Team to assign** |
| OpenAI ChatGPT (GPT-6) | Historical model evaluation interpretation, risk classes, baseline and uncertainty reporting | Drafted or helped polish `MODEL_CARD.md`, `DQ_REPORT.md`, and `OPTIMIZATION_LOG.md`; numerical results copied from recorded local evaluation JSON/CSV, not produced as invented estimates | Verified test MAE 1.055950°C vs predeclared baseline 1.107406°C in `model_metrics_20261020T083434210943Z.json`; DQ report still requires complete rule-level audit | **Team to assign** |
| OpenAI ChatGPT (GPT-6) | Gemini RAG integration and fallback safeguards | Assisted with `rag/generative_agent.py`, structured JSON output, refusal paths, citation ID checks and routing of verified live numerical forecasts to DuckDB | Manually tested a workplace heat-safety question (citations), unsupported Karachi deaths question (refusal), and current heat-index question (gold record); sentence-by-sentence claim verification and document section metadata remain incomplete | **Team to assign** |
| Google Gemini via Google AI Studio / Gemini API (`gemini-3.5-flash-lite` in committed code; verify runtime override) | Runtime generation of evidence-restricted workplace heat-safety answers | Used retrieved source chunks from CDC NIOSH, UK HSE and NOAA; outputs include matching chunk IDs and publisher URLs | Tested one grounded guidance response; numeric forecast facts bypass LLM and come from `gold.latest_live_ml`; refusal and fallback tested. **These checks do not prove all generated sentences are supported.** | **Team to assign** |
| OpenAI ChatGPT (GPT-6) | Multi-city weather feed, scripting and integration support | Assisted with `pipelines/multi_city_forecast.py` for 16-city, 48-hour Open-Meteo forecasts and manifest/CSV/JSON outputs | Team executed script: 16 cities, 768 rows, 0 rows flagged by its basic DQ checks. **These are provider forecasts, not validated city-specific HeatSafe ML predictions.** | **Team to assign** |
| OpenAI ChatGPT (GPT-6) | GitHub collaboration workflow and submission document drafting | Commands and pull-request guidance were followed and adapted; document drafts reviewed before final submission | Feature branch `feature/backend-finalization` pushed and merged by user; team should inspect exact final diff and validate deployed app | **Team to assign** |

## Disclosure of external systems, not generative-AI training sources

- **Data:** Open-Meteo interfaces providing ERA5 reanalysis and CAMS Global modelled air-quality data for historical datasets; a separate Open-Meteo weather forecast feed for sixteen cities. Licences, terms, provenance and independent-source eligibility need verification against `manifest.csv` and source registries.
- **Model training:** scikit-learn HistGradientBoosting; the target is weather-derived heat index 24 hours ahead. Gemini **did not train the ML model**.
- **Reference documents:** CDC NIOSH heat-stress recommendations, UK HSE heat-stress employer guidance, and NOAA NWS heat-index guidance. Verify original-page licences and chunk-to-section citations before final release.
- **Code libraries:** DuckDB, pandas, scikit-learn, Prefect, `google-genai` and other packages listed in `requirements.txt`.

## Human accountability, safety, and remaining actions

1. **Check every row in this disclosure** and add any AI tool actually used by the dashboard team, including design or code generation tools; remove claims that do not match the repository history.
2. Assign a **real team member** in the final column of every row; each must be able to explain and demonstrate the relevant code.
3. Review source licences, API usage and dependency licences. Do not commit `.env` files or API keys. Revoke any keys accidentally shared.
4. Inspect the complete Data Quality Agent logs, full Git diff and Prefect history before claiming full reproducibility or uninterrupted scheduling.
5. Mark draft/unverified features as such: the 16-city feed is weather-provider forecast only; the ML model is validated on historical Karachi data; human workplace exposure has not been observed.
6. Add a short dated change entry if other AI-assisted changes are made before the `v1.0-final` tag.

**Verification note:** This document is a transparent, evidence-based *draft* of known usage, not a claim that all AI-generated outputs were exhaustively validated. Final team review is required.
