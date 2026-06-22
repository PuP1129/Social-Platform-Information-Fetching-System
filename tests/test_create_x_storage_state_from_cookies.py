from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts.create_x_storage_state_from_cookies import build_storage_state, main


class XCookieStorageStateTests(unittest.TestCase):
    def test_builds_playwright_storage_state_from_dummy_cookie_values(self) -> None:
        state = build_storage_state(
            {
                "X_AUTH_TOKEN": "dummy-auth-token",
                "X_CT0": "dummy-csrf-token",
                "X_TWID": "dummy-user-id",
                "X_GUEST_ID": "dummy-guest-id",
                "X_PERSONALIZATION_ID": "dummy-personalization-id",
            }
        )

        self.assertEqual(state["origins"], [])
        cookies = state["cookies"]
        self.assertEqual(
            {cookie["name"] for cookie in cookies},
            {"auth_token", "ct0", "twid", "guest_id", "personalization_id"},
        )
        for cookie in cookies:
            self.assertEqual(cookie["domain"], ".x.com")
            self.assertEqual(cookie["path"], "/")
            self.assertTrue(cookie["secure"])
            self.assertEqual(cookie["sameSite"], "Lax")

        auth_cookie = next(cookie for cookie in cookies if cookie["name"] == "auth_token")
        self.assertTrue(auth_cookie["httpOnly"])

    def test_reports_required_environment_variable_names(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            r"^Missing required environment variable\(s\): X_AUTH_TOKEN, X_CT0$",
        ):
            build_storage_state({})

    def test_main_accepts_dummy_environment_values_without_printing_them(self) -> None:
        output_path = MagicMock()
        dummy_environment = {
            "X_AUTH_TOKEN": "dummy_auth",
            "X_CT0": "dummy_ct0",
        }

        with patch.dict("os.environ", dummy_environment, clear=True):
            with patch(
                "scripts.create_x_storage_state_from_cookies.parse_args",
                return_value=SimpleNamespace(output=output_path),
            ):
                with patch("builtins.print") as print_mock:
                    self.assertEqual(main(), 0)

        output_path.parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        output_path.write_text.assert_called_once()
        serialized_state = output_path.write_text.call_args.args[0]
        self.assertIn("dummy_auth", serialized_state)
        self.assertIn("dummy_ct0", serialized_state)
        printed_text = " ".join(str(call) for call in print_mock.call_args_list)
        self.assertNotIn("dummy_auth", printed_text)
        self.assertNotIn("dummy_ct0", printed_text)


if __name__ == "__main__":
    unittest.main()
