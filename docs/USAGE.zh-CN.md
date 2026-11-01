# 使用说明

[English](USAGE.md) · [返回首页](../README.zh-CN.md)

## 安装方式

Windows 离线包包含应用 wheel、对应依赖 wheel 和摘要校验安装器，需先安装 **CPython 3.13 x64**。按[快速开始](../README.zh-CN.md#安装与启动)操作，为虚拟环境选择不存在的新目录。

也可从 [Releases](https://github.com/ToYOhin/PactTrail-Public/releases/tag/v0.1.0-prealpha.1) 单独下载 `pacttrail-0.1.0-py3-none-any.whl`。下列方式需要联网下载依赖：

```powershell
$venv = Join-Path $env:LOCALAPPDATA 'PactTrail-wheel-venv'
if (Test-Path -LiteralPath $venv) { throw '请选择新的虚拟环境目录。' }
py -3.13 -m venv $venv
$python = Join-Path $venv 'Scripts\python.exe'
& $python -m pip install .\pacttrail-0.1.0-py3-none-any.whl
& $python -m pip check
$pacttrail = Join-Path $venv 'Scripts\pacttrail.exe'
```

任一安装命令失败即停止。这里直接安装下载的文件。保留资产旁的 `SHA256SUMS`，安装前用 `Get-FileHash -Algorithm SHA256` 与对应条目核对。

## 首次启动与重开

```powershell
$workspaceParent = Join-Path $env:LOCALAPPDATA 'PactTrail-workspaces'
New-Item -ItemType Directory -Force -Path $workspaceParent | Out-Null
$workspace = Join-Path $workspaceParent 'my-workspace'
& $pacttrail agent --workspace-dir $workspace
```

父目录须已存在，首次启动时工作区末级目录须不存在，由应用初始化。后续用相同绝对路径重开；不要借用不相关的已有目录，也不要删除工作区来重试错误。

打开终端显示的本机回环地址，保持终端运行。默认自动选空闲端口，重启后地址可能变化。

默认模式使用固定离线模拟决策，演示工具流程并在本机实际保存报告和草案，**不是本地语言模型**。启动、打开网页、查看历史都不请求模型。离线体验时保持可选联网搜索关闭。

退出时在服务终端按 **Ctrl+C**，等待服务完全退出，再用原命令重开。运行中的同一工作区不能再由第二个服务打开。

## 报告与来源

选择“保存报告”，关联所需文件，核对范围后发送，例如“检查 TODO 并保存行动报告”。离线回答是固定演示；真实模型回答需使用下方可选连接。

报告是独立保存的成果，可查看、复制和下载。有实际观察时，来源位置帮助回看所读材料；位置匹配不证明结论正确。

在项目定位中勾选候选文件不会自动把正文发送给模型，仍须预览并确认关联。快照固定所选版本，不会静默跟随磁盘后续变化。

## 草案与文件修改

用新工作区绑定待编辑项目：

```powershell
$project = Read-Host '输入已存在项目目录的完整路径'
$workspace = Join-Path $workspaceParent 'edit-workspace'
& $pacttrail agent --workspace-dir $workspace --edit-project-dir $project
```

项目须已存在，工作区与项目不能相互包含。每次重开重复相同编辑根；换项目须使用另一个新工作区。

1. 选择“修改文件”并关联快照，或选择“新建文件”并填写编辑根内的目标路径。
2. 发送要求，成果生成后打开“查看改动”。
3. 逐个选择文件，查看完整正文与差异。
4. 需要实际写入时再明确确认应用。

关闭审阅或按 Esc 会清除本次同意。若目标相对快照发生变化，应用被拒绝。新文件父目录须已存在。生成草案不会执行代码、运行测试、删除或重命名文件。

Python 语法检查只解析所选快照，不导入或执行项目代码；通过语法检查不代表功能正确。

## 历史与上下文

默认包含完整成功历史。可明确选择其中一部分、选择空集，或恢复默认全部。选择按会话保存；新成果不会自动加入明确选集。原始历史和旧成果仍可查看。

上下文预览显示参考集合、逐项占用和完全重复建议。可准备可编辑的摘录摘要，再明确采用某个版本；它绑定原 run、记录、成果摘要及版本。撤回后恢复原文投影，不会替换原始历史。

摘录在本机生成，不是模型提炼或事实核验。所选范围或绑定版本改变后，摘要会失效；查看后明确撤回或准备新版本，不会扩大选择范围或继承旧授权。

页面草稿与显式保存项不同：切换会话保留各自页面草稿，但整页刷新或关闭浏览器会清除未保存草稿。退出前使用显式保存操作。恢复保存输入不会覆盖正在编辑的正文，也不恢复旧应用授权。

## 可选模型连接

启动或重开工作区时加 `--local-model`；若原工作区绑定了编辑根，仍须重复原参数：

```powershell
& $pacttrail agent --workspace-dir $workspace --edit-project-dir $project --local-model
```

此名称指在本机配置连接，**不会在本机运行模型权重**。当前支持向 DeepSeek 发送明确请求。通过页面输入自己的凭据，依次配置并启用；这些操作不验证鉴权或调用模型，明确发送才会调用。不要把凭据放进命令行。

默认只在服务内存保留凭据。Windows 下可再加 `--remember-model-key`，使用同一 Windows 用户的凭据管理器保存并恢复同工作区凭据；显式清除连接会删除保存项。复制工作区不等于把凭据迁移到另一用户或机器。

可选联网搜索默认关闭。打开后发送会真实访问网络，即使回答处于离线模拟模式。Bing 选项使用公开搜索结果；DeepSeek 搜索需要模型配置模式，可能单独计费。发送前核对预览。

## 容量与恢复

| 项目 | 默认值或上限 |
|---|---|
| 普通问题与回答 | 各 2,048 字符，可配置到 4,096 |
| 单段会话 | 12 次尝试 / 16 KiB 完整上下文，可配置到 32 次 / 64 KiB |
| 所选项目材料 | 最多 4 文件 / 8 KiB |
| 草案替换正文 | 总计最多 4 KiB；草案成果最多 8 KiB |
| 活动会话 | 最多 8 个 |

可选长度和会话容量参数属于本次启动，重开须重复。请求次数、token、时间和费用等上限仍独立生效；超限拒绝新工作，不静默裁剪原始历史。

中断或结果未知时，先核对现有任务记录与收据，再决定后续动作。重开不续跑旧执行，也不自动重发未知请求。不要删除锁或修改工作区清单来强制恢复。

备份前完全关闭服务，复制**整个工作区**并保留目录结构；若绑定编辑根，项目须另行备份。按原绝对工作区路径与项目路径重开。本版本尚未建立跨机器、换路径或断电恢复保证。

## 发布范围

本 Pre-Alpha 具有同机 Windows 离线安装及合成报告、草案、重开流程证据。干净 Windows 新机器、通用真实模型质量、跨平台运行仍未验证。当前产品为本机工作台，不是托管服务或任意代码执行 Agent。
