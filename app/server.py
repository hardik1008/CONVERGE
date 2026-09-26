"""Local dashboard server with an optional, cached OpenAI explanation endpoint."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import yfinance as yf
from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from app.local_auth import LocalAuthStore, SESSION_COOKIE, SESSION_MAX_AGE_SECONDS
from forecast_config import (
    DEFAULT_SAMPLE_COUNT,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_K,
    DEFAULT_TOP_P,
    FORECAST_BARS,
    INTERVAL_MINUTES,
    LOOKBACK_BARS,
    MARKET_TIMEZONE,
    MARKET_TIMEZONE_LABEL,
    SESSION_END,
    SESSION_START,
    coerce_market_timestamps,
    ensure_market_timestamp,
    resolve_forecast_bars,
    trading_duration_label,
)
from first_forecast import FEATURES, MODEL_NAME as KRONOS_MODEL_NAME, build_summary, predict_with_kronos, run_kronos_forecast

OUTPUTS_ROOT = PROJECT_ROOT / "outputs"
USER_OUTPUTS_ROOT = OUTPUTS_ROOT / "users"
AUTH_STORE = LocalAuthStore(OUTPUTS_ROOT / "local_profiles.json")
CURRENT_USER_ID: ContextVar[str | None] = ContextVar("kronos_current_user_id", default=None)
ENV_PATH = PROJECT_ROOT / ".env.local"
MODEL_NAME = "gpt-5-mini"
EXPLANATION_PROMPT_VERSION = "3"
FORECAST_LOCK = threading.Lock()
EXPLANATION_LOCK = threading.Lock()
REQUIRED_COLUMNS = ["timestamps", "open", "high", "low", "close", "volume", "amount"]
SYMBOL_SEARCH_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, str]]]] = {}
SYMBOL_SEARCH_TTL_SECONDS = 300


def current_output_dir() -> Path:
    user_id = CURRENT_USER_ID.get()
    if not user_id:
        return OUTPUTS_ROOT
    if not re.fullmatch(r"[a-f0-9]{64}", user_id):
        raise ValueError("The local profile identifier is invalid.")
    return USER_OUTPUTS_ROOT / user_id


def output_path(name: str) -> Path:
    return current_output_dir() / name


def load_local_key() -> None:
    """Load the local key without placing it in browser-visible code."""
    if os.environ.get("OPENAI_API_KEY") or not ENV_PATH.exists():
        return

    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENAI_API_KEY="):
            os.environ["OPENAI_API_KEY"] = line.split("=", 1)[1].strip().strip('"')
            return


def load_summary() -> dict[str, object]:
    summary_path = output_path("forecast_summary.json")
    if not summary_path.exists():
        raise FileNotFoundError("Run the local Kronos forecast before requesting an explanation.")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def forecast_identity(summary: dict[str, object]) -> dict[str, object]:
    """Return the stable fields that bind an explanation to one exact forecast."""
    input_source = str(summary.get("input_source", ""))
    _, normalized_symbol, exchange, source_type = source_identity(input_source)
    uploaded_data_path = output_path("uploaded_market_data.csv")
    forecast_path = output_path("forecast.csv")
    summary_path = output_path("forecast_summary.json")
    history = pd.read_csv(uploaded_data_path) if uploaded_data_path.exists() else pd.DataFrame()
    timestamps = (
        coerce_market_timestamps(history.get("timestamps")).dropna()
        if "timestamps" in history
        else pd.Series(dtype="datetime64[ns]")
    )
    last_observed_timestamp = timestamps.iloc[-1].isoformat() if not timestamps.empty else ""
    forecast = pd.read_csv(forecast_path) if forecast_path.exists() else pd.DataFrame()
    forecast_timestamps = (
        coerce_market_timestamps(forecast.get("timestamps")).dropna()
        if "timestamps" in forecast
        else pd.Series(dtype="datetime64[ns]")
    )
    forecast_hash = (
        hashlib.sha256(forecast_path.read_bytes()).hexdigest()
        if forecast_path.exists()
        else ""
    )
    return {
        "normalized_symbol": normalized_symbol,
        "exchange": exchange or "CSV",
        "data_source": source_type,
        "interval_minutes": INTERVAL_MINUTES,
        "last_observed_timestamp": last_observed_timestamp,
        "first_forecast_timestamp": forecast_timestamps.iloc[0].isoformat() if not forecast_timestamps.empty else "",
        "final_forecast_timestamp": forecast_timestamps.iloc[-1].isoformat() if not forecast_timestamps.empty else "",
        "forecast_count": int(summary.get("forecast_rows", 0) or 0),
        "forecast_version": datetime.fromtimestamp(
            summary_path.stat().st_mtime, tz=timezone.utc
        ).isoformat() if summary_path.exists() else "",
        "kronos_model": summary.get("model"),
        "last_observed_price": summary.get("last_observed_close"),
        "final_forecast_price": summary.get("forecast_final_close"),
        "movement_percentage": summary.get("forecast_pct_change"),
        "forecast_low": summary.get("forecast_min_close"),
        "forecast_high": summary.get("forecast_max_close"),
        "direction": summary.get("direction"),
        "forecast_output_hash": forecast_hash,
        "explanation_prompt_version": EXPLANATION_PROMPT_VERSION,
    }


def summary_fingerprint(summary: dict[str, object]) -> str:
    serialized = json.dumps(forecast_identity(summary), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compact_explanation_summary(summary: dict[str, object]) -> dict[str, object]:
    identity = forecast_identity(summary)
    last_price = float(summary.get("last_observed_close", 0) or 0)
    range_span = float(summary.get("forecast_max_close", 0) or 0) - float(
        summary.get("forecast_min_close", 0) or 0
    )
    compact = {
        "symbol": identity["normalized_symbol"],
        "exchange": identity["exchange"],
        "data_source": identity["data_source"],
        "currency": "INR" if identity["exchange"] in {"NSE", "BSE"} else "unspecified",
        "interval_minutes": identity["interval_minutes"],
        "observed_bars": summary.get("input_rows"),
        "forecast_horizon": summary.get("forecast_horizon_label", "Next 75 market bars"),
        "forecast_bars": summary.get("forecast_rows"),
        "last_observed_timestamp": identity["last_observed_timestamp"],
        "forecast_start": identity["first_forecast_timestamp"],
        "forecast_end": identity["final_forecast_timestamp"],
        "last_observed_price": identity["last_observed_price"],
        "final_forecast_price": identity["final_forecast_price"],
        "movement_percentage": identity["movement_percentage"],
        "direction": identity["direction"],
        "forecast_low": identity["forecast_low"],
        "forecast_high": identity["forecast_high"],
        "forecast_range_percent_of_last": round(range_span / last_price * 100, 4) if last_price else None,
        "forecast_close_std": summary.get("forecast_close_std"),
        "forecast_range_pct": summary.get("forecast_range_pct"),
        "sampling_T": summary.get("sampling_T"),
        "sampling_top_p": summary.get("sampling_top_p"),
        "sample_count": summary.get("sample_count"),
        "inference_time_seconds": summary.get("inference_time_seconds"),
        "kronos_model": identity["kronos_model"],
        "forecast_timestamp": identity["forecast_version"],
    }
    if summary.get("mode") == "validation":
        compact.update({
            "validation_mode": True,
            "actual_final_price": summary.get("actual_final_close"),
            "actual_movement_percentage": summary.get("actual_pct_change"),
            "mae": summary.get("mae"),
            "rmse": summary.get("rmse"),
            "final_error_pct": summary.get("final_error_pct"),
            "directional_match": summary.get("directional_match"),
            "directional_agreement_pct": summary.get("directional_agreement_pct"),
        })
    return compact


def load_cached_explanation(summary: dict[str, object]) -> dict[str, object] | None:
    cache_path = output_path("explanation.json")
    if not cache_path.exists():
        return None
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    if (
        cached.get("summary_fingerprint") == summary_fingerprint(summary)
        and cached.get("prompt_version") == EXPLANATION_PROMPT_VERSION
    ):
        return cached
    return None


def format_explanation_currency(explanation: str, summary: dict[str, object]) -> str:
    """Use polished currency notation only when the market identity is known."""
    _, _, exchange, _ = source_identity(str(summary.get("input_source", "")))
    if exchange not in {"NSE", "BSE"}:
        return explanation
    formatted = re.sub(r"\bINR\s*([0-9][0-9,]*(?:\.[0-9]+)?)", r"₹\1", explanation)
    formatted = re.sub(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*INR\b", r"₹\1", formatted)
    return re.sub(r"(?<![₹0-9])([0-9][0-9,]*(?:\.[0-9]+)?)([–-])(?=₹)", r"₹\1\2", formatted)


def generate_explanation(requested_fingerprint: str | None = None) -> dict[str, object]:
    with EXPLANATION_LOCK:
        return _generate_explanation(requested_fingerprint)


def _generate_explanation(requested_fingerprint: str | None = None) -> dict[str, object]:
    summary = load_summary()
    current_fingerprint = summary_fingerprint(summary)
    if requested_fingerprint and requested_fingerprint != current_fingerprint:
        raise RuntimeError("This explanation no longer matches the current forecast.")
    cached = load_cached_explanation(summary)
    if cached:
        return {
            "explanation": format_explanation_currency(cached["explanation"], summary),
            "cached": True,
            "model": cached["model"],
            "summary_fingerprint": current_fingerprint,
        }

    load_local_key()
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("A local OpenAI API key is required for this optional feature.")

    explanation_summary = compact_explanation_summary(summary)

    client = OpenAI()
    response = client.responses.create(
        model=MODEL_NAME,
        instructions=(
            "Explain this compact Kronos-base forecast in 90-120 plain-language words. "
            "Include direction, percentage movement, range, practical meaning, and one uncertainty sentence. "
            "State that Kronos-base generated the forecast locally and that it is not investment advice. "
            "Do not add market facts, recommendations, or claims beyond the supplied JSON. "
            "When currency is INR, use the rupee symbol ₹ instead of the letters INR. "
            "When currency is unspecified, "
            "do not add any currency name or symbol."
        ),
        input=json.dumps(explanation_summary, separators=(",", ":")),
        reasoning={"effort": "minimal"},
        text={"verbosity": "low"},
        max_output_tokens=180,
    )
    explanation = format_explanation_currency(response.output_text.strip(), summary)
    if summary_fingerprint(load_summary()) != current_fingerprint:
        raise RuntimeError("The forecast changed while the explanation was being created.")
    result = {
        "summary_fingerprint": current_fingerprint,
        "prompt_version": EXPLANATION_PROMPT_VERSION,
        "explanation": explanation,
        "model": MODEL_NAME,
    }
    output_path("explanation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {
        "explanation": explanation,
        "cached": False,
        "model": MODEL_NAME,
        "summary_fingerprint": current_fingerprint,
    }


def validate_market_csv(csv_text: str) -> pd.DataFrame:
    """Return a readable market frame with beginner-friendly validation errors."""
    try:
        market_data = pd.read_csv(io.StringIO(csv_text))
    except Exception as error:
        raise ValueError("This CSV could not be read. Export it as a standard comma-separated file and try again.") from error

    missing_columns = [column for column in REQUIRED_COLUMNS if column not in market_data.columns]
    if missing_columns:
        raise ValueError(f"CSV is missing required columns: {', '.join(missing_columns)}.")
    valid_rows = market_data[REQUIRED_COLUMNS].dropna()
    if len(valid_rows) < LOOKBACK_BARS:
        raise ValueError(f"Kronos needs {LOOKBACK_BARS} valid market bars. This source returned {len(valid_rows)}.")
    return market_data


def normalize_market_data(market_data: pd.DataFrame) -> pd.DataFrame:
    clean_data = market_data[REQUIRED_COLUMNS].dropna().copy()
    clean_data["timestamps"] = coerce_market_timestamps(clean_data["timestamps"])
    return clean_data.sort_values("timestamps").drop_duplicates("timestamps").reset_index(drop=True)


def forecast_cache_key(
    market_data: pd.DataFrame,
    *,
    mode: str,
    input_label: str,
    forecast_bars: int,
    temperature: float = DEFAULT_TEMPERATURE,
    top_k: int = DEFAULT_TOP_K,
    top_p: float = DEFAULT_TOP_P,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
) -> str:
    normalized = normalize_market_data(market_data)
    stable_data = pd.DataFrame({
        "timestamps": normalized["timestamps"].map(iso_timestamp),
        "open": normalized["open"].astype(float).round(6),
        "high": normalized["high"].astype(float).round(6),
        "low": normalized["low"].astype(float).round(6),
        "close": normalized["close"].astype(float).round(6),
        "volume": normalized["volume"].astype(float).round(3),
        "amount": normalized["amount"].astype(float).round(3),
    })
    data_hash = hashlib.sha256(stable_data.to_csv(index=False).encode("utf-8")).hexdigest()
    identity = {
        "mode": mode,
        "input_label": input_label,
        "data_hash": data_hash,
        "last_timestamp": iso_timestamp(normalized["timestamps"].iloc[-1]) if not normalized.empty else "",
        "lookback": LOOKBACK_BARS,
        "prediction_length": forecast_bars,
        "model": KRONOS_MODEL_NAME,
        "T": temperature,
        "top_k": top_k,
        "top_p": top_p,
        "sample_count": sample_count,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def cache_paths(key: str) -> tuple[Path, Path, Path]:
    directory = current_output_dir() / "forecast_cache" / key
    return directory / "forecast.csv", directory / "summary.json", directory / "validation_actual.csv"


def restore_forecast_cache(key: str, validation: bool = False) -> dict[str, object] | None:
    forecast_cache, summary_cache, actual_cache = cache_paths(key)
    if not forecast_cache.exists() or not summary_cache.exists():
        return None
    (current_output_dir() / "forecast_cache").mkdir(parents=True, exist_ok=True)
    shutil.copy2(forecast_cache, output_path("forecast.csv"))
    shutil.copy2(summary_cache, output_path("forecast_summary.json"))
    if validation and actual_cache.exists():
        shutil.copy2(actual_cache, output_path("validation_actual.csv"))
    summary_path = output_path("forecast_summary.json")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["cache_hit"] = True
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def save_forecast_cache(key: str, validation: bool = False) -> None:
    directory = current_output_dir() / "forecast_cache" / key
    directory.mkdir(parents=True, exist_ok=True)
    forecast_cache, summary_cache, actual_cache = cache_paths(key)
    forecast_path = output_path("forecast.csv")
    summary_path = output_path("forecast_summary.json")
    actual_path = output_path("validation_actual.csv")
    if forecast_path.exists():
        shutil.copy2(forecast_path, forecast_cache)
    if summary_path.exists():
        shutil.copy2(summary_path, summary_cache)
    if validation and actual_path.exists():
        shutil.copy2(actual_path, actual_cache)


def validation_metrics(predicted: pd.DataFrame, actual: pd.DataFrame, context: pd.DataFrame) -> dict[str, object]:
    joined = predicted[["timestamps", "close"]].rename(columns={"close": "predicted_close"}).merge(
        actual[["timestamps", "close"]].rename(columns={"close": "actual_close"}),
        on="timestamps",
        how="inner",
    )
    if joined.empty:
        raise ValueError("Validation could not align predicted bars with the hidden actual future bars.")
    error = joined["predicted_close"] - joined["actual_close"]
    mae = float(error.abs().mean())
    rmse = float((error.pow(2).mean()) ** 0.5)
    actual_final = float(joined["actual_close"].iloc[-1])
    predicted_final = float(joined["predicted_close"].iloc[-1])
    context_final = float(context["close"].iloc[-1])
    actual_move = actual_final - context_final
    predicted_move = predicted_final - context_final
    actual_direction = "up" if actual_move >= 0 else "down"
    predicted_direction = "up" if predicted_move >= 0 else "down"
    actual_steps = joined["actual_close"].diff().iloc[1:]
    predicted_steps = joined["predicted_close"].diff().iloc[1:]
    step_mask = actual_steps.ne(0) & predicted_steps.ne(0)
    directional_agreement = None
    if step_mask.any():
        directional_agreement = round(float((actual_steps[step_mask].gt(0) == predicted_steps[step_mask].gt(0)).mean() * 100), 2)
    return {
        "mode": "validation",
        "actual_rows": int(len(actual)),
        "compared_rows": int(len(joined)),
        "actual_final_close": round(actual_final, 4),
        "actual_pct_change": round((actual_final - context_final) / context_final * 100, 3) if context_final else None,
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "mape": round(float((error.abs() / joined["actual_close"].abs()).mean() * 100), 4) if (joined["actual_close"].abs() > 0).all() else None,
        "final_error_pct": round(abs(predicted_final - actual_final) / actual_final * 100, 4) if actual_final else None,
        "directional_match": predicted_direction == actual_direction,
        "actual_direction": actual_direction,
        "predicted_direction": predicted_direction,
        "directional_agreement_pct": directional_agreement,
    }


def run_validation(
    csv_text: str,
    input_label: str = "uploaded_market_data.csv",
    request_id: int | str | None = None,
    forecast_bars: int | str | None = None,
) -> dict[str, object]:
    if not csv_text.strip():
        raise ValueError("Select a CSV file before starting validation.")
    market_data = normalize_market_data(validate_market_csv(csv_text))
    forecast_bars = resolve_forecast_bars(forecast_bars)
    cache_key = forecast_cache_key(
        market_data,
        mode="validation",
        input_label=input_label,
        forecast_bars=forecast_bars,
    )
    required = LOOKBACK_BARS + forecast_bars
    if len(market_data) < required:
        raise ValueError(f"Validation needs {required} valid market bars: {LOOKBACK_BARS} context bars plus {forecast_bars} hidden future bars.")
    context = market_data.iloc[-required:-forecast_bars].reset_index(drop=True)
    actual = market_data.iloc[-forecast_bars:].reset_index(drop=True)

    with FORECAST_LOCK:
        cached_summary = restore_forecast_cache(cache_key, validation=True)
        if cached_summary:
            return build_dashboard_payload(cached_summary, context, request_id)
        uploaded_data_path = output_path("uploaded_market_data.csv")
        uploaded_data_path.parent.mkdir(parents=True, exist_ok=True)
        uploaded_data_path.write_text(market_data.to_csv(index=False), encoding="utf-8")
        forecast, inference_seconds = predict_with_kronos(
            context,
            actual["timestamps"],
            forecast_bars,
            temperature=DEFAULT_TEMPERATURE,
            top_k=DEFAULT_TOP_K,
            top_p=DEFAULT_TOP_P,
            sample_count=DEFAULT_SAMPLE_COUNT,
        )
        forecast.to_csv(output_path("forecast.csv"), index=False)
        actual.to_csv(output_path("validation_actual.csv"), index=False)
        summary = build_summary(
            context,
            forecast,
            f"{Path(input_label).name} validation",
            forecast_bars=forecast_bars,
            temperature=DEFAULT_TEMPERATURE,
            top_k=DEFAULT_TOP_K,
            top_p=DEFAULT_TOP_P,
            sample_count=DEFAULT_SAMPLE_COUNT,
            inference_seconds=inference_seconds,
        )
        summary.update(validation_metrics(forecast, actual, context))
        summary["cache_hit"] = False
        output_path("forecast_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        save_forecast_cache(cache_key, validation=True)
        payload = build_dashboard_payload(summary, context, request_id)
        return payload


def source_identity(input_source: str) -> tuple[str, str, str | None, str]:
    live_match = re.match(r"^([A-Z0-9.^=\-]+)\.(NS|BO) live", input_source, re.IGNORECASE)
    if live_match:
        normalized_symbol = f"{live_match.group(1).upper()}.{live_match.group(2).upper()}"
        exchange = "NSE" if normalized_symbol.endswith(".NS") else "BSE"
        return normalized_symbol.split(".", 1)[0], normalized_symbol, exchange, "live"
    display_name = Path(input_source).stem or "CSV forecast"
    return display_name, display_name, None, "csv"


def normalize_exchange(value: str) -> str:
    exchange = value.strip().upper()
    return exchange if exchange in {"NSE", "BSE"} else "NSE"


def result_exchange(symbol: str, quote: dict[str, object]) -> str | None:
    upper_symbol = symbol.upper()
    exchange_hint = str(quote.get("exchange") or quote.get("exchDisp") or "").upper()
    if upper_symbol.endswith(".NS") or exchange_hint in {"NSE", "NSI"}:
        return "NSE"
    if upper_symbol.endswith(".BO") or exchange_hint in {"BSE", "BOM", "BSE LTD"}:
        return "BSE"
    return None


def normalize_symbol_result(quote: dict[str, object]) -> dict[str, str] | None:
    symbol = str(quote.get("symbol") or "").strip().upper()
    exchange = result_exchange(symbol, quote)
    quote_type = str(quote.get("quoteType") or quote.get("typeDisp") or "").lower()
    if not symbol or exchange not in {"NSE", "BSE"}:
        return None
    if quote_type and quote_type not in {"equity", "stock"}:
        return None
    if not re.fullmatch(r"[A-Z0-9.^=\-]+(\.NS|\.BO)", symbol):
        return None
    name = str(
        quote.get("longname")
        or quote.get("shortname")
        or quote.get("name")
        or symbol
    ).strip()
    return {
        "symbol": symbol,
        "baseSymbol": symbol.rsplit(".", 1)[0],
        "name": name,
        "exchange": exchange,
        "type": "Equity",
    }


def direct_symbol_result(query: str, preferred_exchange: str) -> dict[str, str] | None:
    clean = query.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.^=\-]{1,20}(\.(NS|BO))?", clean):
        return None
    if clean.endswith((".NS", ".BO")):
        symbol = clean
        exchange = "NSE" if clean.endswith(".NS") else "BSE"
    else:
        exchange = preferred_exchange
        symbol = f"{clean}{'.NS' if exchange == 'NSE' else '.BO'}"
    return {
        "symbol": symbol,
        "baseSymbol": symbol.rsplit(".", 1)[0],
        "name": symbol.rsplit(".", 1)[0],
        "exchange": exchange,
        "type": "Equity",
    }


def search_score(result: dict[str, str], query: str, preferred_exchange: str) -> tuple[int, str]:
    compact_query = re.sub(r"\s+", " ", query.strip().upper())
    name = result["name"].upper()
    symbol = result["symbol"].upper()
    base = result["baseSymbol"].upper()
    score = 0
    if result["exchange"] == preferred_exchange:
        score -= 20
    if compact_query == symbol:
        score -= 100
    elif compact_query == base:
        score -= 90
    elif compact_query == name:
        score -= 80
    elif symbol.startswith(compact_query) or base.startswith(compact_query):
        score -= 70
    elif name.startswith(compact_query):
        score -= 60
    elif compact_query in name:
        score -= 40
    return score, result["symbol"]


def symbol_search(query: str, exchange: str) -> list[dict[str, str]]:
    cleaned = re.sub(r"\s+", " ", query.strip())
    preferred_exchange = normalize_exchange(exchange)
    if len(cleaned) < 2:
        return []
    cache_key = (cleaned.lower(), preferred_exchange)
    cached = SYMBOL_SEARCH_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < SYMBOL_SEARCH_TTL_SECONDS:
        return cached[1]

    results: list[dict[str, str]] = []
    direct = direct_symbol_result(cleaned, preferred_exchange)
    if direct:
        results.append(direct)

    try:
        search = yf.Search(
            cleaned,
            max_results=12,
            news_count=0,
            lists_count=0,
            include_research=False,
            include_cultural_assets=False,
            timeout=5,
            raise_errors=False,
        )
        for quote in search.quotes or []:
            normalized = normalize_symbol_result(quote)
            if normalized:
                results.append(normalized)
    except Exception:
        pass

    unique: dict[str, dict[str, str]] = {}
    for result in results:
        unique[result["symbol"]] = result
    ranked = sorted(unique.values(), key=lambda item: search_score(item, cleaned, preferred_exchange))[:8]
    SYMBOL_SEARCH_CACHE[cache_key] = (time.time(), ranked)
    return ranked


def iso_timestamp(value: object) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return ensure_market_timestamp(parsed).isoformat()


def market_point(row: pd.Series) -> dict[str, object]:
    point: dict[str, object] = {"timestamp": iso_timestamp(row["timestamps"])}
    for column in ("open", "high", "low", "close", "volume", "amount"):
        if column in row and pd.notna(row[column]):
            point[column] = float(row[column])
    return point


def is_market_open(now: pd.Timestamp) -> bool:
    if now.weekday() >= 5:
        return False
    current = now.time()
    return SESSION_START <= current < SESSION_END


def format_ist_timestamp(timestamp: pd.Timestamp) -> str:
    return ensure_market_timestamp(timestamp).strftime("%d %b %Y, %I:%M %p IST").replace(" 0", " ")


def freshness_message(latest_bar: pd.Timestamp | None, source_type: str) -> str:
    if source_type != "live" or latest_bar is None:
        return "CSV forecast · Timestamp quality depends on the uploaded file."
    now = pd.Timestamp.now(tz=MARKET_TIMEZONE)
    latest = ensure_market_timestamp(latest_bar)
    if not is_market_open(now):
        return f"Market closed · Last available bar {format_ist_timestamp(latest)}"
    age_minutes = max(int((now - latest).total_seconds() // 60), 0)
    if age_minutes > 20:
        return f"Yahoo Finance · Latest bar is {age_minutes} minutes old · Market data may be delayed"
    return "Yahoo Finance · Recent five-minute market data · Market data may be delayed"


def build_dashboard_payload(
    summary: dict[str, object],
    history: pd.DataFrame | None = None,
    request_id: int | str | None = None,
) -> dict[str, object]:
    """Add UI metadata and chart points without changing forecast calculations."""
    uploaded_data_path = output_path("uploaded_market_data.csv")
    forecast_path = output_path("forecast.csv")
    actual_path = output_path("validation_actual.csv")
    summary_path = output_path("forecast_summary.json")
    if history is None and uploaded_data_path.exists():
        history = pd.read_csv(uploaded_data_path)
    if history is None:
        history = pd.DataFrame(columns=REQUIRED_COLUMNS)

    forecast = pd.read_csv(forecast_path) if forecast_path.exists() else pd.DataFrame()
    history = history.copy()
    if "timestamps" in history:
        history["timestamps"] = coerce_market_timestamps(history["timestamps"])
    valid_history = history.dropna(subset=["timestamps", "close"]).tail(64)

    if not forecast.empty:
        forecast = forecast.copy()
        forecast["timestamps"] = coerce_market_timestamps(forecast.get("timestamps"))

    input_source = str(summary.get("input_source", "Unknown source"))
    display_symbol, normalized_symbol, exchange, source_type = source_identity(input_source)
    observed_points = [market_point(row) for _, row in valid_history.iterrows()]
    forecast_points = [market_point(row) for _, row in forecast.dropna(subset=["timestamps", "close"]).iterrows()]
    actual_points: list[dict[str, object]] = []
    if summary.get("mode") == "validation" and actual_path.exists():
        actual_data = pd.read_csv(actual_path)
        if "timestamps" in actual_data:
            actual_data["timestamps"] = coerce_market_timestamps(actual_data["timestamps"])
            actual_points = [
                market_point(row)
                for _, row in actual_data.dropna(subset=["timestamps", "close"]).iterrows()
            ]
    all_history_timestamps = history.dropna(subset=["timestamps"])["timestamps"] if "timestamps" in history else pd.Series(dtype="datetime64[ns]")
    latest_market_bar = all_history_timestamps.iloc[-1] if not all_history_timestamps.empty else None
    forecast_created_at = pd.Timestamp.fromtimestamp(summary_path.stat().st_mtime, tz=MARKET_TIMEZONE).isoformat()
    yahoo_retrieved_at = str(summary.get("yahoo_retrieved_at", "")) or None
    data_source_name = (
        "Yahoo Finance · Recent five-minute market data"
        if source_type == "live"
        else "Uploaded CSV market data"
    )
    payload = dict(summary)
    payload.update(
        {
            "display_symbol": display_symbol,
            "normalized_symbol": normalized_symbol,
            "exchange": exchange,
            "source_type": source_type,
            "currency": "INR" if exchange in {"NSE", "BSE"} else None,
            "interval_minutes": INTERVAL_MINUTES,
            "interval_label": "Each point = 5 minutes",
            "forecast_created_at": forecast_created_at,
            "data_source_name": data_source_name,
            "market_freshness": freshness_message(latest_market_bar, source_type),
            "exchange_holiday_note": "Projected trading timestamps may not account for every exchange holiday.",
            "summary_fingerprint": summary_fingerprint(summary),
            "chart": {
                "observed": observed_points,
                "forecast": forecast_points,
                "actual": actual_points,
            },
            "validation": {
                **{key: summary.get(key) for key in (
                    "actual_rows", "compared_rows", "actual_final_close", "actual_pct_change",
                    "mae", "rmse", "mape", "final_error_pct", "directional_match",
                    "actual_direction", "predicted_direction", "directional_agreement_pct"
                )},
                "actual": actual_points,
            } if summary.get("mode") == "validation" else None,
            "timing": {
                "observed_start": observed_points[0]["timestamp"] if observed_points else "",
                "observed_end": observed_points[-1]["timestamp"] if observed_points else "",
                "last_yahoo_market_bar": iso_timestamp(latest_market_bar) if latest_market_bar is not None else "",
                "first_forecast_timestamp": forecast_points[0]["timestamp"] if forecast_points else "",
                "final_forecast_timestamp": forecast_points[-1]["timestamp"] if forecast_points else "",
                "forecast_created_at": forecast_created_at,
                "yahoo_retrieved_at": yahoo_retrieved_at,
                "interval_minutes": INTERVAL_MINUTES,
                "timezone": MARKET_TIMEZONE,
                "timezone_label": MARKET_TIMEZONE_LABEL,
                "data_source_name": data_source_name,
                "total_model_input_count": int(summary.get("input_rows", 0) or 0),
                "displayed_historical_count": len(observed_points),
                "forecast_count": len(forecast_points),
            },
        }
    )
    if request_id is not None:
        payload["request_id"] = request_id
    return payload


def run_forecast(
    csv_text: str,
    input_label: str = "uploaded_market_data.csv",
    request_id: int | str | None = None,
    forecast_bars: int | str | None = None,
    inference_settings: dict[str, object] | None = None,
) -> dict[str, object]:
    """Save a selected CSV locally and run the existing Kronos forecast script."""
    if not csv_text.strip():
        raise ValueError("Select a CSV file before starting a forecast.")
    if len(csv_text.encode("utf-8")) > 5_000_000:
        raise ValueError("Use a CSV smaller than 5 MB for this local demo.")

    market_data = validate_market_csv(csv_text)
    forecast_bars = resolve_forecast_bars(forecast_bars)
    settings = inference_settings or {}
    cache_key = forecast_cache_key(
        market_data,
        mode="forecast",
        input_label=input_label,
        forecast_bars=forecast_bars,
        temperature=float(settings.get("temperature", DEFAULT_TEMPERATURE) or DEFAULT_TEMPERATURE),
        top_k=int(settings.get("top_k", DEFAULT_TOP_K) or DEFAULT_TOP_K),
        top_p=float(settings.get("top_p", DEFAULT_TOP_P) or DEFAULT_TOP_P),
        sample_count=int(settings.get("sample_count", DEFAULT_SAMPLE_COUNT) or DEFAULT_SAMPLE_COUNT),
    )
    with FORECAST_LOCK:
        cached_summary = restore_forecast_cache(cache_key)
        if cached_summary:
            return build_dashboard_payload(cached_summary, market_data, request_id)
        uploaded_data_path = output_path("uploaded_market_data.csv")
        uploaded_data_path.parent.mkdir(parents=True, exist_ok=True)
        uploaded_data_path.write_text(csv_text, encoding="utf-8")
        yahoo_retrieved_at = None
        if input_label.lower().endswith("live 5-minute data"):
            yahoo_retrieved_at = pd.Timestamp.now(tz=MARKET_TIMEZONE).isoformat()
        summary = run_kronos_forecast(
            input_path=uploaded_data_path,
            input_label=input_label,
            forecast_bars=forecast_bars,
            temperature=settings.get("temperature"),
            top_k=settings.get("top_k"),
            top_p=settings.get("top_p"),
            sample_count=settings.get("sample_count"),
            yahoo_retrieved_at=yahoo_retrieved_at,
            output_dir=current_output_dir(),
        )
        summary["cache_hit"] = False
        output_path("forecast_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        save_forecast_cache(cache_key)
        return build_dashboard_payload(summary, market_data, request_id)


def fetch_live_forecast(
    ticker: str,
    exchange: str = "NSE",
    request_id: int | str | None = None,
    forecast_bars: int | str | None = None,
) -> dict[str, object]:
    """Fetch Indian-market OHLCV data and pass it through the local forecast flow."""
    source_ticker, clean_data = fetch_live_market_data(ticker, exchange)
    return run_forecast(
        clean_data.to_csv(index=False),
        f"{source_ticker} live 5-minute data",
        request_id,
        forecast_bars,
    )


def fetch_live_market_data(ticker: str, exchange: str = "NSE") -> tuple[str, pd.DataFrame]:
    normalized_ticker = ticker.strip().upper()
    normalized_exchange = exchange.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.^=\-]{1,20}", normalized_ticker):
        raise ValueError("Use a standard ticker such as AAPL, MSFT, RELIANCE.NS, or ^NSEI.")
    if normalized_exchange not in {"NSE", "BSE"}:
        raise ValueError("Choose NSE or BSE.")

    if normalized_ticker.startswith("^") or normalized_ticker.endswith((".NS", ".BO")):
        source_ticker = normalized_ticker
    else:
        suffix = ".NS" if normalized_exchange == "NSE" else ".BO"
        source_ticker = f"{normalized_ticker}{suffix}"

    try:
        market_data = yf.Ticker(source_ticker).history(period="1mo", interval="5m", auto_adjust=False)
    except Exception as error:
        raise ValueError(f"No five-minute data is currently available for {source_ticker}.") from error
    if market_data.empty:
        raise ValueError(f"No recent five-minute data was found for {source_ticker}.")

    market_data = market_data.reset_index()
    timestamp_column = "Datetime" if "Datetime" in market_data.columns else "Date"
    market_data = market_data.rename(
        columns={
            timestamp_column: "timestamps",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    market_data["timestamps"] = pd.to_datetime(market_data["timestamps"], utc=True).dt.tz_convert(MARKET_TIMEZONE)
    market_data["amount"] = market_data["close"] * market_data["volume"]
    required_columns = ["timestamps", "open", "high", "low", "close", "volume", "amount"]
    clean_data = market_data[required_columns].dropna()
    return source_ticker, clean_data


def fetch_live_validation(
    ticker: str,
    exchange: str = "NSE",
    request_id: int | str | None = None,
    forecast_bars: int | str | None = None,
) -> dict[str, object]:
    source_ticker, clean_data = fetch_live_market_data(ticker, exchange)
    return run_validation(
        clean_data.to_csv(index=False),
        f"{source_ticker} live 5-minute data",
        request_id,
        forecast_bars,
    )


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

    def translate_path(self, path: str) -> str:
        resolved = Path(super().translate_path(path)).resolve()
        app_root = (PROJECT_ROOT / "app").resolve()
        try:
            resolved.relative_to(app_root)
        except ValueError:
            return str(app_root / "__not_found__")
        return str(resolved)

    def send_json(
        self,
        payload: dict[str, object],
        status: HTTPStatus = HTTPStatus.OK,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self) -> None:
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/api/auth/session":
            if not self.is_loopback_client():
                self.send_json({"error": "Local demo profiles are available only from this computer."}, HTTPStatus.FORBIDDEN)
                return
            try:
                profile = self.current_profile()
                first_run = not AUTH_STORE.has_profiles()
            except (OSError, ValueError) as error:
                self.send_json({"error": f"Local profile storage could not be read: {error}"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            self.send_json({
                "mode": "local-demo",
                "authenticated": profile is not None,
                "first_run": first_run,
                "profile": profile,
            })
            return

        if parsed_path.path.startswith("/api/"):
            if not self.is_loopback_client():
                self.send_json({"error": "Local demo profiles are available only from this computer."}, HTTPStatus.FORBIDDEN)
                return
            try:
                profile = self.current_profile()
            except (OSError, ValueError) as error:
                self.send_json({"error": f"Local profile storage could not be read: {error}"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if profile is None:
                self.send_json({"error": "Sign in to local demo mode to continue."}, HTTPStatus.UNAUTHORIZED)
                return
            context_token = CURRENT_USER_ID.set(str(profile["id"]))
            try:
                if parsed_path.path == "/api/auth/profile":
                    self.send_json({"profile": profile})
                    return
                self.handle_authenticated_get(parsed_path)
            finally:
                CURRENT_USER_ID.reset(context_token)
            return

        if parsed_path.path.startswith("/app/"):
            super().do_GET()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def is_loopback_client(self) -> bool:
        return self.client_address[0] in {"127.0.0.1", "::1"}

    def local_origin_is_valid(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "").lower()
        return host in {"127.0.0.1:8000", "localhost:8000"} and origin == f"http://{host}"

    def current_profile(self) -> dict[str, object] | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return AUTH_STORE.restore(morsel.value if morsel else None)

    def read_json_body(self) -> dict[str, object]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Request length is invalid.") from error
        if content_length < 0 or content_length > 32_000:
            raise ValueError("Profile request is too large.")
        try:
            data = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Request must contain valid JSON.") from error
        if not isinstance(data, dict):
            raise ValueError("Request must be a JSON object.")
        return data

    def handle_authenticated_get(self, parsed_path) -> None:
        if parsed_path.path == "/api/dashboard":
            try:
                self.send_json(build_dashboard_payload(load_summary()))
            except (FileNotFoundError, json.JSONDecodeError, ValueError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed_path.path == "/api/symbol-search":
            params = parse_qs(parsed_path.query)
            self.send_json(
                {
                    "results": symbol_search(
                        params.get("q", [""])[0],
                        params.get("exchange", ["NSE"])[0],
                    )
                }
            )
            return
        if parsed_path.path == "/api/explanation":
            try:
                summary = load_summary()
                cached = load_cached_explanation(summary)
                self.send_json(
                    {
                        "available": bool(cached),
                        "explanation": (
                            format_explanation_currency(cached.get("explanation", ""), summary)
                            if cached
                            else None
                        ),
                        "summary_fingerprint": summary_fingerprint(summary),
                    }
                )
            except (FileNotFoundError, json.JSONDecodeError) as error:
                self.send_json({"available": False, "error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed_path = urlparse(self.path)
        if not self.is_loopback_client():
            self.send_json({"error": "Local demo profiles are available only from this computer."}, HTTPStatus.FORBIDDEN)
            return
        if not self.local_origin_is_valid():
            self.send_json({"error": "This local profile request came from an unexpected site."}, HTTPStatus.FORBIDDEN)
            return

        if parsed_path.path == "/api/auth/sign-in":
            try:
                request_data = self.read_json_body()
                token, profile, first_run = AUTH_STORE.sign_in(
                    request_data.get("email"), request_data.get("name", "")
                )
            except (ValueError, OSError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            cookie = (
                f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_MAX_AGE_SECONDS}; "
                "HttpOnly; SameSite=Strict"
            )
            self.send_json(
                {"authenticated": True, "first_run": first_run, "mode": "local-demo", "profile": profile},
                extra_headers={"Set-Cookie": cookie},
            )
            return

        if parsed_path.path == "/api/auth/sign-out":
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                morsel = cookie.get(SESSION_COOKIE)
                AUTH_STORE.sign_out(morsel.value if morsel else None)
            except Exception:
                pass
            expired_cookie = f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
            self.send_json({"signed_out": True}, extra_headers={"Set-Cookie": expired_cookie})
            return

        try:
            profile = self.current_profile()
        except (OSError, ValueError) as error:
            self.send_json({"error": f"Local profile storage could not be read: {error}"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if profile is None:
            self.send_json({"error": "Sign in to local demo mode to continue."}, HTTPStatus.UNAUTHORIZED)
            return

        context_token = CURRENT_USER_ID.set(str(profile["id"]))
        try:
            if parsed_path.path == "/api/auth/profile":
                try:
                    request_data = self.read_json_body()
                    updated_profile = AUTH_STORE.update_profile(
                        str(profile["id"]),
                        request_data.get("name"),
                        request_data.get("preferences"),
                    )
                except (ValueError, OSError, json.JSONDecodeError) as error:
                    self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                    return
                self.send_json({"profile": updated_profile})
                return
            self.handle_authenticated_post(parsed_path)
        finally:
            CURRENT_USER_ID.reset(context_token)

    def handle_authenticated_post(self, parsed_path) -> None:
        if self.path == "/api/forecast":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                request_data = json.loads(self.rfile.read(content_length).decode("utf-8"))
                filename = Path(str(request_data.get("filename", "uploaded_market_data.csv"))).name
                self.send_json(
                    run_forecast(
                        request_data.get("csv", ""),
                        filename,
                        request_data.get("request_id"),
                        request_data.get("forecast_bars") or request_data.get("horizon"),
                    )
                )
            except (ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/validation":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                request_data = json.loads(self.rfile.read(content_length).decode("utf-8"))
                filename = Path(str(request_data.get("filename", "uploaded_market_data.csv"))).name
                self.send_json(
                    run_validation(
                        request_data.get("csv", ""),
                        filename,
                        request_data.get("request_id"),
                        request_data.get("forecast_bars") or request_data.get("horizon"),
                    )
                )
            except (ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/live-forecast":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                request_data = json.loads(self.rfile.read(content_length).decode("utf-8"))
                self.send_json(
                    fetch_live_forecast(
                        request_data.get("ticker", ""),
                        request_data.get("exchange", "NSE"),
                        request_data.get("request_id"),
                        request_data.get("forecast_bars") or request_data.get("horizon"),
                    )
                )
            except (ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/live-validation":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                request_data = json.loads(self.rfile.read(content_length).decode("utf-8"))
                self.send_json(
                    fetch_live_validation(
                        request_data.get("ticker", ""),
                        request_data.get("exchange", "NSE"),
                        request_data.get("request_id"),
                        request_data.get("forecast_bars") or request_data.get("horizon"),
                    )
                )
            except (ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/explanation":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                request_data = (
                    json.loads(self.rfile.read(content_length).decode("utf-8"))
                    if content_length
                    else {}
                )
                self.send_json(generate_explanation(request_data.get("summary_fingerprint")))
            except Exception:
                self.send_json(
                    {"error": "The explanation could not be generated. Check your key and API billing, then try again."},
                    HTTPStatus.BAD_GATEWAY,
                )
            return
        self.send_error(HTTPStatus.NOT_FOUND)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8000), DashboardHandler)
    print("Kronos Copilot dashboard: http://127.0.0.1:8000/app/dashboard.html")
    print("Local demo profiles are available only on this computer; no production identity provider is configured.")
    server.serve_forever()
