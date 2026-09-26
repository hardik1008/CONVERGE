# Local profile and session boundary

Kronos Copilot currently has no production identity provider or database. The app therefore provides a **passwordless local/demo profile mode**, clearly labeled in the UI. It lets one computer maintain separate research preferences and saved forecasts per profile; it does not prove a person’s identity and must not be represented as production authentication.

## Scope and operation

- The server binds to `127.0.0.1:8000` only. LAN access is intentionally unavailable while this local mode is active.
- Sign in identifies a local profile by email. A name is required for a new profile and optional for an existing one. There is no password field and no password is stored.
- The server issues a random, 256-bit session token in an `HttpOnly`, `SameSite=Strict` cookie with a 14-day maximum age. Only a SHA-256 hash of the token and its expiry are persisted. Sign out revokes that session and expires the cookie.
- The app runs over local HTTP, so the cookie is not marked `Secure`; this mode is intended only for the loopback development server. Do not expose or reverse-proxy it.
- API routes that return a forecast, profile, or market search require a valid session. The only unauthenticated API routes are session bootstrap, sign in, and sign out. Static serving is restricted to files beneath `app/`; runtime data and credentials are not served as static files.
- Profile data lives in `outputs/local_profiles.json`. Forecasts, uploaded market data, validation actuals, explanation caches, and forecast caches live under `outputs/users/<profile-id>/`. The profile ID is a SHA-256 digest of the normalized email, not a credential. The existing ignored `outputs/` filesystem is the sole persistence mechanism; no database or browser storage was added.
- Email identifies the profile and is read-only in the profile editor. Display name, exchange, chart mode, price notation, volume visibility, default horizon, and default research view are editable and validated by the server.
- `.env.local` and `OPENAI_API_KEY` remain server-side and are never included in sign-in or profile requests.

The local JSON file protects against accidental cross-profile mixing inside this app. It does not protect profile data from other software or users with access to the same OS account, and it is not a substitute for an identity provider, TLS, or production session management. A future production auth integration must replace the local boundary before network exposure.

## Verification

Focused tests:

```powershell
py -m unittest tests.test_local_auth -v
py -m unittest tests.test_auth_routes -v
```

Run all repository-owned tests:

```powershell
py -m unittest discover -v
```

These tests use temporary profile stores and an ephemeral loopback HTTP server; they do not use forecast fixtures or modify the protected Kronos evaluation assets.
