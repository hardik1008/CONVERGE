from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.local_auth import DEFAULT_PREFERENCES, LocalAuthStore


class LocalAuthStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.temp_dir.name) / "local_profiles.json"
        self.store = LocalAuthStore(self.store_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sign_in_creates_a_profile_and_opaque_session(self) -> None:
        token, profile, first_run = self.store.sign_in("Ria@example.com", "Ria Rao")

        self.assertTrue(first_run)
        self.assertEqual(profile["email"], "ria@example.com")
        self.assertEqual(profile["name"], "Ria Rao")
        self.assertEqual(profile["preferences"], DEFAULT_PREFERENCES)
        self.assertEqual(self.store.restore(token)["id"], profile["id"])
        persisted = json.loads(self.store_path.read_text(encoding="utf-8"))
        self.assertNotIn(token, self.store_path.read_text(encoding="utf-8"))
        self.assertIn("profiles", persisted)
        self.assertEqual(len(persisted["sessions"]), 1)

    def test_sign_out_invalidates_the_session(self) -> None:
        token, _, _ = self.store.sign_in("ria@example.com", "Ria Rao")

        self.store.sign_out(token)

        self.assertIsNone(self.store.restore(token))

    def test_session_restores_after_store_recreation(self) -> None:
        token, profile, _ = self.store.sign_in("ria@example.com", "Ria Rao")
        restored = LocalAuthStore(self.store_path).restore(token)

        self.assertEqual(restored, profile)

    def test_profile_preferences_persist_for_that_identity_only(self) -> None:
        token_a, profile_a, _ = self.store.sign_in("ria@example.com", "Ria Rao")
        token_b, profile_b, _ = self.store.sign_in("dev@example.com", "Dev Shah")
        updated = self.store.update_profile(
            profile_a["id"],
            "Ria Research",
            {"preferred_exchange": "BSE", "default_horizon": 24, "chart_mode": "line"},
        )

        restored_store = LocalAuthStore(self.store_path)
        restored_a = restored_store.restore(token_a)
        restored_b = restored_store.restore(token_b)
        self.assertEqual(updated["name"], "Ria Research")
        self.assertEqual(restored_a["preferences"]["preferred_exchange"], "BSE")
        self.assertEqual(restored_a["preferences"]["default_horizon"], 24)
        self.assertEqual(restored_b["id"], profile_b["id"])
        self.assertEqual(restored_b["name"], "Dev Shah")
        self.assertEqual(restored_b["preferences"], DEFAULT_PREFERENCES)

    def test_invalid_email_name_and_preferences_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid email"):
            self.store.sign_in("not-an-email", "Ria Rao")
        with self.assertRaisesRegex(ValueError, "name"):
            self.store.sign_in("ria@example.com", "")

        _, profile, _ = self.store.sign_in("ria@example.com", "Ria Rao")
        for invalid_preferences in (
            {"preferred_exchange": "NYSE"},
            {"chart_mode": []},
            {"currency_display": ""},
            {"show_volume": "yes"},
            {"default_horizon": 75.0},
            {"default_research_mode": "buy"},
            {"unknown": True},
        ):
            with self.subTest(preferences=invalid_preferences), self.assertRaises(ValueError):
                self.store.update_profile(profile["id"], "Ria Rao", invalid_preferences)


if __name__ == "__main__":
    unittest.main()
