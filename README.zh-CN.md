# PactTrail

把对话推进成有来源可回看、有改动可审阅的本地成果。

[English](README.md) · [下载](https://github.com/ToYOhin/PactTrail-Public/releases/tag/v0.1.0-prealpha.1) · [使用说明](docs/USAGE.zh-CN.md)

**0.1.0 · Pre-Alpha · Windows x64 / CPython 3.13**

![PactTrail 项目问答](docs/images/project-conversation.png)

真实 API 项目问答，界面为简体中文。

## 从提问到成果

1. **准备问题**：直接提问，或明确选择项目快照、历史回答和成果作为参考；按需预览本次上下文。
2. **生成成果**：得到回答、独立报告或文件草案，保留原文、旧历史和旧成果。
3. **核对改动**：回看来源，查看草案全文与差异，需要写入文件时明确确认应用。

## 关于项目

<details>
<summary>与普通聊天界面有什么不同？</summary>

PactTrail 把参考材料、来源版本、对话和独立成果放在同一条工作流中，便于回看，也便于基于旧成果继续迭代。

</details>

<details>
<summary>生成草案会直接修改项目文件吗？</summary>

草案作为独立成果保存。查看完整正文与差异、明确确认后才写入；目标相对原快照发生变化时，应用会被拒绝。

</details>

<details>
<summary>不连接模型也能体验吗？</summary>

可以。默认的固定离线演示可体验界面和本地成果流程；真实交流需显式配置 DeepSeek 连接。离线体验时保持可选联网搜索关闭。

</details>

## 安装与启动

先安装 **CPython 3.13 x64**，下载并解压 [Windows 离线包](https://github.com/ToYOhin/PactTrail-Public/releases/tag/v0.1.0-prealpha.1)，按下方步骤启动。来源关联、文件修改、模型配置和重开方式见[使用说明](docs/USAGE.zh-CN.md)。

<details>
<summary>Windows 离线快速开始</summary>

在含 `candidate.json` 的解压目录打开 PowerShell，为安装选择不存在的新目录：

```powershell
$python = (& py -3.13 -c 'import sys; print(sys.executable)').Trim()
$venv = Join-Path $env:LOCALAPPDATA 'PactTrail-0.1.0-venv'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-candidate.ps1 -Python $python -Destination $venv
```

安装器校验文件摘要，并从包内依赖离线安装。安装失败时停止，不继续启动。包内不含 Python；若没有 `py` 启动器，将 `$python` 改为 CPython 3.13 解释器的完整路径。

```powershell
$pacttrail = Join-Path $venv 'Scripts\pacttrail.exe'
$workspaceParent = Join-Path $env:LOCALAPPDATA 'PactTrail-workspaces'
New-Item -ItemType Directory -Force -Path $workspaceParent | Out-Null
& $pacttrail agent --workspace-dir (Join-Path $workspaceParent 'my-workspace')
```

打开终端本次显示的本机地址。启动不发送模型请求，默认回答为固定离线演示；真实交流需显式配置已支持的模型连接。按 **Ctrl+C** 退出，再用相同命令重开；退出前显式保存待发输入。

</details>

<details>
<summary>版本与使用范围</summary>

本仓库用于项目展示，当前为 Pre-Alpha。干净 Windows 安装、跨平台和通用模型质量仍未验证。默认演示使用固定规则；真实 API 对话及可选联网搜索可能产生服务商费用。当前不提供通用 Shell 或自主执行项目代码。

</details>

## 许可证

[Apache-2.0](LICENSE) · [NOTICE](NOTICE) · [第三方声明](THIRD_PARTY_NOTICES.md)
