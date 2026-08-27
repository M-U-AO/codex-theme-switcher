import json
import random
import sys
import tomllib
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import codex_theme_core as core  # noqa: E402


PAYLOAD = {
    "codeThemeId": "tokyo-night",
    "theme": {
        "accent": "#7aa2f7",
        "contrast": 52,
        "fonts": {"ui": "SF Pro Text", "code": "SF Mono"},
        "ink": "#c0caf5",
        "opaqueWindows": False,
        "semanticColors": {
            "diffAdded": "#9ece6a",
            "diffRemoved": "#f7768e",
            "skill": "#bb9af7",
        },
        "surface": "#24283b",
    },
    "variant": "dark",
}


class PayloadTests(unittest.TestCase):
    def test_parse_theme_payload(self):
        encoded = core.THEME_PREFIX + json.dumps(PAYLOAD)
        self.assertEqual(core.parse_theme_payload(encoded), PAYLOAD)

    def test_rejects_missing_prefix(self):
        with self.assertRaises(core.ThemeError):
            core.parse_theme_payload(json.dumps(PAYLOAD))


class ConfigUpdateTests(unittest.TestCase):
    def test_preserves_unrelated_config_and_replaces_inline_values(self):
        source = """model = "gpt-5.6-sol"

[desktop]
followUpQueueMode = "queue"
appearanceTheme = "light"
appearanceDarkCodeThemeId = "old"
appearanceDarkChromeTheme = { accent = "#000000" }

[mcp_servers.example]
command = "example"
"""
        updated = core.update_config_text(source, PAYLOAD)
        parsed = tomllib.loads(updated)

        self.assertEqual(parsed["model"], "gpt-5.6-sol")
        self.assertEqual(parsed["desktop"]["followUpQueueMode"], "queue")
        self.assertEqual(parsed["desktop"]["appearanceTheme"], "dark")
        self.assertEqual(parsed["desktop"]["appearanceDarkCodeThemeId"], "tokyo-night")
        self.assertEqual(
            parsed["desktop"]["appearanceDarkChromeTheme"]["surface"], "#24283b"
        )
        self.assertEqual(parsed["mcp_servers"]["example"]["command"], "example")

    def test_removes_nested_target_tables_only(self):
        source = """[desktop]
keep = true

[desktop.appearanceDarkChromeTheme]
accent = "#000000"

[desktop.appearanceDarkChromeTheme.fonts]
ui = "Old"
code = "Old Mono"

[desktop.unrelated]
value = 42
"""
        updated = core.update_config_text(source, PAYLOAD)
        parsed = tomllib.loads(updated)

        self.assertTrue(parsed["desktop"]["keep"])
        self.assertEqual(parsed["desktop"]["unrelated"]["value"], 42)
        theme = parsed["desktop"]["appearanceDarkChromeTheme"]
        self.assertEqual(theme["accent"], "#7aa2f7")
        self.assertEqual(theme["fonts"]["code"], "SF Mono")

    def test_creates_desktop_section(self):
        updated = core.update_config_text('model = "gpt-5.6-sol"\n', PAYLOAD)
        parsed = tomllib.loads(updated)
        self.assertEqual(parsed["desktop"]["appearanceTheme"], "dark")


class RandomThemeTests(unittest.TestCase):
    def test_avoids_immediate_repeat(self):
        themes = [
            {"slug": "a", "mode": "dark"},
            {"slug": "b", "mode": "dark"},
            {"slug": "c", "mode": "light"},
        ]
        selected = core.choose_random_theme(
            themes, mode="dark", last_slug="a", chooser=random.Random(1)
        )
        self.assertEqual(selected["slug"], "b")


if __name__ == "__main__":
    unittest.main()
