(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const bytes = value => new TextEncoder().encode(value).byteLength;
  const fields = (value, names) => value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
  const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  const safeText = value => typeof value === "string"
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\ud800-\udfff]/u.test(value);
  const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === "object"
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
  const boundary = "变更草案 · 未应用 · 未执行测试。仅保存到本次任务目录，不修改原项目，也不运行命令。";
  const applyErrors = {
    invalid_request: "应用请求无法核对。", draft_unavailable: "这份草案暂不能应用。",
    target_conflict: "目标文件已变化，未继续写入。", unsafe_path: "目标路径无法安全核对，未继续写入。",
    apply_unavailable: "应用结果不可用，请查看已保存的记录。", apply_unknown: "部分写入结果未知，请人工核对文件。",
  };
  const fileStates = { pending: "待应用", applied: "已写入", not_attempted: "未尝试", unknown: "结果未知" };
  let reviewed = null;
  let paintEditing = () => {};
  let reviewView = null;
  const localSource = source => source && source.kind === "selected_project" && source.version === 1
    && window.PactTrailTaskUI.validProject(source);
  function validRecovered(data) {
    const value = data?.recovered_draft;
    if (value == null) return true;
    const artifact = value.artifact, result = data?.conversation?.result;
    return fields(value, ["run_id", "source", "artifact"]) && /^[a-f0-9]{32}$/.test(value.run_id)
      && data.operation === null && data.task?.run_id === null && !data.task?.artifact
      && (!data.busy || Boolean(validPrecheck(data.draft_precheck))
        && data.draft_precheck.run_id === value.run_id && data.draft_precheck.artifact_sha256 === artifact?.sha256
        && (data.draft_precheck.status === "running" || !data.draft_precheck.reaped))
      && localSource(value.source) && fields(artifact, ["name", "sha256", "bytes", "text"])
      && hash(artifact.sha256) && Number.isSafeInteger(artifact.bytes) && artifact.bytes > 0 && artifact.bytes <= 8192
      && safeText(artifact.text) && bytes(artifact.text) === artifact.bytes
      && result?.run_id === value.run_id && JSON.stringify(canonical(result.source)) === JSON.stringify(canonical(value.source))
      && JSON.stringify(canonical(result.artifact)) === JSON.stringify(canonical(artifact))
      && Boolean(parse(artifact, value.run_id, value.source));
  }
  function effectiveDraft(data) {
    if (data?.recovered_draft) return validRecovered(data)
      ? { ...data.recovered_draft, intent: "change_draft", recovered: true } : null;
    return data?.task?.intent === "change_draft" ? data.task : null;
  }
  const emptyHash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
  const newPath = (path, source) => window.PactTrailTaskUI.validProjectPath(path)
    && !source.files.some(file => window.PactTrailTaskUI.foldPath(file.path) === window.PactTrailTaskUI.foldPath(path));
  const checkStatuses = { idle: "尚未预检", running: "正在预检草案语法", passed: "草案 Python 语法通过",
    failed: "草案 Python 语法失败", cancelled: "预检已取消", timed_out: "预检超时", error: "预检未完成",
    unknown: "预检结果未知，请先核对服务状态；不会自动重试", stale: "旧预检已过期，不能代表当前草案" };
  const checkErrors = new Set(["draft_precheck_stale", "draft_precheck_timeout", "draft_precheck_unknown", "draft_precheck_error"]);
  function validPrecheck(value) {
    return fields(value, ["check_id", "run_id", "artifact_sha256", "status", "exit_code", "files", "error", "reaped",
      "version", "command", "available", "next_id"]) && value.version === 1 && value.command === "python_syntax_draft"
      && Object.hasOwn(checkStatuses, value.status) && typeof value.reaped === "boolean" && typeof value.available === "boolean"
      && /^[a-f0-9]{32}$/.test(value.next_id) && (value.check_id === null || /^[a-f0-9]{32}$/.test(value.check_id))
      && (value.run_id === null || typeof value.run_id === "string") && (value.artifact_sha256 === null || hash(value.artifact_sha256))
      && (value.exit_code === null || Number.isInteger(value.exit_code)) && (value.error === null || checkErrors.has(value.error))
      && Array.isArray(value.files) && value.files.length <= 4 && value.files.every(file => fields(file, ["path", "status", "line"])
        && window.PactTrailTaskUI.validProjectPath(file.path) && file.path.toLowerCase().endsWith(".py")
        && ["pending", "passed", "failed", "not_run"].includes(file.status)
        && (file.line === null || Number.isInteger(file.line) && file.line > 0 && file.line <= 257));
  }
  function failureText(task) {
    if (!task || task.intent !== "change_draft" || !task.error) return "";
    if (task.error === "state_unavailable") return "草案保存结果未确认。请先点击“刷新状态”，核对本次任务记录和草案文件；结果确认前不要重复发送或应用。不会自动重试。";
    if (task.artifact) return "本轮草案任务未完成，但已有草案记录。请先点击“刷新状态”，打开并核对已有内容与可信差异，不要重复生成。不会自动重发或应用。";
    if (task.error === "invalid_response" && task.rejection?.stage === "consumer"
        && task.rejection.reason === "draft_repair_unsupported") {
      return "离线模拟未形成可用修复草案，仅支持受检行的空参数 def name() 缺冒号且修复后仍有实际变更；本次没有保存新草案。失败来自未应用草案的新文本，可清除预填请求后继续。真实模型修复质量仍未验收。";
    }
    if (task.error === "invalid_response" && task.rejection?.stage === "consumer"
        && task.rejection.reason === "syntax_repair_unsupported") {
      return "离线模拟暂不支持此语法错误，仅支持失败行的空参数 def name() 缺冒号；本次没有生成草案。请先点击“刷新状态”核对结果，在编辑器修改后刷新并确认项目范围、重新验证；如仍失败，再采用新的失败反馈并单独发送。不会自动重发。";
    }
    return "本轮草案生成未完成。请先点击“刷新状态”，核对任务记录与草案文件；确认没有已保存或待核对的草案后，如有旧预填请求先清除，再重新核对范围、准备请求并单独发送。不会自动重发或应用。";
  }
  function creationDiff(path, content) {
    const parts = content.split("\n"), lines = parts.slice(0, -1).map(line => line + "\n");
    if (parts[parts.length - 1]) lines.push(parts[parts.length - 1]);
    return "--- /dev/null\n+++ b/" + path + "\n@@ -0,0 +1" + (lines.length === 1 ? "" : "," + lines.length) + " @@\n"
      + lines.map(line => "+" + line + (line.endsWith("\n") ? "" : "\n\\ No newline at end of file\n")).join("");
  }

  function validEditing(value, readOnly = false) {
    if (!fields(value, ["version", "target_root", "run_id", "artifact_sha256", "state", "files", "error"])
      || value.version !== 1 || typeof value.target_root !== "string" || value.target_root.length > 4096
      || !/^(?:[a-z]:[\\/]|\/|\\\\)/i.test(value.target_root) || /[\u0000-\u001f\u007f-\u009f\ud800-\udfff]/u.test(value.target_root)
      || !(value.run_id === null && value.artifact_sha256 === null
        || typeof value.run_id === "string" && /^[a-f0-9]{32}$/.test(value.run_id) && hash(value.artifact_sha256))
      || !["idle", "ready", "applied", "partial", "unknown", "unavailable"].includes(value.state)
      || readOnly && value.state === "ready" || !(value.error === null || typeof value.error === "string" && Object.hasOwn(applyErrors, value.error))
      || !Array.isArray(value.files) || value.files.length > 4
      || !value.files.every(file => fields(file, ["path", "state", "sha256"])
        && window.PactTrailTaskUI.validProjectPath(file.path) && typeof file.state === "string" && Object.hasOwn(fileStates, file.state) && hash(file.sha256))
      || new Set(value.files.map(file => window.PactTrailTaskUI.foldPath(file.path))).size !== value.files.length) return false;
    return value.state !== "ready" || value.run_id !== null && value.files.length > 0
      && value.error === null && value.files.every(file => file.state === "pending");
  }

  function applicationStatus(value) {
    const count = value.files.filter(file => file.state === "applied").length;
    if (value.state === "ready") return "核对下方目标与文件，确认后才会写入。验证未运行。";
    if (value.state === "idle") return "生成并核对变更草案后，可以应用到这个项目。";
    if (value.state === "unavailable") return "无法核对应用结果 · 验证未运行。请先刷新状态并核对本次任务记录与目标文件；结果确认前不要重新生成或应用。" + (value.error ? " " + applyErrors[value.error] : "");
    return "已确认 " + count + " / " + value.files.length + " 已写入 · 验证未运行。"
      + (value.state === "partial" ? "部分文件未完成；请先刷新状态，核对各文件收据与目标文件，不要重新应用旧草案。不会自动重试。"
        : value.state === "unknown" ? "结果未知；请先刷新状态并核对各文件收据与目标文件；结果确认前不要重新生成或应用。不会自动重试。" : "")
      + (value.error ? " " + applyErrors[value.error] : "");
  }

  function applicationFiles(value) {
    return value.files.map(file => {
      const item = document.createElement("li");
      item.textContent = file.path + " · " + fileStates[file.state];
      return item;
    });
  }

  function renderApplication(value, prefix) {
    const target = el(prefix); target.replaceChildren(); target.hidden = !value;
    if (!value) return;
    if (!validEditing(value, true)) { target.textContent = "无法核对应用记录。"; return; }
    const heading = document.createElement("h3"); heading.textContent = "文件应用记录";
    const root = document.createElement("p"); root.textContent = value.target_root;
    const status = document.createElement("p"); status.textContent = applicationStatus(value);
    const files = document.createElement("ul"); files.replaceChildren(...applicationFiles(value));
    target.append(heading, root, status, files);
  }

  function parse(artifact, runId, source) {
    if (!localSource(source) || !artifact || !safeText(artifact.text) || bytes(artifact.text) > 8192
      || artifact.name !== "draft-" + runId + ".json") return null;
    try {
      const value = JSON.parse(artifact.text);
      if (JSON.stringify(canonical(value)) !== artifact.text) return null;
      if (!fields(value, ["version", "kind", "run_id", "source_sha256", "applied", "verification", "changes"])
        || ![1, 2].includes(value.version) || value.kind !== "change_draft" || value.run_id !== runId
        || value.source_sha256 !== source.sha256 || value.applied !== false || value.verification !== "not_run"
        || !Array.isArray(value.changes) || value.changes.length < 1 || value.changes.length > 4) return null;
      if (value.version === 2) {
        const change = value.changes[0];
        if (value.changes.length !== 1 || source.files.length > 3
          || !fields(change, ["file_id", "path", "base_sha256", "new_sha256", "new_text", "diff"])
          || change.file_id !== "new_file" || !newPath(change.path, source) || change.base_sha256 !== emptyHash
          || !hash(change.new_sha256) || change.new_sha256 === emptyHash
          || !safeText(change.new_text) || !change.new_text.trim().length || bytes(change.new_text) > 4096
          || source.byte_count + bytes(change.new_text) > 8192
          || change.new_text.replace(/\r\n|\r/g, "\n").split("\n").length > 256
          || !safeText(change.diff) || change.diff !== creationDiff(change.path, change.new_text)) return null;
        return value;
      }
      let previous = -1, total = 0;
      for (const change of value.changes) {
        if (!fields(change, ["file_id", "path", "base_sha256", "new_sha256", "new_text", "diff"])) return null;
        const index = source.files.findIndex(file => file.file_id === change.file_id), file = source.files[index];
        if (index <= previous || !file || change.path !== file.path || change.base_sha256 !== file.sha256
          || !hash(change.new_sha256) || change.new_sha256 === change.base_sha256
          || !safeText(change.new_text) || !change.new_text.trim().length || !safeText(change.diff)
          || !change.diff.startsWith("--- a/" + file.path + "\n+++ b/" + file.path + "\n@@ ")) return null;
        total += bytes(change.new_text);
        if (total > 4096) return null;
        previous = index;
      }
      return value;
    } catch (_) { return null; }
  }

  async function verify(artifact, runId, source) {
    const value = parse(artifact, runId, source);
    if (!value) return null;
    for (const change of value.changes) {
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(change.new_text));
      const actual = Array.from(new Uint8Array(digest), item => item.toString(16).padStart(2, "0")).join("");
      if (actual !== change.new_sha256) return null;
    }
    return value;
  }

  function clear(prefix, preserveReview = false) {
    if (prefix === "task-draft") {
      reviewed = null;
      if (!preserveReview) { reviewView?.clear(); paintEditing(); }
    }
    el(prefix).hidden = true;
    el(prefix + "-status").textContent = "";
    el(prefix + "-files").replaceChildren();
  }

  function render(value, prefix, artifactSha256 = null) {
    clear(prefix, Boolean(value));
    if (!value) return;
    el(prefix).hidden = false;
    el(prefix + "-status").textContent = boundary;
    const rows = value.changes.map(change => {
      const row = document.createElement("section"); row.className = "draft-file"; row.dataset.draftFileId = change.file_id;
      for (const [tag, className, content] of [
        ["h4", "draft-path", (value.version === 2 ? "新建 · " : "") + change.path],
        ["p", "draft-identity", change.file_id + " · 原 SHA-256 " + change.base_sha256 + "\n新 SHA-256 " + change.new_sha256
          + " · 新文本 " + bytes(change.new_text) + " 字节"],
        ["h5", "", value.version === 2 ? "新文件的完整内容" : "修改后的完整内容"], ["pre", "draft-new-text", change.new_text],
        ["h5", "", value.version === 2 ? "新增文件的差异" : "与原快照的差异"], ["pre", "draft-diff", change.diff],
      ]) {
        const node = document.createElement(tag); node.className = className; node.textContent = content;
        if (className === "draft-diff") {
          node.textContent = "";
          const lines = content.split("\n");
          lines.forEach((line, index) => {
            const span = document.createElement("span");
            span.className = line.startsWith("@@") ? "diff-hunk"
              : line.startsWith("+++") || line.startsWith("---") ? "diff-meta"
                : line.startsWith("+") ? "diff-add" : line.startsWith("-") ? "diff-remove" : "diff-context";
            span.textContent = line + (index < lines.length - 1 ? "\n" : ""); node.appendChild(span);
          });
        }
        if (className === "draft-identity") {
          const details = document.createElement("details"); details.className = "supporting-details";
          const summary = document.createElement("summary"); summary.textContent = "查看文件校验信息";
          details.appendChild(summary); details.appendChild(node); row.appendChild(details);
        } else row.appendChild(node);
      }
      return row;
    });
    el(prefix + "-files").replaceChildren(...rows);
    if (prefix === "task-draft" && hash(artifactSha256)) {
      reviewed = { run_id: value.run_id, artifact_sha256: artifactSha256, source_sha256: value.source_sha256,
        changes: value.changes, version: value.version };
      reviewView?.render(value, artifactSha256);
      paintEditing();
    }
  }

  function create({ state, action, updateControls, prepareRepair, clearRepair }) {
    reviewView = window.PactTrailDraftReviewUI?.create({ state, updateControls }) || null;
    const select = el("task-intent");
    const consent = el("task-apply-confirm"), apply = el("task-apply");
    let editingKey = "", unavailable = false, applying = false, conversationKey = null, prepared = null;
    select.value = "report";
    const toolMode = data => data && ["agent_demo", "agent_model"].includes(data.mode);
    const eligible = data => toolMode(data) && data.project && data.project.kind === "selected_project"
      && data.project.version === 1 && !(data.followup && data.followup.selected);
    const conversational = data => Boolean(data && data.conversation);
    const creatable = data => Boolean(conversational(data) && eligible(data) && validEditing(data.editing)
      && data.project.files.length < 4 && data.project.byte_count < 8192
      && window.PactTrailConversationUI.scopeReady(data));
    const ready = () => repairReady() && (select.value === "answer" && conversational(state().snapshot) || select.value === "report"
      || select.value === "change_draft" && Boolean(eligible(state().snapshot))
      || select.value === "create_file" && creatable(state().snapshot) && newPath(el("task-creation-path").value, state().snapshot.project))
      && (!conversational(state().snapshot) || window.PactTrailConversationUI.scopeReady(state().snapshot));
    function canApply() {
      const current = state(), data = current.snapshot, value = data && data.editing, draft = effectiveDraft(data);
      return Boolean(typeof action === "function" && !unavailable && !applying && !current.locked && !current.mutating
        && toolMode(data) && !data.busy && validEditing(value) && value.state === "ready"
        && draft && draft.run_id === value.run_id && draft.artifact?.sha256 === value.artifact_sha256
        && draft.source?.sha256 === data.project?.sha256 && reviewed && reviewed.source_sha256 === draft.source.sha256
        && (!draft.recovered || data.conversation?.scope.state === "confirmed"
          && data.conversation.scope.sha256 === data.project.sha256)
        && reviewed.run_id === value.run_id && reviewed.artifact_sha256 === value.artifact_sha256
        && value.files.length === reviewed.changes.length && value.files.every((file, index) => {
          const change = reviewed.changes[index]; return file.path === change.path && file.sha256 === change.new_sha256;
        }));
    }
    const repairBinding = data => JSON.stringify([data?.conversation_id, data?.revision,
      data?.draft_precheck?.check_id, data?.task?.run_id, data?.task?.artifact?.sha256]);
    function repairGoal(data) {
      const value = data?.draft_precheck, scope = data?.conversation?.scope;
      if (!conversational(data) || !scope || scope.state !== "confirmed" || scope.sha256 !== data.project?.sha256
        || !canApply() || reviewed.source_sha256 !== data.project.sha256 || data.task.source?.sha256 !== data.project.sha256
        || !window.PactTrailConversationUI.scopeReady(data) || !validPrecheck(value)
        || !value.available || value.status !== "failed" || value.exit_code !== 1 || !value.reaped || value.error !== null
        || data.operation?.status !== "complete" || value.run_id !== data.task.run_id
        || value.artifact_sha256 !== data.task.artifact.sha256) return null;
      const files = reviewed.changes.filter(change => change.path.toLowerCase().endsWith(".py"));
      if (value.files.length !== files.length || !value.files.every((file, index) => file.path === files[index].path
        && ["passed", "failed"].includes(file.status) && (file.status === "failed" ? file.line !== null : file.line === null))) return null;
      const failures = value.files.filter(file => file.status === "failed").map(({path, line}) => ({path, line}));
      if (!failures.length) return null;
      const goal = "修复未应用草案的 Python 语法失败；" + JSON.stringify({check_id:value.check_id,run_id:value.run_id,
        artifact_sha256:value.artifact_sha256,failures});
      return Array.from(goal).length <= 2048 ? goal : null;
    }
    function repairReady() {
      if (prepared && el("message").value === "") { prepared = null; el("message").readOnly = false; }
      return !prepared || repairGoal(state().snapshot) === prepared.goal && repairBinding(state().snapshot) === prepared.key
        && el("message").value === prepared.goal && select.value === "change_draft";
    }
    function repairControls() {
      if (!el("task-draft-repair")) return;
      const current = state(), data = current.snapshot, goal = repairGoal(data), valid = repairReady();
      const blocked = unavailable || current.locked || current.mutating || data?.busy;
      el("task-draft-repair-region").hidden = !prepared && !["failed", "stale"].includes(data?.draft_precheck?.status);
      el("task-draft-repair").disabled = blocked || Boolean(prepared) || !goal || typeof prepareRepair !== "function" || Boolean(el("message").value);
      el("task-draft-repair-clear").hidden = !prepared;
      el("task-draft-repair-clear").disabled = blocked || !prepared || typeof clearRepair !== "function";
      el("task-draft-repair-feedback").textContent = prepared ? valid
        ? "已预填当前未应用草案的失败摘要。请单独发送，再审阅新草案；不会自动修复或应用。"
        : "预填请求与当前草案或失败结果不再一致，请清除后重新采用；不会发送旧反馈。"
        : goal ? "仅采用草案新文本的失败文件与行号；原项目文件未因此执行或修改。"
          : "当前草案失败结果无法完整核对，请先刷新状态；不能采用旧结果或未知结果。";
    }
    function precheck() {
      if (!el("task-draft-precheck")) return; // Old embedded page fixtures remain usable.
      const current = state(), data = current.snapshot, value = data?.draft_precheck;
      const valid = validPrecheck(value), task = effectiveDraft(data);
      const bound = valid && task?.intent === "change_draft" && value.run_id === task.run_id
        && value.artifact_sha256 === task.artifact?.sha256;
      el("task-draft-precheck").hidden = !data || !Object.hasOwn(data, "draft_precheck");
      el("task-draft-check-status").textContent = !valid ? "无法核对预检状态，不能启动。"
        : !bound && value.status !== "idle" ? checkStatuses.stale : checkStatuses[value.status];
      el("task-draft-check-files").replaceChildren(...(valid ? value.files.map(file => {
        const li = document.createElement("li");
        li.textContent = file.path + (file.line === null ? "" : ":" + file.line) + " · "
          + ({ pending: "待检查", passed: "语法通过", failed: "语法失败", not_run: "未检查" })[file.status];
        return li;
      }) : []));
      el("task-draft-check").textContent = valid && !["idle", "running"].includes(value.status) && value.reaped
        ? "重新预检草案语法" : "预检草案 Python 语法";
      el("task-draft-check").disabled = !bound || !valid || !value.available || !canApply();
      const cancel = bound && value.status === "running" && !current.locked && !current.mutating && !unavailable;
      el("task-draft-check-cancel").hidden = !valid || value.status !== "running";
      el("task-draft-check-cancel").disabled = !cancel;
      repairControls();
    }
    function editing() {
      const data = state().snapshot, present = data && Object.hasOwn(data, "editing"), value = present && data.editing;
      const valid = present && validEditing(value);
      const next = JSON.stringify([data && data.conversation_id, data && data.revision, valid ? value : null,
        reviewed && [reviewed.run_id, reviewed.artifact_sha256]]);
      if (next !== editingKey) { consent.checked = false; editingKey = next; }
      el("task-apply-actions").hidden = !present;
      el("task-apply-target").textContent = valid ? value.target_root : "";
      el("task-apply-files").replaceChildren(...(valid ? applicationFiles(value) : []));
      el("task-apply-consent-label").textContent = reviewed && reviewed.version === 2
        ? "我已查看以上差异，确认在此目录中新建这个文件；不覆盖已有文件。"
        : "我已查看以上差异，确认覆盖此目录中的这些文件。";
      el("task-apply-status").textContent = !present ? "" : !valid ? "无法核对应用状态，不能应用修改。"
        : applying ? "正在应用修改，请等待结果；不会自动重试。" : applicationStatus(value);
      if (present && !el("task-draft").hidden) el("task-draft-status").textContent = "草案生成时：未应用 · 未执行测试。实际应用结果单独记录在下方。";
      const allowed = canApply();
      if (!allowed) consent.checked = false;
      consent.disabled = !allowed;
      apply.disabled = !allowed || !consent.checked;
      precheck();
      reviewView?.controls(unavailable);
    }
    paintEditing = editing;
    function paint() {
      const data = state().snapshot, conversation = conversational(data);
      const nextConversation = conversation ? data.conversation.selected : null;
      if (nextConversation !== conversationKey) {
        if (conversation) select.value = "answer";
        else if (select.value === "answer") select.value = "report";
        el("task-creation-path").value = "";
        conversationKey = nextConversation;
      }
      el("task-intent-answer").hidden = !conversation;
      el("task-intent-answer").disabled = !conversation;
      el("task-intent-report").textContent = conversation ? "保存报告" : "回答与报告";
      el("task-intent-draft").textContent = conversation ? "修改文件" : "变更草案";
      const creating = select.value === "create_file", draft = select.value === "change_draft" || creating;
      el("task-intent-create").hidden = !conversation;
      el("task-intent-create").disabled = !creatable(data);
      el("task-creation-controls").hidden = !creating;
      el("task-creation-path").disabled = unavailable || Boolean(data && data.busy) || !creatable(data);
      el("task-creation-note").textContent = !creatable(data)
        ? "请先关联 1–3 个项目文件并指定编辑目录。"
        : el("task-creation-path").value && !newPath(el("task-creation-path").value, data.project)
          ? "请填写未选用的安全相对路径（txt/md/py/json）。"
          : "审阅后创建；父目录须已存在。";
      const scopeNote = data && validEditing(data.editing)
        ? "草案生成时未应用、未执行测试；确认后可修改指定项目。" : boundary;
      el("task-intent-controls").hidden = !toolMode(data);
      el("task-intent-scope").textContent = draft ? scopeNote + (eligible(data) ? "" : " 请关联本地项目文件快照，并先清除已选历史父任务。")
        : conversation ? select.value === "answer" ? "结合这段对话和已关联文件回答。" : "回答并保存一份报告。"
          : "分析本次确认的内容，生成回答与报告。未选文件时使用固定示例。";
      if (draft && eligible(data)) el("task-intent-scope").textContent += "\n当前范围：" + data.project.name
        + "\n" + data.project.files.map(file => file.path).join("\n");
      const option = el("task-intent-draft");
      option.disabled = !eligible(data);
      editing();
    }
    select.addEventListener("change", () => {
      el("task-use-material").checked = false;
      el("task-use-followup").checked = false;
      paint(); updateControls();
    });
    el("task-creation-path").addEventListener("input", () => { paint(); updateControls(); });
    consent.addEventListener("change", editing);
    apply.addEventListener("click", async () => {
      editing();
      if (!canApply() || !consent.checked) return;
      const value = state().snapshot.editing;
      consent.checked = false; applying = true; editing();
      try { await action("/draft-apply", { run_id: value.run_id, artifact_sha256: value.artifact_sha256, confirmed: true }); }
      finally { applying = false; editing(); }
    });
    if (el("task-draft-check")) {
      el("task-draft-check").addEventListener("click", async () => {
        precheck();
        if (el("task-draft-check").disabled) return;
        const data = state().snapshot, value = data.draft_precheck;
        await action("/draft-precheck", { action: "start", check_id: value.next_id, run_id: data.editing.run_id,
          artifact_sha256: data.editing.artifact_sha256, confirmed: true });
      });
      el("task-draft-check-cancel").addEventListener("click", async () => {
        precheck();
        if (el("task-draft-check-cancel").disabled) return;
        await action("/draft-precheck", { action: "cancel", check_id: state().snapshot.draft_precheck.check_id });
      });
    }
    if (el("task-draft-repair")) {
      el("task-draft-repair").addEventListener("click", () => {
        repairControls();
        if (el("task-draft-repair").disabled) return;
        const data = state().snapshot, goal = repairGoal(data);
        if (goal && prepareRepair(goal) === true) {
          prepared = {key:repairBinding(data),goal}; el("message").readOnly = true; updateControls();
        }
      });
      el("task-draft-repair-clear").addEventListener("click", () => {
        repairControls();
        if (el("task-draft-repair-clear").disabled) return;
        if (clearRepair() === true) { prepared = null; el("message").readOnly = false; updateControls(); }
      });
    }
    const checking = () => Boolean(state().snapshot?.draft_precheck
      && (state().snapshot.draft_precheck.status === "running" || !state().snapshot.draft_precheck.reaped));
    return { ready, checking,
      readTaskDraft: () => ({ intent: select.value, creationPath: el("task-creation-path").value }),
      restoreTaskDraft(value, data) {
        prepared = null; el("message").readOnly = false;
        conversationKey = data?.conversation?.selected ?? null;
        select.value = ["answer", "report", "change_draft", "create_file"].includes(value.intent) ? value.intent : "answer";
        el("task-creation-path").value = typeof value.creationPath === "string" ? value.creationPath : "";
        el("task-use-material").checked = false; el("task-use-followup").checked = false;
        consent.checked = false;
      }, pending() {
      return !checking() ? "" : state().snapshot.draft_precheck.status === "running"
        ? "正在预检草案 Python 语法…" : "草案预检结果未知，请先核对服务状态。";
    }, allowed(path, fields) {
      return !checking() || path === "/draft-precheck" && fields.action === "cancel"
        && state().snapshot.draft_precheck.status === "running";
    }, prepareChange() {
      const context = state(), data = context.snapshot;
      if (unavailable || context.locked || context.mutating || data?.busy || !eligible(data) || !validEditing(data.editing)) return false;
      select.value = "change_draft";
      el("task-use-material").checked = false; el("task-use-followup").checked = false;
      consent.checked = false; paint(); updateControls(); return true;
    }, fields: () => select.value === "create_file"
      ? { task_intent: "change_draft", creation_path: el("task-creation-path").value }
      : conversational(state().snapshot) || select.value === "change_draft" ? { task_intent: select.value } : {},
      render: paint, controls: blocked => { unavailable = blocked; select.disabled = blocked || Boolean(state().snapshot && state().snapshot.busy); paint(); } };
  }

  window.PactTrailDraftUI = { create, parse, verify, render, clear, localSource, validRecovered, effectiveDraft,
    requestReview: (runId, sha) => reviewView?.requestOpen(runId, sha),
    validEditing, validPrecheck, renderApplication, failureText };
})();
