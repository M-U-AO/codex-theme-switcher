#!/usr/bin/env python3
"""Cross-platform Codex Desktop theme switcher.

The tool consumes codex-theme-v1 payloads from shaw-baobao/codex-themes,
backs up the user's Codex config, and updates only desktop appearance keys.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import queue
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable


VERSION = "0.1.0"
THEME_PREFIX = "codex-theme-v1:"
DEFAULT_INDEX_URL = (
    "https://raw.githubusercontent.com/shaw-baobao/codex-themes/"
    "main/themes/index.json"
)
USER_AGENT = f"codex-theme-switcher/{VERSION}"
WINDOWS_TASK_NAME = "CodexThemeSwitcherDaily"
MACOS_LAUNCH_AGENT = "dev.muao.codex-theme-switcher"


class ThemeError(RuntimeError):
    """A user-facing theme switcher error."""


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def default_config_path() -> Path:
    return codex_home() / "config.toml"


def state_directory() -> Path:
    configured = os.environ.get("CODEX_THEME_STATE_DIR")
    return Path(configured).expanduser() if configured else codex_home() / "theme-switcher"


def state_path() -> Path:
    return state_directory() / "state.json"


def _read_url(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8-sig")
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        raise ThemeError(f"无法读取 {url}: {exc}") from exc


def load_theme_index(index_url: str = DEFAULT_INDEX_URL) -> list[dict[str, Any]]:
    try:
        document = json.loads(_read_url(index_url))
    except json.JSONDecodeError as exc:
        raise ThemeError(f"主题索引不是有效 JSON: {exc}") from exc
    themes = document.get("themes") if isinstance(document, dict) else None
    if not isinstance(themes, list):
        raise ThemeError("主题索引缺少 themes 数组")
    valid: list[dict[str, Any]] = []
    for item in themes:
        if not isinstance(item, dict):
            continue
        if not isinstance(item.get("slug"), str):
            continue
        if item.get("mode") not in {"light", "dark"}:
            continue
        if not isinstance(item.get("import"), str):
            continue
        valid.append(item)
    if not valid:
        raise ThemeError("主题索引中没有可用主题")
    return valid


def theme_import_url(theme: dict[str, Any], index_url: str) -> str:
    import_path = str(theme["import"])
    if urllib.parse.urlparse(import_path).scheme:
        return import_path
    # index.json is under themes/, while entries are rooted at the repository.
    return urllib.parse.urljoin(index_url, "../" + import_path.lstrip("/"))


def parse_theme_payload(text: str) -> dict[str, Any]:
    line = next(
        (candidate.strip() for candidate in text.splitlines() if candidate.strip()),
        "",
    )
    if not line.startswith(THEME_PREFIX):
        raise ThemeError(f"主题数据必须以 {THEME_PREFIX} 开头")
    try:
        payload = json.loads(line[len(THEME_PREFIX) :])
    except json.JSONDecodeError as exc:
        raise ThemeError(f"主题数据不是有效 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ThemeError("主题数据根节点必须是对象")
    if payload.get("variant") not in {"light", "dark"}:
        raise ThemeError("主题 variant 必须是 light 或 dark")
    if not isinstance(payload.get("codeThemeId"), str) or not payload["codeThemeId"]:
        raise ThemeError("主题缺少 codeThemeId")
    theme = payload.get("theme")
    if not isinstance(theme, dict):
        raise ThemeError("主题缺少 theme 对象")
    for key in ("accent", "ink", "surface"):
        value = theme.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ThemeError(f"theme.{key} 必须是 #RRGGBB 颜色")
    if not isinstance(theme.get("contrast"), (int, float)):
        raise ThemeError("theme.contrast 必须是数字")
    return payload


def download_theme(
    theme: dict[str, Any], index_url: str = DEFAULT_INDEX_URL
) -> dict[str, Any]:
    return parse_theme_payload(_read_url(theme_import_url(theme, index_url)))


def find_theme(themes: Iterable[dict[str, Any]], slug: str) -> dict[str, Any]:
    normalized = slug.strip().lower()
    for theme in themes:
        if str(theme.get("slug", "")).lower() == normalized:
            return theme
    raise ThemeError(f"找不到主题: {slug}（先运行 list 查看可用主题）")


def choose_random_theme(
    themes: Iterable[dict[str, Any]],
    mode: str | None = None,
    last_slug: str | None = None,
    chooser: random.Random | Any = random,
) -> dict[str, Any]:
    candidates = [item for item in themes if mode is None or item.get("mode") == mode]
    if not candidates:
        raise ThemeError(f"没有符合模式 {mode!r} 的主题")
    alternatives = [item for item in candidates if item.get("slug") != last_slug]
    return chooser.choice(alternatives or candidates)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        fields = ", ".join(
            f"{key} = {_toml_value(item)}" for key, item in value.items()
        )
        return "{ " + fields + " }"
    raise ThemeError(f"不能转换为 TOML 的值: {type(value).__name__}")


def appearance_edits(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    variant = str(payload["variant"])
    title = variant.title()
    return [
        ("appearanceTheme", variant),
        (f"appearance{title}CodeThemeId", payload["codeThemeId"]),
        (f"appearance{title}ChromeTheme", payload["theme"]),
    ]


def update_config_text(text: str, payload: dict[str, Any]) -> str:
    """Replace only the target desktop appearance keys and preserve all others."""

    variant_title = str(payload["variant"]).title()
    code_key = f"appearance{variant_title}CodeThemeId"
    chrome_key = f"appearance{variant_title}ChromeTheme"
    target_table = f"desktop.{chrome_key}"
    keys_to_replace = {"appearanceTheme", code_key, chrome_key}

    lines = text.splitlines(keepends=True)
    filtered: list[str] = []
    current_section: str | None = None
    skipping_target_table = False

    for line in lines:
        section_match = re.match(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$", line.rstrip("\r\n"))
        if section_match:
            section = section_match.group(1).strip()
            skipping_target_table = section == target_table or section.startswith(
                target_table + "."
            )
            current_section = section
            if not skipping_target_table:
                filtered.append(line)
            continue
        if skipping_target_table:
            continue
        if current_section == "desktop":
            assignment = re.match(r"^\s*([A-Za-z0-9_-]+)\s*=", line)
            if assignment and assignment.group(1) in keys_to_replace:
                continue
        filtered.append(line)

    assignments = [f"{key} = {_toml_value(value)}\n" for key, value in appearance_edits(payload)]
    desktop_header = next(
        (
            index
            for index, line in enumerate(filtered)
            if re.match(r"^\s*\[desktop\]\s*(?:#.*)?$", line.rstrip("\r\n"))
        ),
        None,
    )

    if desktop_header is None:
        result = "".join(filtered)
        if result and not result.endswith("\n"):
            result += "\n"
        if result and not result.endswith("\n\n"):
            result += "\n"
        return result + "[desktop]\n" + "".join(assignments)

    insertion = len(filtered)
    for index in range(desktop_header + 1, len(filtered)):
        if re.match(r"^\s*\[", filtered[index]):
            insertion = index
            break
    while insertion > desktop_header + 1 and not filtered[insertion - 1].strip():
        insertion -= 1
    block = assignments + ["\n"]
    filtered[insertion:insertion] = block
    return "".join(filtered)


def load_state() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(data: dict[str, Any]) -> None:
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_write(state_path(), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(text)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def backup_config(config_path: Path) -> Path | None:
    if not config_path.exists():
        return None
    backup_dir = state_directory() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = backup_dir / f"config-{stamp}.toml"
    shutil.copy2(config_path, destination)
    return destination


def _wait_for_rpc_response(
    output_queue: queue.Queue[dict[str, Any] | Exception],
    request_id: int,
    timeout: float,
) -> dict[str, Any]:
    deadline = dt.datetime.now().timestamp() + timeout
    while True:
        remaining = deadline - dt.datetime.now().timestamp()
        if remaining <= 0:
            raise ThemeError("Live RPC 等待响应超时")
        try:
            message = output_queue.get(timeout=remaining)
        except queue.Empty as exc:
            raise ThemeError("Live RPC 等待响应超时") from exc
        if isinstance(message, Exception):
            raise ThemeError(f"Live RPC 读取失败: {message}") from message
        if message.get("id") == request_id:
            return message


def try_live_write(payload: dict[str, Any], config_path: Path) -> tuple[bool, str]:
    """Try config/batchWrite through the managed app-server control socket."""

    if platform.system() != "Darwin":
        return False, "Live 模式目前仅在 macOS 上尝试"
    codex = shutil.which("codex")
    if not codex:
        return False, "找不到 codex 命令"

    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            [codex, "app-server", "proxy"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        assert process.stdin is not None and process.stdout is not None
        messages: queue.Queue[dict[str, Any] | Exception] = queue.Queue()

        def reader() -> None:
            try:
                for raw_line in process.stdout:
                    try:
                        messages.put(json.loads(raw_line))
                    except json.JSONDecodeError:
                        continue
            except Exception as exc:  # pragma: no cover - platform integration
                messages.put(exc)

        threading.Thread(target=reader, daemon=True).start()

        def send(message: dict[str, Any]) -> None:
            assert process is not None and process.stdin is not None
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()

        send(
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    "clientInfo": {
                        "name": "codex_theme_switcher",
                        "title": "Codex Theme Switcher",
                        "version": VERSION,
                    }
                },
            }
        )
        initialized = _wait_for_rpc_response(messages, 1, 4)
        if initialized.get("error"):
            return False, str(initialized["error"])
        send({"method": "initialized", "params": {}})
        edits = [
            {"keyPath": f"desktop.{key}", "value": value, "mergeStrategy": "upsert"}
            for key, value in appearance_edits(payload)
        ]
        send(
            {
                "method": "config/batchWrite",
                "id": 2,
                "params": {
                    "edits": edits,
                    "filePath": str(config_path),
                    "reloadUserConfig": True,
                },
            }
        )
        response = _wait_for_rpc_response(messages, 2, 6)
        if response.get("error"):
            return False, str(response["error"])
        return True, "已通过 config/batchWrite 请求热重载"
    except (OSError, ThemeError) as exc:
        return False, str(exc)
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()


def restart_codex() -> tuple[bool, str]:
    system = platform.system()
    try:
        if system == "Windows":
            script = (
                "$targets = Get-Process -Name Codex -ErrorAction SilentlyContinue | "
                "Where-Object { $_.Path -like '*\\WindowsApps\\OpenAI.Codex_*' }; "
                "$targets | Stop-Process -Force; "
                "Start-Sleep -Milliseconds 700; "
                "$appId = (Get-StartApps | Where-Object { $_.Name -eq 'Codex' } | "
                "Select-Object -First 1).AppID; "
                "if (-not $appId) { $appId = 'OpenAI.Codex_2p2nqsd0c76g0!App' }; "
                "Start-Process explorer.exe -ArgumentList ('shell:AppsFolder\\' + $appId)"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", script],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return False, result.stderr.strip() or "Windows 重启命令失败"
            return True, "已重新启动 Codex Desktop"
        if system == "Darwin":
            subprocess.run(
                ["osascript", "-e", 'tell application "Codex" to quit'],
                check=False,
                capture_output=True,
            )
            result = subprocess.run(
                ["open", "-a", "Codex"], check=False, capture_output=True, text=True
            )
            if result.returncode != 0:
                return False, result.stderr.strip() or "macOS 重启命令失败"
            return True, "已重新启动 Codex"
        return False, "当前平台不支持自动重启，请手动重启 Codex"
    except OSError as exc:
        return False, f"重启 Codex 失败: {exc}"


def apply_payload(
    payload: dict[str, Any],
    slug: str,
    config_path: Path,
    *,
    force_restart: bool = False,
    no_restart: bool = False,
    live: bool | None = None,
) -> list[str]:
    config_path = config_path.expanduser().resolve()
    backup = backup_config(config_path)
    messages: list[str] = []

    attempt_live = live is True or (live is None and platform.system() == "Darwin")
    live_succeeded = False
    if attempt_live:
        live_succeeded, live_message = try_live_write(payload, config_path)
        messages.append(live_message)

    if not live_succeeded:
        original = config_path.read_text(encoding="utf-8-sig") if config_path.exists() else ""
        _atomic_write(config_path, update_config_text(original, payload))
        messages.append(f"已更新 {config_path}")

    should_restart = force_restart or (not no_restart and not live_succeeded)
    if should_restart:
        restarted, restart_message = restart_codex()
        messages.append(restart_message)
        if force_restart and not restarted:
            raise ThemeError(restart_message)

    state = load_state()
    state.update(
        {
            "last_theme": slug,
            "last_mode": payload["variant"],
            "last_applied_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "last_backup": str(backup) if backup else None,
            "config_path": str(config_path),
            "live_succeeded": live_succeeded,
        }
    )
    save_state(state)
    if backup:
        messages.insert(0, f"备份: {backup}")
    return messages


def restore_config(config_path: Path, *, no_restart: bool = False) -> list[str]:
    state = load_state()
    backup_value = state.get("last_backup")
    if not backup_value:
        raise ThemeError("没有可恢复的备份")
    backup = Path(str(backup_value)).expanduser()
    if not backup.exists():
        raise ThemeError(f"备份文件不存在: {backup}")
    config_path = config_path.expanduser().resolve()
    current_backup = backup_config(config_path)
    _atomic_write(config_path, backup.read_text(encoding="utf-8-sig"))
    messages = [f"已从 {backup} 恢复 {config_path}"]
    if current_backup:
        messages.append(f"恢复前配置另存为 {current_backup}")
    if not no_restart:
        _, restart_message = restart_codex()
        messages.append(restart_message)
    state["restored_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    save_state(state)
    return messages


def live_status() -> tuple[bool, str]:
    if platform.system() != "Darwin":
        return False, "managed app-server Live 模式当前仅支持 macOS/Unix"
    codex = shutil.which("codex")
    if not codex:
        return False, "找不到 codex 命令"
    result = subprocess.run(
        [codex, "app-server", "daemon", "version"],
        check=False,
        capture_output=True,
        text=True,
    )
    output = (result.stdout or result.stderr).strip()
    return result.returncode == 0, output or "daemon 未运行"


def setup_live() -> list[str]:
    if platform.system() != "Darwin":
        raise ThemeError("setup-live 当前只支持 macOS")
    codex = shutil.which("codex")
    if not codex:
        raise ThemeError("找不到 codex 命令")
    commands = [
        [codex, "app-server", "daemon", "bootstrap", "--remote-control"],
        [codex, "app-server", "daemon", "start"],
        [codex, "app-server", "daemon", "enable-remote-control"],
    ]
    messages: list[str] = []
    for command in commands:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        output = (result.stdout or result.stderr).strip()
        if result.returncode != 0:
            raise ThemeError(output or f"命令失败: {' '.join(command)}")
        if output:
            messages.append(output)
    ok, status = live_status()
    if not ok:
        raise ThemeError(status)
    messages.append(status)
    messages.append("Live 模式是实验性功能；若界面未刷新，请加 --restart。")
    return messages


def _parse_time(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value)
    if not match:
        raise ThemeError("时间格式必须是 HH:MM，例如 09:00")
    return int(match.group(1)), int(match.group(2))


def install_daily(time_value: str, mode: str | None) -> str:
    hour, minute = _parse_time(time_value)
    script_dir = Path(__file__).resolve().parent
    system = platform.system()
    if system == "Windows":
        wrapper = script_dir / "codex-theme.ps1"
        task_command = (
            f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{wrapper}" random'
        )
        if mode:
            task_command += f" --mode {mode}"
        result = subprocess.run(
            [
                "schtasks.exe",
                "/Create",
                "/SC",
                "DAILY",
                "/ST",
                time_value,
                "/TN",
                WINDOWS_TASK_NAME,
                "/TR",
                task_command,
                "/F",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ThemeError((result.stderr or result.stdout).strip())
        return f"已创建 Windows 每日任务：{time_value} 随机切换主题"
    if system == "Darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{MACOS_LAUNCH_AGENT}.plist"
        arguments = [sys.executable, str(Path(__file__).resolve()), "random"]
        if mode:
            arguments.extend(["--mode", mode])
        argument_xml = "\n".join(
            f"        <string>{_xml_escape(item)}</string>" for item in arguments
        )
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{MACOS_LAUNCH_AGENT}</string>
    <key>ProgramArguments</key>
    <array>
{argument_xml}
    </array>
    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>{minute}</integer></dict>
    <key>StandardOutPath</key><string>{_xml_escape(str(state_directory() / 'daily.log'))}</string>
    <key>StandardErrorPath</key><string>{_xml_escape(str(state_directory() / 'daily-error.log'))}</string>
</dict>
</plist>
"""
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        state_directory().mkdir(parents=True, exist_ok=True)
        _atomic_write(plist_path, plist)
        domain = f"gui/{os.getuid()}"
        subprocess.run(
            ["launchctl", "bootout", domain, str(plist_path)],
            check=False,
            capture_output=True,
        )
        result = subprocess.run(
            ["launchctl", "bootstrap", domain, str(plist_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ThemeError(result.stderr.strip() or "launchctl bootstrap 失败")
        return f"已创建 macOS 每日任务：{time_value} 随机切换主题"
    raise ThemeError("每日任务当前仅支持 Windows 和 macOS")


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def remove_daily() -> str:
    system = platform.system()
    if system == "Windows":
        result = subprocess.run(
            ["schtasks.exe", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ThemeError((result.stderr or result.stdout).strip())
        return "已删除 Windows 每日主题任务"
    if system == "Darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{MACOS_LAUNCH_AGENT}.plist"
        domain = f"gui/{os.getuid()}"
        subprocess.run(
            ["launchctl", "bootout", domain, str(plist_path)],
            check=False,
            capture_output=True,
        )
        plist_path.unlink(missing_ok=True)
        return "已删除 macOS 每日主题任务"
    raise ThemeError("每日任务当前仅支持 Windows 和 macOS")


def print_theme_list(themes: list[dict[str, Any]], mode: str | None) -> None:
    selected = [theme for theme in themes if mode is None or theme.get("mode") == mode]
    width = max((len(str(theme["slug"])) for theme in selected), default=4)
    for theme in selected:
        print(
            f"{str(theme['slug']):<{width}}  {str(theme['mode']):<5}  "
            f"{theme.get('name', '')}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-theme",
        description="切换 Codex Desktop 原生 Appearance 主题",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  codex-theme list
  codex-theme tokyo-night
  codex-theme random --mode dark
  codex-theme restore
  codex-theme install-daily 09:00 --mode dark
  codex-theme setup-live                  # macOS 实验性
""",
    )
    parser.add_argument("command", nargs="?", help="命令或主题 slug")
    parser.add_argument("argument", nargs="?", help="命令参数，例如每日任务时间")
    parser.add_argument("--mode", choices=("light", "dark"), help="筛选主题模式")
    parser.add_argument(
        "--config", type=Path, default=default_config_path(), help="Codex config.toml 路径"
    )
    parser.add_argument("--index-url", default=DEFAULT_INDEX_URL, help="主题索引 URL")
    parser.add_argument("--restart", action="store_true", help="应用后强制重启 Codex")
    parser.add_argument("--no-restart", action="store_true", help="只写配置，不重启 Codex")
    parser.add_argument("--live", action="store_true", help="强制尝试 macOS Live RPC")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command
    if not command:
        parser.print_help()
        return 0
    if args.restart and args.no_restart:
        parser.error("--restart 和 --no-restart 不能同时使用")

    try:
        if command == "list":
            themes = load_theme_index(args.index_url)
            if args.json:
                print(json.dumps(themes, ensure_ascii=False, indent=2))
            else:
                print_theme_list(themes, args.mode)
            return 0
        if command == "status":
            state = load_state()
            state["config_path"] = str(args.config.expanduser().resolve())
            state["config_exists"] = args.config.expanduser().exists()
            if args.json:
                print(json.dumps(state, ensure_ascii=False, indent=2))
            else:
                for key, value in state.items():
                    print(f"{key}: {value}")
            return 0
        if command == "restore":
            messages = restore_config(args.config, no_restart=args.no_restart)
        elif command == "setup-live":
            messages = setup_live()
        elif command == "live-status":
            ok, status = live_status()
            messages = [status]
            if not ok:
                raise ThemeError(status)
        elif command == "install-daily":
            if not args.argument:
                raise ThemeError("install-daily 需要时间，例如 09:00")
            messages = [install_daily(args.argument, args.mode)]
        elif command == "remove-daily":
            messages = [remove_daily()]
        else:
            themes = load_theme_index(args.index_url)
            if command == "random":
                theme = choose_random_theme(
                    themes, mode=args.mode, last_slug=load_state().get("last_theme")
                )
            else:
                theme = find_theme(themes, command)
                if args.mode and theme.get("mode") != args.mode:
                    raise ThemeError(f"主题 {command} 不是 {args.mode} 模式")
            payload = download_theme(theme, args.index_url)
            messages = apply_payload(
                payload,
                str(theme["slug"]),
                args.config,
                force_restart=args.restart,
                no_restart=args.no_restart,
                live=True if args.live else None,
            )
            messages.append(
                f"已应用 {theme.get('name', theme['slug'])} ({payload['variant']})"
            )

        if args.json:
            print(json.dumps({"ok": True, "messages": messages}, ensure_ascii=False, indent=2))
        else:
            for message in messages:
                print(message)
        return 0
    except ThemeError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

