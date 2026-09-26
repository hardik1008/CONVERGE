# Kronos Copilot developer baseline

This note describes the checked-in application and the local environment observed on 2026-09-26. The app is a research prototype: a Python HTTP server serves a static JavaScript dashboard, with local Kronos inference and optional server-side OpenAI explanations.

## Start locally

From the repository root on Windows, the documented direct command is:

```powershell
.\.venv\Scripts\python.exe app\server.py
```

Open [http://127.0.0.1:8000/app/dashboard.html](http://127.0.0.1:8000/app/dashboard.html). The server also prints that URL. `Start Kronos Copilot.bat` launches the same server and opens Chrome; it expects `.venv\Scripts\python.exe` and Chrome at a standard installation path. If Kronos source is absent, `setup_kronos.bat` clones the upstream repository into `vendor\Kronos-master`. The runtime model weights are fetched by Hugging Face on first inference.

Install the pinned top-level dependencies into a working virtual environment with:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` includes the upstream Kronos requirements file, OpenAI SDK, and yfinance. Python and Git are prerequisites. Live symbol search and market retrieval require network access; CSV forecasts avoid the Yahoo data fetch but still require the Kronos model and weights.

## Tests and checks

Focused repository-owned tests now cover local profile/session storage and protected HTTP routes:

```powershell
py -m unittest tests.test_local_auth -v
py -m unittest tests.test_auth_routes -v
```

Run all repository-owned tests with:

```powershell
py -m unittest discover -v
```

The focused HTTP tests import the application server and therefore need the top-level dependencies and vendored Kronos source installed. The upstream Kronos regression test remains a separate model evaluation and is not part of routine product tests. Small startup/import checks, once Python is available, are:

```powershell
.\.venv\Scripts\python.exe -m compileall app src tools
.\.venv\Scripts\python.exe -c "import app.server; print('server imports')"
```

Then start the server and check the static dashboard and API:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/app/dashboard.html
Invoke-WebRequest http://127.0.0.1:8000/api/auth/session
```

After local sign-in, `GET /api/dashboard` is the protected forecast-flow check.

The existing upstream Kronos regression test is a model evaluation, not a Copilot product test:

```powershell
.\.venv\Scripts\python.exe -m pytest vendor/Kronos-master/tests/test_kronos_regression.py -v
```

It loads the small Kronos model and tokenizer from Hugging Face at pinned revisions, runs CPU inference against upstream regression fixtures, and may download model weights. Do not substitute this for the protected final/locked evaluation. The repository contains no visible app-focused automated regression coverage. The audit script `tools/ox_alpha_audit.py` is a network-backed report generator, not a test suite; running it writes `docs/ox_alpha_audit.md`.

## Entry points and views

- `app/server.py` — HTTP server, API handlers, CSV validation, Yahoo Finance market retrieval, dashboard payloads, validation metrics, forecast caching, and optional OpenAI explanation.
- `app/dashboard.html`, `app/dashboard.js`, `app/dashboard.css` — single static dashboard view; no client-side router or frontend build step.
- `src/first_forecast.py` — Kronos model loading and `run_kronos_forecast()` inference entry point.
- `src/forecast_config.py` — lookback, allowed horizons, sampling defaults, and NSE session/timezone timestamp logic.
- `tools/ox_alpha_audit.py` — separate network-backed architecture audit tool.
- `README.md`, `How to run project`, and `docs/ox_alpha_audit.md` — project overview, startup note, and prior architecture/security audit.

The dashboard’s single route is `/app/dashboard.html`. It begins at the local/demo sign-in screen, then shows live NSE/BSE symbol search and forecast controls, CSV forecast controls, observed/forecast chart with candles or price-line mode, summary metrics, historical performance, profile settings, and optional explanation. The same page switches between forecast, empty, and error states; these are UI states, not separate routes. Public auth bootstrap is `GET /api/auth/session`; sign-in and sign-out are `POST /api/auth/sign-in` and `/api/auth/sign-out`. Protected profile routes are `GET` and `POST /api/auth/profile`. Protected product routes include `GET /api/dashboard`, `/api/symbol-search`, `/api/explanation`, and `POST /api/forecast`, `/api/validation`, `/api/live-forecast`, `/api/live-validation`, `/api/explanation`.

The server binds to `127.0.0.1:8000` in local/demo mode and serves static assets only from `app/`. It does not expose LAN access.

## Market data and forecast boundaries

Market data is currently acquired directly in `app/server.py`: `fetch_live_market_data()` calls `yf.Ticker(...).history(...)`, and `symbol_search()` calls `yf.Search(...)`. CSV upload is handled by `validate_market_csv()` and `normalize_market_data()` in that same module. There is not yet a separate market-data adapter/module. Yahoo candles are mapped into the application’s `timestamps`, OHLC, `volume`, and derived `amount` fields before forecasting.

Forecast execution flows from the dashboard API handlers through `run_forecast()`/`run_validation()` in `app/server.py` into `run_kronos_forecast()` in `src/first_forecast.py`. The model loader imports the vendored source at `vendor/Kronos-master`, fetches `NeoQuasar/Kronos-base` and its tokenizer, and runs inference locally. Optional OpenAI explanation code consumes a compact forecast summary and does not generate the numerical forecast.

Local auth and per-profile forecast storage are documented in [local_auth.md](local_auth.md). Profiles/sessions and user forecast artifacts use the existing ignored `outputs/` filesystem storage; no separate database was added.

## Environment variables and local files

- `OPENAI_API_KEY` — optional explanation feature; read from the server environment or `.env.local` and never needed for market retrieval or Kronos inference.
- `KRONOS_INPUT_PATH`, `KRONOS_INPUT_LABEL` — optional forecast input path and label.
- `KRONOS_FORECAST_BARS`, `KRONOS_FORECAST_HORIZON` — optional allowed forecast horizon selection.
- `KRONOS_T`, `KRONOS_TOP_K`, `KRONOS_TOP_P`, `KRONOS_SAMPLE_COUNT` — optional inference sampling settings.
- `KRONOS_YAHOO_RETRIEVED_AT` — internal retrieval-time provenance passed into forecast summaries.
- Local auth has no password, signing key, or API-key setting. Its session cookie is an opaque random bearer token; only its hash is persisted in ignored `outputs/local_profiles.json`.

No secret values belong in source files, docs, or browser code. Keep credentials in environment variables or the ignored `.env.local`. `.gitignore` excludes `.env.local`, `.venv/`, runtime `data/` and `outputs/`, and `vendor/`.

## Scientific and evaluation protection

Treat all upstream evaluation fixtures and definitions under `vendor/Kronos-master/tests/` as protected scientific assets. This includes `test_kronos_regression.py`, `tests/data/regression_input.csv`, and the expected `regression_output_*.csv` files. Preserve their contents, model/tokenizer revisions, test tolerances, and benchmark/split definitions. Do not run a final locked test during product feature work. The repository does not expose a separately named locked-test dataset or final evaluation command in tracked files; confirm with the project owner before treating any external/ignored evaluation asset as available. Forecast configuration in `src/forecast_config.py` also affects evaluation reproducibility and must not be changed for UI work. Generated files under `outputs/` and uploaded data under `data/` are runtime artifacts, not benchmark fixtures.

## Baseline verification on 2026-09-26

- The checked-in `.venv` launcher fails with `No Python at '"C:\Users\Asus\AppData\Local\Programs\Python\Python311\python.exe'`; neither `python` nor `py` is available in the current shell.
- Consequently, pytest discovery, unittest discovery, app import/compile checks, and server startup could not execute. The commands stop at interpreter launch, before app code or tests run.
- Port 8000 had no listening server. Requests to the dashboard and `/api/dashboard` could not connect. The documented local URL is `http://127.0.0.1:8000/app/dashboard.html`, but it was not live during this verification.
- An existing ignored `outputs/server.stderr.log` records earlier dashboard static-file responses and a prior `FileNotFoundError` when a forecast request attempted to write `data/uploaded_market_data.csv` while its parent directory was absent. This is historical log evidence, not a failure reproduced in this baseline run.
