# Codex Theme Switcher

跨平台切换 Codex Desktop 原生 Appearance 主题：在线读取社区主题库，支持随机、备份恢复和每日自动切换，不注入 CSS，也不修改 Codex 应用文件。

> 非 OpenAI 官方项目。Codex Desktop 的主题配置仍可能随版本更新而变化。

## 功能

- Windows 与 macOS 使用一致的命令
- 自动读取 [`shaw-baobao/codex-themes`](https://github.com/shaw-baobao/codex-themes) 的最新主题索引
- 应用 `codex-theme-v1:` 原生导入数据
- 支持 Light / Dark 筛选；裸 `random` 会在亮暗模式间自动交替
- 随机时避免连续重复上一次主题
- 修改前自动备份 `config.toml`，可一键恢复
- `--dry-run` 安全预览，不写配置、不重启 Codex
- 对远程 URL、下载大小、主题字段和 TOML 键进行安全校验
- Windows Task Scheduler / macOS launchd 每日自动切换
- macOS 实验性 App Server Live RPC，失败时自动回退到重启

## 环境要求

- Codex Desktop
- Python 3.10+
- Windows 10/11，或 macOS

Windows 尚未安装 Python 时，可以先执行：

```powershell
winget install Python.Python.3.13
```

Windows 版也会尝试使用 Codex Desktop 随附的 Python 运行时作为后备；该路径不是公开 API，长期使用仍建议单独安装 Python。

## 安装

```bash
git clone https://github.com/M-U-AO/codex-theme-switcher.git
cd codex-theme-switcher
```

macOS：

```bash
chmod +x codex-theme
./codex-theme list
```

Windows PowerShell：

```powershell
.\codex-theme.ps1 list
```

## 使用

查看全部主题：

```bash
codex-theme list
```

切换到指定主题：

```bash
codex-theme tokyo-night
codex-theme catppuccin-mocha
codex-theme nord
```

先预览将要写入的字段，不修改真实配置：

```bash
codex-theme tokyo-night --dry-run
codex-theme tokyo-night --dry-run --json
```

随机主题：

```bash
codex-theme random
codex-theme random --mode dark
codex-theme random --mode light
```

不指定 `--mode` 时，第一次从全部主题随机选择，之后会在上次模式的反面随机，
即 Light 与 Dark 每次交替。显式指定 `--mode` 时始终按指定模式选择。

默认情况下，普通配置写入后会重新启动 Codex Desktop，以确保界面刷新。只写配置、不重启：

```bash
codex-theme tokyo-night --no-restart
```

恢复最近一次修改前的配置：

```bash
codex-theme restore
```

查看状态：

```bash
codex-theme status
```

## 每日自动随机

每天 09:00 在亮色与暗色模式间交替，并在对应模式内随机选择主题：

```bash
codex-theme install-daily 09:00
```

如需固定为暗色主题，可使用 `codex-theme install-daily 09:00 --mode dark`。

取消每日任务：

```bash
codex-theme remove-daily
```

Windows 使用 Task Scheduler，macOS 使用用户级 launchd LaunchAgent。

## macOS 实验性 Live 模式

Codex App Server 当前提供 Unix socket transport 和 `config/batchWrite`。本机 Codex 协议 Schema 还定义了 `reloadUserConfig: true`，因此本项目可尝试通过 managed app-server 写入并热重载配置。

首次设置：

```bash
codex-theme setup-live
codex-theme live-status
```

之后正常切换即可。若配置已经改变但界面没有即时刷新，使用：

```bash
codex-theme tokyo-night --restart
```

Live 模式是实验性能力；OpenAI 文档将部分 App Server transports 标注为实验性，不应把它用于远程公开监听。参见 [OpenAI Codex App Server 文档](https://developers.openai.com/codex/app-server/)。

## 它会修改什么

项目只更新 `~/.codex/config.toml`（Windows 为 `%USERPROFILE%\.codex\config.toml`）的以下键：

```toml
[desktop]
appearanceTheme = "dark"
appearanceDarkCodeThemeId = "tokyo-night"
appearanceDarkChromeTheme = { ... }
```

Light 主题对应 `appearanceLightCodeThemeId` 和 `appearanceLightChromeTheme`。模型、MCP、权限等其他配置保持不变。

## 安全边界

- 只接受 HTTPS 主题源，主题导入必须与索引同源并位于同一仓库路径下。
- 下载内容限制为 2 MiB，并限制主题字段数量、嵌套深度、字符串长度和字段名格式。
- 远程主题只能成为 `[desktop]` 下的外观值；TOML 键会安全序列化，不能借主题字段注入其他配置段。
- 默认社区索引仍来自第三方仓库的 `main` 分支。使用前可先执行 `--dry-run`；对供应链要求更高时，应使用自己审查和托管的 `--index-url`。
- Windows 普通应用会重启 Codex Desktop，可能中断正在进行的任务；`--no-restart` 只写配置。

备份和状态保存在：

```text
~/.codex/theme-switcher/
```

## 开发与测试

```bash
python -m unittest discover -s tests -v
python -m py_compile codex_theme_core.py
```

## 上游与致谢

- 主题数据：[`shaw-baobao/codex-themes`](https://github.com/shaw-baobao/codex-themes)
- App Server 协议：[OpenAI 官方文档](https://developers.openai.com/codex/app-server/)

主题本身归各自作者所有；本仓库只负责读取并应用其导入数据。

## License

[MIT](LICENSE)
