# PactTrail

[English](README.md) · [下载](https://github.com/ToYOhin/PactTrail-Public/releases/tag/v0.1.0-prealpha.1) · [使用说明](docs/USAGE.zh-CN.md)

运行在本机的 Agent 工作台：连续交流、结合来源生成报告，制作文件草案，审阅确认后再应用。

**0.1.0 · Pre-Alpha · Windows x64 / CPython 3.13**

- 深色简洁界面，保留会话历史，支持显式保存待发输入。
- 选择项目快照、历史回答或成果作为参考，保留完整原文和版本。
- 预览上下文占用，明确采用可编辑的摘录摘要，随时撤回。
- 回看来源位置，比较草案差异，确认后才修改文件。
- 默认离线体验；需要时显式配置已支持的 DeepSeek 连接。

## 安装与启动

先安装 **64 位 CPython 3.13**。从 [Releases](https://github.com/ToYOhin/PactTrail-Public/releases/tag/v0.1.0-prealpha.1) 下载并解压 `pacttrail-0.1.0-windows-cp313-x64-prealpha.1.zip`，在含 `candidate.json` 的解压目录打开 PowerShell。

```powershell
$python = (& py -3.13 -c 'import sys; print(sys.executable)').Trim()
$venv = Join-Path $env:LOCALAPPDATA 'PactTrail-0.1.0-venv'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-candidate.ps1 -Python $python -Destination $venv
```

安装器校验文件摘要，并从包内依赖离线安装；目标必须是**不存在的新目录**。安装失败时停止，不继续启动。交付包不包含 Python；若没有 `py` 启动器，将 `$python` 改为本机 CPython 3.13 解释器的完整路径。

```powershell
$pacttrail = Join-Path $venv 'Scripts\pacttrail.exe'
$workspaceParent = Join-Path $env:LOCALAPPDATA 'PactTrail-workspaces'
New-Item -ItemType Directory -Force -Path $workspaceParent | Out-Null
& $pacttrail agent --workspace-dir (Join-Path $workspaceParent 'my-workspace')
```

打开终端本次显示的 `127.0.0.1` 地址。启动不发送模型请求。默认模式使用固定的离线模拟回答，文件读取和成果保存由本机实际执行；保持“联网搜索”关闭即可离线体验。

## 最短使用路线

1. 选择普通提问、保存报告、修改文件或新建文件。
2. 关联并确认本次需要的材料，参考范围由你明确选择。
3. 发送后打开报告，或查看草案完整正文与差异。
4. 核对后明确确认应用；生成草案、关闭审阅窗口都不会写入目标。

需要修改项目时，用**新工作区**加上 `--edit-project-dir` 指定已存在的项目目录。工作区与项目须相互独立。详见[文件审阅、模型配置和恢复](docs/USAGE.zh-CN.md)。

按 **Ctrl+C** 并等待服务退出，再用相同命令重开原工作区。关闭网页不等于关闭服务。退出前显式保存待发输入；仅留在页面中的草稿不能跨浏览器重启恢复。

## 当前范围

这是预发布版本。离线安装、合成报告、草案审阅及退出重开流程已在同一 Windows 主机验证；**干净 Windows 新机器安装尚未验证**。这不代表通用模型质量或跨平台支持已经验证。

上下文和文件容量有上限，超限拒绝而不静默裁剪原始历史。不提供任意 Shell，不运行项目测试，也不自主执行目标代码；Python 语法检查只解析所选快照。来源链接便于回看，不等于回答事实已经核验。

真实模型及可选联网搜索需要明确使用，可能产生服务商费用。工作区历史和快照可能包含你的材料，请保存在私有位置。

## 许可证

[Apache-2.0](LICENSE)。第三方归属见 [NOTICE](NOTICE) 与[第三方声明](THIRD_PARTY_NOTICES.md)。
