# User guide

[简体中文](USAGE.zh-CN.md) · [Home](../README.md)

## Installation choices

The Windows offline bundle includes the application wheel, matching dependency wheels, and a hash-checking installer. It requires an existing **CPython 3.13 x64** installation. Follow the [quick start](../README.md#install-and-start); choose a new virtual-environment destination.

Alternatively, download `pacttrail-0.1.0-py3-none-any.whl` from [Releases](https://github.com/ToYOhin/PactTrail-Public/releases/tag/v0.1.0-prealpha.1) and install it into a new environment. This option downloads dependencies and requires internet access:

```powershell
$venv = Join-Path $env:LOCALAPPDATA 'PactTrail-wheel-venv'
if (Test-Path -LiteralPath $venv) { throw 'Choose a new environment directory.' }
py -3.13 -m venv $venv
$python = Join-Path $venv 'Scripts\python.exe'
& $python -m pip install .\pacttrail-0.1.0-py3-none-any.whl
& $python -m pip check
$pacttrail = Join-Path $venv 'Scripts\pacttrail.exe'
```

Stop on any failed installation command. This installs the downloaded file directly. Keep the downloaded `SHA256SUMS` beside the assets; compare `Get-FileHash -Algorithm SHA256` output with its entry before installation.

## First launch and reopening

```powershell
$workspaceParent = Join-Path $env:LOCALAPPDATA 'PactTrail-workspaces'
New-Item -ItemType Directory -Force -Path $workspaceParent | Out-Null
$workspace = Join-Path $workspaceParent 'my-workspace'
& $pacttrail agent --workspace-dir $workspace
```

The parent directory must exist; the workspace's final directory must not exist on first launch. PactTrail initializes it. Later, pass the same absolute workspace path to reopen it. Do not use an unrelated existing directory or delete a workspace to retry an error.

Open the loopback address printed at startup. Keep the terminal open. The service chooses an available port by default, so the address may change after restarting.

The default is a deterministic offline mode: it demonstrates the tool workflow and saves actual local reports or drafts. It is not an offline language model. Starting the service, opening a page, and viewing history do not send requests to a model. Keep optional web search off for offline use.

To exit, press **Ctrl+C** in the service terminal and wait for it to stop. Rerun the same command to reopen. An already running workspace cannot be opened by a second service.

## Reports and sources

Choose **Save report**, associate the files you want to use, inspect the selected scope, and send a question such as “Find TODO items and save an action report.” Offline responses are fixed demonstrations; real model answers require the optional connection below.

Reports are independent saved artifacts. You can read, copy, or download them. Source locations show the material actually observed when available; a matching location does not prove the conclusion is correct.

Selecting a candidate file during project search does not automatically send its contents. Preview and confirm the association first. Snapshots preserve the selected version rather than silently following later disk changes.

## Drafts and file changes

Use a new workspace to bind an editing project:

```powershell
$project = Read-Host 'Full path to an existing project directory'
$workspace = Join-Path $workspaceParent 'edit-workspace'
& $pacttrail agent --workspace-dir $workspace --edit-project-dir $project
```

The project must exist. Workspace and project must not contain each other. Every reopening must repeat the same editing root; use a different new workspace to bind another project.

1. Choose **Modify files** and associate the selected snapshots, or choose **New file** and specify the destination within the editing root.
2. Send your request and open **Review changes**.
3. Select each file to inspect the complete text and diff.
4. Confirm application explicitly if you want to write the changes.

Closing the review or pressing Esc clears the review consent. If a target changed since its snapshot, application is refused. New-file parent directories must already exist. Generated drafts do not execute code, run tests, delete files, or rename files.

For Python files, syntax checking parses the selected snapshot without importing or executing project code. Passing syntax checking does not establish functional correctness.

## History and context

Successful history is included by default. You can explicitly choose a subset, choose no successful history, or return to the default. Selection is saved per conversation; new results are not automatically added to an explicitly selected subset. Original history and older artifacts remain available.

The context preview shows the reference set, per-item usage, and exact duplicate suggestions. You may prepare an editable excerpt summary and explicitly adopt its version. It remains bound to the original run, record, artifact hash, and version. Withdrawing it restores the original-text projection. It never replaces the original history.

The excerpt is local, not a model-generated synthesis or a fact check. If its selected references or bound versions change, it becomes invalid; inspect it and explicitly withdraw or prepare a new version. It does not expand the selected scope or inherit previous permissions.

Page drafts and saved input are different: switching conversations preserves each page draft, but reloading the entire page or closing the browser clears unsaved page drafts. Use the explicit save action before exiting. Restoring saved input does not overwrite text currently being composed or restore old application permission.

## Optional model connection

Add `--local-model` when starting or reopening your workspace; repeat its original editing-root option if applicable:

```powershell
& $pacttrail agent --workspace-dir $workspace --edit-project-dir $project --local-model
```

The name refers to configuration on your computer; it **does not run model weights locally**. The supported connection sends explicit requests to DeepSeek. In the page, enter your own credential, configure it, and enable the connection. These setup actions do not test authentication or call the model; sending a request does. Do not put credentials on the command line.

By default the credential lives only in service memory. On Windows, opt into same-workspace credential storage by also passing `--remember-model-key`. This uses Windows Credential Manager for the same Windows user. Explicitly clearing the connection removes its saved credential. Workspace backups do not transfer the credential to another user or machine.

Optional web search is off by default. Enabling it and sending a request performs real network access even with the offline response mode. The Bing option uses public search results; the optional DeepSeek search service requires the model-configuration mode and may charge separately. Inspect the preview before sending.

## Limits and recovery

| Item | Default / bound |
|---|---|
| Ordinary question and answer | 2,048 characters each; configurable to 4,096 |
| Conversation | 12 attempts / 16 KiB complete context; configurable to 32 / 64 KiB |
| Selected project material | Up to 4 files / 8 KiB |
| Draft replacement text | Up to 4 KiB total; draft artifact up to 8 KiB |
| Live conversations | Up to 8 |

Optional length and conversation settings belong to the current launch and must be repeated when reopening. Other request, token, time, and cost limits also apply. Limits reject new work without silently trimming original history.

After interruption or an unknown outcome, inspect the existing task record and receipt before doing anything again. Reopening does not resume old execution or automatically resend an uncertain request. Do not delete locks or alter workspace manifests to force recovery.

For a backup, stop the service completely and copy the **whole workspace**, keeping its directory structure. If editing is bound, back up the project separately. Reopen at the original absolute workspace and project paths. Cross-machine, changed-path, and power-loss recovery are not established for this release.

## Release scope

This Pre-Alpha has same-host Windows offline installation and synthetic report/draft/reopening evidence. A clean Windows machine installation, general real-model quality, and cross-platform operation remain unverified. It is a local workbench, not a hosted service or an arbitrary-code execution agent.
