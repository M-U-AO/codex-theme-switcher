import contextlib
import copy
import io
import json
import random
import sys
import tomllib
import unittest
from pathlib import Path
from unittest import mock


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

    def test_rejects_unsafe_nested_key(self):
        payload = copy.deepcopy(PAYLOAD)
        payload["theme"][
            'escape = 1 }\n[mcp_servers.injected]\ncommand = "bad"\n#'
        ] = "ignored"

        with self.assertRaisesRegex(core.ThemeError, "字段名不安全"):
            core.parse_theme_payload(core.THEME_PREFIX + json.dumps(payload))

    def test_rejects_non_finite_numbers(self):
        encoded = core.THEME_PREFIX + json.dumps(PAYLOAD).replace("52", "NaN", 1)

        with self.assertRaisesRegex(core.ThemeError, "不是有效 JSON"):
            core.parse_theme_payload(encoded)

    def test_rejects_boolean_contrast(self):
        payload = copy.deepcopy(PAYLOAD)
        payload["theme"]["contrast"] = True

        with self.assertRaisesRegex(core.ThemeError, "contrast"):
            core.parse_theme_payload(core.THEME_PREFIX + json.dumps(payload))


class RemoteUrlTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, url, content=b"ok"):
            self.url = url
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def geturl(self):
            return self.url

        def read(self, _limit):
            return self.content

    def test_resolves_import_inside_repository(self):
        url = core.theme_import_url(
            {"import": "themes/imports/tokyo-night.txt"}, core.DEFAULT_INDEX_URL
        )

        self.assertEqual(
            url,
            "https://raw.githubusercontent.com/shaw-baobao/codex-themes/"
            "main/themes/imports/tokyo-night.txt",
        )

    def test_rejects_cross_origin_import(self):
        with self.assertRaisesRegex(core.ThemeError, "同源"):
            core.theme_import_url(
                {"import": "https://example.com/theme.txt"}, core.DEFAULT_INDEX_URL
            )

    def test_rejects_repository_path_escape(self):
        with self.assertRaisesRegex(core.ThemeError, "仓库范围"):
            core.theme_import_url(
                {"import": "../../other/repository/theme.txt"},
                core.DEFAULT_INDEX_URL,
            )

    def test_rejects_encoded_repository_path_escape(self):
        with self.assertRaisesRegex(core.ThemeError, "仓库范围"):
            core.theme_import_url(
                {"import": "%2e%2e/%2e%2e/other/repository/theme.txt"},
                core.DEFAULT_INDEX_URL,
            )

    def test_rejects_non_https_index(self):
        with self.assertRaisesRegex(core.ThemeError, "HTTPS"):
            core.theme_import_url(
                {"import": "themes/theme.txt"}, "http://example.com/themes/index.json"
            )

    def test_rejects_redirects(self):
        response = self.FakeResponse("https://example.com/redirected.json")
        with (
            mock.patch.object(core.urllib.request, "urlopen", return_value=response),
            self.assertRaisesRegex(core.ThemeError, "拒绝远程重定向"),
        ):
            core._read_url("https://example.com/index.json")

    def test_rejects_oversized_download(self):
        url = "https://example.com/index.json"
        response = self.FakeResponse(url, b"x" * (core.MAX_DOWNLOAD_BYTES + 1))
        with (
            mock.patch.object(core.urllib.request, "urlopen", return_value=response),
            self.assertRaisesRegex(core.ThemeError, "超过"),
        ):
            core._read_url(url)

    def test_retries_transient_network_errors(self):
        url = "https://example.com/index.json"
        response = self.FakeResponse(url, b"ok")
        with (
            mock.patch.object(
                core.urllib.request,
                "urlopen",
                side_effect=[core.urllib.error.URLError("temporary"), response],
            ) as urlopen,
            mock.patch.object(core.time, "sleep") as sleep,
        ):
            content = core._read_url(url)

        self.assertEqual(content, "ok")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()


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

    def test_serializer_cannot_escape_theme_inline_table(self):
        payload = copy.deepcopy(PAYLOAD)
        malicious_key = (
            'escape = 1 }\n[mcp_servers.injected]\ncommand = "bad"\n#'
        )
        payload["theme"][malicious_key] = "ignored"

        updated = core.update_config_text("", payload)
        parsed = tomllib.loads(updated)

        self.assertNotIn("mcp_servers", parsed)
        self.assertEqual(
            parsed["desktop"]["appearanceDarkChromeTheme"][malicious_key], "ignored"
        )


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

    def test_alternates_from_light_to_dark(self):
        themes = [
            {"slug": "light-a", "mode": "light"},
            {"slug": "dark-a", "mode": "dark"},
            {"slug": "dark-b", "mode": "dark"},
        ]

        selected = core.choose_random_theme(
            themes, last_slug="light-a", last_mode="light", chooser=random.Random(1)
        )

        self.assertEqual(selected["mode"], "dark")

    def test_alternates_from_dark_to_light(self):
        themes = [
            {"slug": "light-a", "mode": "light"},
            {"slug": "light-b", "mode": "light"},
            {"slug": "dark-a", "mode": "dark"},
        ]

        selected = core.choose_random_theme(
            themes, last_slug="dark-a", last_mode="dark", chooser=random.Random(1)
        )

        self.assertEqual(selected["mode"], "light")

    def test_explicit_mode_overrides_alternation(self):
        themes = [
            {"slug": "light-a", "mode": "light"},
            {"slug": "dark-a", "mode": "dark"},
        ]

        selected = core.choose_random_theme(
            themes, mode="light", last_mode="light", chooser=random.Random(1)
        )

        self.assertEqual(selected["mode"], "light")


class CliTests(unittest.TestCase):
    def test_windows_wrapper_is_ascii_for_legacy_powershell(self):
        wrapper = Path(__file__).resolve().parents[1] / "codex-theme.ps1"

        self.assertTrue(wrapper.read_bytes().isascii())

    def test_dry_run_never_applies_payload(self):
        themes = [
            {
                "slug": "tokyo-night",
                "mode": "dark",
                "name": "Tokyo Night",
                "import": "themes/imports/tokyo-night.txt",
            }
        ]
        output = io.StringIO()
        with (
            mock.patch.object(core, "load_theme_index", return_value=themes),
            mock.patch.object(core, "download_theme", return_value=PAYLOAD),
            mock.patch.object(core, "apply_payload") as apply_payload,
            contextlib.redirect_stdout(output),
        ):
            result = core.main(["tokyo-night", "--dry-run", "--json"])

        self.assertEqual(result, 0)
        apply_payload.assert_not_called()
        preview = json.loads(output.getvalue())
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["slug"], "tokyo-night")

    def test_display_text_removes_terminal_controls(self):
        self.assertEqual(core.safe_display_text("safe\x1b[31mname"), "safe[31mname")

    def test_windows_restart_targets_current_codex_package(self):
        script = core.windows_restart_script()

        self.assertIn("Get-Process -Name ChatGPT,Codex", script)
        self.assertIn("WindowsApps\\OpenAI.Codex_", script)
        self.assertIn("OpenAI.Codex_*!App", script)


if __name__ == "__main__":
    unittest.main()
