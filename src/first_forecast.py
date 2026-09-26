"""Create a first local market forecast with the open-source Kronos-base model."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import torch

from forecast_config import (
    DEFAULT_SAMPLE_COUNT,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_K,
    DEFAULT_TOP_P,
    FORECAST_BARS,
    LOOKBACK_BARS,
    build_forecast_timestamps,
    coerce_market_timestamps,
    horizon_detail,
    horizon_label,
    resolve_forecast_bars,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
KRONOS_ROOT = PROJECT_ROOT / "vendor" / "Kronos-master"
DATA_FILE = KRONOS_ROOT / "tests" / "data" / "regression_input.csv"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

FEATURES = ["open", "high", "low", "close", "volume", "amount"]
MODEL_NAME = "NeoQuasar/Kronos-base"
TOKENIZER_NAME = "NeoQuasar/Kronos-Tokenizer-base"
_MODEL_CACHE: dict[str, object] = {}


def _float_summary(value: object, digits: int = 4) -> float:
    return round(float(value), digits)


def build_summary(
    history: pd.DataFrame,
    forecast: pd.DataFrame,
    input_source: str,
    *,
    forecast_bars: int,
    temperature: float,
    top_k: int,
    top_p: float,
    sample_count: int,
    inference_seconds: float,
) -> dict[str, object]:
    """Return the small JSON object the dashboard and AI layer will use."""
    last_close = float(history["close"].iloc[-1])
    final_close = float(forecast["close"].iloc[-1])
    percent_change = (final_close - last_close) / last_close * 100
    close_range = float(forecast["close"].max()) - float(forecast["close"].min())

    return {
        "model": MODEL_NAME,
        "input_source": input_source,
        "input_rows": len(history),
        "forecast_rows": len(forecast),
        "lookback_bars": len(history),
        "prediction_length": forecast_bars,
        "forecast_horizon_label": horizon_label(forecast_bars),
        "forecast_horizon_detail": horizon_detail(forecast_bars),
        "yahoo_retrieved_at": os.environ.get("KRONOS_YAHOO_RETRIEVED_AT"),
        "last_observed_close": _float_summary(last_close),
        "forecast_final_close": _float_summary(final_close),
        "forecast_min_close": _float_summary(forecast["close"].min()),
        "forecast_max_close": _float_summary(forecast["close"].max()),
        "forecast_pct_change": round(percent_change, 3),
        "forecast_close_std": _float_summary(forecast["close"].std(ddof=0)),
        "forecast_range_pct": round(close_range / last_close * 100, 4) if last_close else None,
        "direction": "up" if percent_change >= 0 else "down",
        "ohlcv_forecast_available": all(column in forecast.columns for column in FEATURES),
        "forecast_columns": [column for column in FEATURES if column in forecast.columns],
        "sampling_T": temperature,
        "sampling_top_k": top_k,
        "sampling_top_p": top_p,
        "sample_count": sample_count,
        "sample_outputs_available": False,
        "sample_aggregation": "This KronosPredictor version averages multiple samples internally.",
        "inference_time_seconds": round(inference_seconds, 3),
        "note": "Compact summary designed for low-token AI explanation prompts.",
    }


def load_kronos_model():
    sys.path.insert(0, str(KRONOS_ROOT))
    from model import Kronos, KronosPredictor, KronosTokenizer

    if "predictor" not in _MODEL_CACHE:
        tokenizer = KronosTokenizer.from_pretrained(TOKENIZER_NAME)
        model = Kronos.from_pretrained(MODEL_NAME)
        tokenizer.eval()
        model.eval()
        _MODEL_CACHE["predictor"] = KronosPredictor(model, tokenizer, device="cpu", max_context=512)
    return _MODEL_CACHE["predictor"]


def prepare_market_data(input_path: str | Path) -> pd.DataFrame:
    market_data = pd.read_csv(input_path, parse_dates=["timestamps"])
    required_columns = {"timestamps", *FEATURES}
    missing_columns = required_columns.difference(market_data.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Input data is missing required columns: {missing}")
    market_data["timestamps"] = coerce_market_timestamps(market_data["timestamps"])
    return market_data.dropna(subset=["timestamps", *FEATURES]).reset_index(drop=True)


def predict_with_kronos(
    history: pd.DataFrame,
    future_timestamps: pd.Series,
    forecast_bars: int,
    *,
    temperature: float,
    top_k: int,
    top_p: float,
    sample_count: int,
) -> tuple[pd.DataFrame, float]:
    predictor = load_kronos_model()
    started_at = time.perf_counter()
    with torch.no_grad():
        forecast = predictor.predict(
            df=history[FEATURES],
            x_timestamp=history["timestamps"],
            y_timestamp=future_timestamps,
            pred_len=forecast_bars,
            T=temperature,
            top_k=top_k,
            top_p=top_p,
            sample_count=sample_count,
            verbose=False,
        )
    inference_seconds = time.perf_counter() - started_at
    forecast = forecast.reset_index(drop=True)
    forecast.insert(0, "timestamps", future_timestamps.reset_index(drop=True))
    return forecast, inference_seconds


def run_kronos_forecast(
    input_path: str | Path | None = None,
    input_label: str | None = None,
    forecast_bars: int | str | None = None,
    temperature: float | None = None,
    top_k: int | None = None,
    top_p: float | None = None,
    sample_count: int | None = None,
    yahoo_retrieved_at: str | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, object]:
    output_directory = Path(output_dir) if output_dir is not None else OUTPUTS_DIR
    output_directory.mkdir(parents=True, exist_ok=True)
    input_path = Path(input_path or os.environ.get("KRONOS_INPUT_PATH", DATA_FILE))
    forecast_bars = resolve_forecast_bars(
        forecast_bars or os.environ.get("KRONOS_FORECAST_BARS") or os.environ.get("KRONOS_FORECAST_HORIZON")
    )
    temperature = float(temperature if temperature is not None else os.environ.get("KRONOS_T", DEFAULT_TEMPERATURE))
    top_k = int(top_k if top_k is not None else os.environ.get("KRONOS_TOP_K", DEFAULT_TOP_K))
    top_p = float(top_p if top_p is not None else os.environ.get("KRONOS_TOP_P", DEFAULT_TOP_P))
    sample_count = int(sample_count if sample_count is not None else os.environ.get("KRONOS_SAMPLE_COUNT", DEFAULT_SAMPLE_COUNT))
    if sample_count < 1:
        raise ValueError("sample_count must be at least 1.")

    market_data = prepare_market_data(input_path)
    if len(market_data) < LOOKBACK_BARS:
        raise ValueError(f"Input data needs at least {LOOKBACK_BARS} rows; received {len(market_data)}.")
    history = market_data.tail(LOOKBACK_BARS).reset_index(drop=True)

    future_timestamps = build_forecast_timestamps(history["timestamps"].iloc[-1], forecast_bars)
    forecast, inference_seconds = predict_with_kronos(
        history,
        future_timestamps,
        forecast_bars,
        temperature=temperature,
        top_k=top_k,
        top_p=top_p,
        sample_count=sample_count,
    )
    forecast.to_csv(output_directory / "forecast.csv", index=False)

    input_source = input_label or os.environ.get("KRONOS_INPUT_LABEL", input_path.name)
    if yahoo_retrieved_at:
        os.environ["KRONOS_YAHOO_RETRIEVED_AT"] = yahoo_retrieved_at
    summary = build_summary(
        history,
        forecast,
        input_source,
        forecast_bars=forecast_bars,
        temperature=temperature,
        top_k=top_k,
        top_p=top_p,
        sample_count=sample_count,
        inference_seconds=inference_seconds,
    )
    (output_directory / "forecast_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    summary = run_kronos_forecast()

    print(json.dumps(summary, indent=2))
    print(f"Saved forecast to: {OUTPUTS_DIR / 'forecast.csv'}")


if __name__ == "__main__":
    main()
