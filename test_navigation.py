import unittest

from navigation import safe_post_login_path


class SafePostLoginPathTests(unittest.TestCase):
    def test_default_and_home_open_terminal(self):
        self.assertEqual(safe_post_login_path(None), "/terminal")
        self.assertEqual(safe_post_login_path(""), "/terminal")
        self.assertEqual(safe_post_login_path("/"), "/terminal")

    def test_requested_local_page_is_preserved(self):
        self.assertEqual(
            safe_post_login_path("/smart-money?tab=insiders"),
            "/smart-money?tab=insiders",
        )

    def test_external_or_malformed_destinations_are_rejected(self):
        for value in (
            "https://example.com",
            "//example.com/path",
            "javascript:alert(1)",
            "/\\example.com",
            "/terminal\r\nLocation: https://example.com",
        ):
            with self.subTest(value=value):
                self.assertEqual(safe_post_login_path(value), "/terminal")


if __name__ == "__main__":
    unittest.main()
