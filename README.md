# PactTrail

A local agent workbench for turning conversations into traceable reports and reviewable file drafts.

[简体中文](README.zh-CN.md) · [Download](https://github.com/ToYOhin/PactTrail-Public/releases/tag/v0.1.0-prealpha.1) · [User guide](docs/USAGE.md)

**0.1.0 · Pre-Alpha · Windows x64 / CPython 3.13**

![PactTrail project Q&A](docs/images/project-conversation.png)

A real API conversation about the project. The interface is currently in Simplified Chinese.

## How it works

1. **Start a conversation.** Ask directly, or explicitly select project snapshots, earlier answers, and artifacts as references; preview the context when needed.
2. **Choose the output.** Continue with an answer, save a report, or generate an independent file draft. Earlier history and artifacts remain available.
3. **Review and decide.** Inspect sources and versions. For file changes, read the full text and diff, then explicitly confirm application.

## Project Q&A

<details>
<summary>What makes PactTrail different from a plain chat UI?</summary>

It connects conversation history and selected sources to saved reports and reviewable file drafts. Original text and artifact versions remain available for inspection.

</details>

<details>
<summary>Does generating a draft change project files?</summary>

Drafts are saved separately; writing files requires explicit confirmation after review. If a target has changed since its snapshot, application is refused.

</details>

<details>
<summary>Can I try it without a model connection?</summary>

Yes. The default offline mode demonstrates the workflow with fixed responses. Explicitly configure the supported DeepSeek connection for live answers; leave optional web search off to stay offline.

</details>

## Install and start

Install **CPython 3.13 x64**, download and extract the [Windows offline bundle](https://github.com/ToYOhin/PactTrail-Public/releases/tag/v0.1.0-prealpha.1), then follow the quick start below. See the [user guide](docs/USAGE.md) for sources, editing, model setup, and reopening.

<details>
<summary>Windows offline quick start</summary>

Open PowerShell in the extracted directory containing `candidate.json`. Choose a new installation directory:

```powershell
$python = (& py -3.13 -c 'import sys; print(sys.executable)').Trim()
$venv = Join-Path $env:LOCALAPPDATA 'PactTrail-0.1.0-venv'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-candidate.ps1 -Python $python -Destination $venv
```

The installer checks the bundle hashes and installs included dependencies offline. Stop if installation fails. Python itself is not bundled; if `py` is unavailable, set `$python` to your CPython 3.13 executable's full path.

```powershell
$pacttrail = Join-Path $venv 'Scripts\pacttrail.exe'
$workspaceParent = Join-Path $env:LOCALAPPDATA 'PactTrail-workspaces'
New-Item -ItemType Directory -Force -Path $workspaceParent | Out-Null
& $pacttrail agent --workspace-dir (Join-Path $workspaceParent 'my-workspace')
```

Open the loopback address printed by the terminal. Startup sends no model requests; default responses are fixed offline demonstrations. Configure the supported model connection explicitly for live conversations. Stop with **Ctrl+C**, then repeat the same command to reopen. Explicitly save pending input before exiting.

</details>

<details>
<summary>Version notes</summary>

This is a project showcase and Pre-Alpha preview. Clean Windows installation, cross-platform operation, and general model quality remain unverified. Live models and optional web search require explicit use and may incur provider charges. There is no arbitrary shell or autonomous project-code execution.

</details>

## License

[Apache-2.0](LICENSE) · [NOTICE](NOTICE) · [Third-party notices](THIRD_PARTY_NOTICES.md)
