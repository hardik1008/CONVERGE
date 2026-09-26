"""Passwordless, loopback-only profile sessions for the local development app."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SESSION_COOKIE = "kronos_local_session"
SESSION_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
DEFAULT_PREFERENCES: dict[str, Any] = {
    "preferred_exchange": "NSE",
    "chart_mode": "candles",
    "currency_display": "symbol",
    "show_volume": True,
    "default_horizon": 75,
    "default_research_mode": "forecast",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_email(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Enter a valid email address.")
    email = value.strip().casefold()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Enter a valid email address, such as name@example.com.")
    return email


def _normalize_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Enter a name between 1 and 80 characters.")
    name = " ".join(value.split())
    if not name or len(name) > 80 or any(ord(character) < 32 for character in name):
        raise ValueError("Enter a name between 1 and 80 characters.")
    return name


def _profile_copy(profile: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(profile))


class LocalAuthStore:
    """Persist local profiles and hashed session tokens in one ignored JSON file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "profiles": {}, "sessions": {}}
        state = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != 1
            or not isinstance(state.get("profiles"), dict)
            or not isinstance(state.get("sessions"), dict)
        ):
            raise ValueError("Local profile storage is invalid. Preserve the file and inspect it before continuing.")
        return state

    def _write(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.{secrets.token_hex(8)}.tmp")
        try:
            temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp_path, self.path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @staticmethod
    def _user_id(email: str) -> str:
        return hashlib.sha256(email.encode("utf-8")).hexdigest()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _prune_expired(state: dict[str, Any], now: datetime) -> None:
        expired = []
        for token_hash, session in state["sessions"].items():
            try:
                expires_at = datetime.fromisoformat(session["expires_at"])
            except (KeyError, TypeError, ValueError):
                expired.append(token_hash)
                continue
            if expires_at.tzinfo is None:
                expired.append(token_hash)
                continue
            if expires_at <= now or session.get("user_id") not in state["profiles"]:
                expired.append(token_hash)
        for token_hash in expired:
            state["sessions"].pop(token_hash, None)

    @staticmethod
    def _new_session(state: dict[str, Any], user_id: str, now: datetime) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = LocalAuthStore._token_hash(token)
        state["sessions"][token_hash] = {
            "user_id": user_id,
            "expires_at": (now + timedelta(seconds=SESSION_MAX_AGE_SECONDS)).isoformat(),
        }
        return token

    def sign_in(self, email_value: object, name_value: object = "") -> tuple[str, dict[str, Any], bool]:
        email = _normalize_email(email_value)
        now = _now()
        user_id = self._user_id(email)
        with self._lock:
            state = self._read()
            self._prune_expired(state, now)
            first_run = not state["profiles"]
            profile = state["profiles"].get(user_id)
            is_new_profile = profile is None
            if is_new_profile:
                name = _normalize_name(name_value)
                profile = {
                    "id": user_id,
                    "name": name,
                    "email": email,
                    "created_at": now.isoformat(),
                    "preferences": dict(DEFAULT_PREFERENCES),
                }
                state["profiles"][user_id] = profile
            token = self._new_session(state, user_id, now)
            self._write(state)
            return token, _profile_copy(profile), first_run or is_new_profile

    def restore(self, token: str | None) -> dict[str, Any] | None:
        if not isinstance(token, str) or not token or len(token) > 256:
            return None
        now = _now()
        with self._lock:
            state = self._read()
            self._prune_expired(state, now)
            session = state["sessions"].get(self._token_hash(token))
            if not session:
                return None
            profile = state["profiles"].get(session["user_id"])
            if profile is None:
                return None
            return _profile_copy(profile)

    def sign_out(self, token: str | None) -> None:
        if not isinstance(token, str) or not token:
            return
        with self._lock:
            state = self._read()
            state["sessions"].pop(self._token_hash(token), None)
            self._write(state)

    def update_profile(
        self,
        user_id: str,
        name_value: object,
        preferences_value: object,
    ) -> dict[str, Any]:
        name = _normalize_name(name_value)
        if not isinstance(preferences_value, dict):
            raise ValueError("Review the profile preferences and try again.")
        unknown = set(preferences_value) - set(DEFAULT_PREFERENCES)
        if unknown:
            raise ValueError("One or more profile preferences are not supported.")

        with self._lock:
            state = self._read()
            profile = state["profiles"].get(user_id)
            if profile is None:
                raise ValueError("This local profile is no longer available. Sign in again.")
            preferences = dict(profile["preferences"])
            preferences.update(preferences_value)
            if not isinstance(preferences["preferred_exchange"], str) or preferences["preferred_exchange"] not in {"NSE", "BSE"}:
                raise ValueError("Preferred exchange must be NSE or BSE.")
            if not isinstance(preferences["chart_mode"], str) or preferences["chart_mode"] not in {"candles", "line"}:
                raise ValueError("Choose candles or price line for the chart.")
            if not isinstance(preferences["currency_display"], str) or preferences["currency_display"] not in {"symbol", "code"}:
                raise ValueError("Choose the rupee symbol or INR code for prices.")
            if type(preferences["show_volume"]) is not bool:
                raise ValueError("Choose whether to show volume in the chart.")
            if type(preferences["default_horizon"]) is not int or preferences["default_horizon"] not in {24, 75, 120}:
                raise ValueError("Choose a supported horizon: 24, 75, or 120 market bars.")
            if not isinstance(preferences["default_research_mode"], str) or preferences["default_research_mode"] not in {"forecast", "historical"}:
                raise ValueError("Choose Forecast or Historical Performance as the default view.")
            profile["name"] = name
            profile["preferences"] = preferences
            self._write(state)
            return _profile_copy(profile)

    def has_profiles(self) -> bool:
        with self._lock:
            return bool(self._read()["profiles"])
