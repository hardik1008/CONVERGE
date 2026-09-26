# Kronos Copilot

Kronos Copilot is a local AI-powered financial time-series forecasting and validation dashboard built around the open-source Kronos foundation model.

It turns recent market data into future OHLCV forecasts, visualizes predicted market behaviour, and evaluates forecasts against historical reality.

> Research prototype only. Not financial advice.

## What it does

- Runs Kronos-base locally for financial time-series forecasting
- Supports NSE and BSE stocks
- Retrieves recent market data using yfinance
- Supports CSV market-data input
- Forecasts future OHLCV candles
- Supports 24, 75, and 120-bar forecast horizons
- Displays observed vs predicted candlesticks and volume
- Provides historical validation using hidden future data
- Calculates MAE, RMSE, MAPE, final error, and directional metrics
- Caches forecasts locally
- Supports optional OpenAI-generated explanations
- Runs through a local Python server and browser dashboard

## Architecture

User
→ Market Data
→ Kronos
→ Future OHLCV Forecast
→ Historical Validation
→ Metrics
→ Dashboard
→ Optional LLM Explanation

Kronos performs the numerical forecasting. The LLM does not generate the underlying market prediction.

## Kronos

This project uses the open-source Kronos financial foundation model.

Kronos itself is NOT included in this repository.

Official repository:
https://github.com/shiyu-coder/Kronos

After cloning Kronos Copilot, run:

setup_kronos.bat

The script downloads the official Kronos source into:

vendor/Kronos-master

The application then loads Kronos from that local directory.

## Setup

1. Clone this repository.
2. Install Git and Python if required.
3. Run `setup_kronos.bat`.
4. Create a Python virtual environment.
5. Install dependencies from `requirements.txt`.
6. Configure optional API credentials in `.env.local`.
7. Run `Start Kronos Copilot.bat`.

The launcher starts the local server, waits until the dashboard API is ready, and opens the dashboard in Google Chrome.

## Local profiles

The dashboard uses a passwordless local/demo profile to keep preferences and saved forecast data separate on this computer. This identifies a local profile but does not verify a real-world identity. The server is loopback-only; no production authentication provider is configured. See [docs/local_auth.md](docs/local_auth.md) for the storage and security boundary.

## Project structure

- `src/` — Kronos forecasting and configuration
- `app/` — backend server and dashboard
- `tools/` — project utilities
- `docs/` — research and project documentation
- `data/` — local/runtime market data (not committed)
- `outputs/` — generated forecasts and cache (not committed)
- `vendor/` — downloaded Kronos source (not committed)

## Privacy and API usage

Kronos inference runs locally.

OpenAI is optional and is used only for natural-language explanation of compact forecast summaries.

`.env.local`, local environments, runtime market data, generated forecasts, and the downloaded Kronos source are excluded from Git.

## Current status

Working research prototype.

Future development is planned around stronger walk-forward evaluation, improved financial data infrastructure, technical indicators, professional charting, current-news retrieval, and multi-agent market research.
