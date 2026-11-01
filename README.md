# PactTrail

[简体中文](README.zh-CN.md) · [Download](https://github.com/ToYOhin/PactTrail-Public/releases/tag/v0.1.0-prealpha.1) · [User guide](docs/USAGE.md)

A local agent workbench for conversations, source-backed reports, and file drafts you can review before applying.

**0.1.0 · Pre-Alpha · Windows x64 / CPython 3.13**

- A compact dark interface with persistent conversations and saved input.
- Select project snapshots, previous answers, or artifacts as references; keep their original text and versions.
- Preview context usage, explicitly adopt an editable excerpt summary, and withdraw it later.
- Read source locations, compare draft diffs, and apply changes only after confirmation.
- Try the workflow offline, or explicitly configure the supported DeepSeek connection.

## Install and start

Install **64-bit CPython 3.13** first. Download and extract `pacttrail-0.1.0-windows-cp313-x64-prealpha.1.zip` from [Releases](https://github.com/ToYOhin/PactTrail-Public/releases/tag/v0.1.0-prealpha.1). Open PowerShell in the extracted bundle directory containing `candidate.json`.

```powershell
$python = (& py -3.13 -c 'import sys; print(sys.executable)').Trim()
$venv = Join-Path $env:LOCALAPPDATA 'PactTrail-0.1.0-venv'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-candidate.ps1 -Python $python -Destination $venv
```

The installer checks the bundle hashes and installs its included dependencies offline. The destination must be a **new directory**; stop if installation fails. Python itself is not included. If the Python launcher is unavailable, assign `$python` the full path to your CPython 3.13 executable.

```powershell
$pacttrail = Join-Path $venv 'Scripts\pacttrail.exe'
$workspaceParent = Join-Path $env:LOCALAPPDATA 'PactTrail-workspaces'
New-Item -ItemType Directory -Force -Path $workspaceParent | Out-Null
& $pacttrail agent --workspace-dir (Join-Path $workspaceParent 'my-workspace')
```

Open the `127.0.0.1` address printed by the terminal. Startup sends no model requests. The default mode uses deterministic offline responses, with real local reading and artifact saving. Leave web search off to stay offline.

## Use the workbench

1. Choose a question, report, file modification, or new-file task.
2. Associate and confirm the files you want to use. Reference selection is explicit.
3. Send your request. Open a saved report, or review a draft's full text and diff.
4. Apply only when you are ready. Draft generation and closing the review never apply files.

For file changes, start a **new workspace** with `--edit-project-dir` pointing to an existing project. The workspace and project must be separate directories. See [editing, model setup, and recovery](docs/USAGE.md).

Stop with **Ctrl+C**, wait for the service to exit, and rerun the same command to reopen the workspace. Closing the browser does not stop the service. Explicitly save pending input before closing; page-only drafts do not survive a browser restart.

## Scope

This is a preview release. Offline installation and synthetic report/draft/restart flows have been exercised on the same Windows host. **Installation on a clean Windows machine has not been verified.** This does not establish general model quality or cross-platform support.

PactTrail has bounded context and file sizes, and rejects over-limit input without trimming the original history. It does not provide an arbitrary shell, run your project tests, or autonomously execute project code. Python syntax checks parse selected snapshots only. Source links help inspection; they do not verify the truth of an answer.

Live model requests and optional web search require explicit use and may incur provider charges. Workspace history and snapshots may contain your material: keep the workspace private.

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) and [third-party notices](THIRD_PARTY_NOTICES.md).
