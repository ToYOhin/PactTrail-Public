(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const fields = (value, names) => value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
  const id = value => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  const integer = (value, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(value) && value >= 0 && value <= max;
  const bounded = (value, max) => typeof value === "string" && Array.from(value).length <= max
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\ud800-\udfff]/u.test(value);
  const bytes = value => new TextEncoder().encode(value).byteLength;
  const optional = (value, names) => names.filter(name => Object.hasOwn(value, name));
  function validMetadata(value, active = []) {
    return Boolean(fields(value, ["version", "conversations", "max_conversations"]) && value.version === 1
      && value.max_conversations === 24 && Array.isArray(value.conversations) && value.conversations.length <= 24
      && value.conversations.every(item => fields(item, ["id", "title", "turns"]) && id(item.id)
        && bounded(item.title, 80) && item.title.trim() && !/[\r\n\t]/.test(item.title) && integer(item.turns, 32))
      && new Set(value.conversations.map(item => item.id)).size === value.conversations.length
      && value.conversations.every(item => !active.some(entry => entry.id === item.id)));
  }
  const identity = value => fields(value, ["task_id", "run_id", "kind", "record_sha256", "artifact_sha256"])
    && id(value.task_id) && id(value.run_id) && ["report", "change_draft"].includes(value.kind)
    && hash(value.record_sha256) && hash(value.artifact_sha256);
  function validArtifact(artifact, turn) {
    if (artifact === null) return turn.intent === "answer" || turn.status !== "complete";
    if (!fields(artifact, ["name", "sha256", "bytes", "text"]) || !hash(artifact.sha256)
      || !integer(artifact.bytes, 8192) || artifact.bytes < 1 || !bounded(artifact.text, 8192)
      || bytes(artifact.text) !== artifact.bytes) return false;
    const draft = turn.intent === "change_draft";
    return artifact.name === (draft ? "draft-" : "report-") + turn.run_id + (draft ? ".json" : ".md")
      && (!draft || Boolean(window.PactTrailDraftUI?.parse(artifact, turn.run_id, turn.source)));
  }
  function validTurn(value, revision) {
    if (!value || !fields(value, ["run_id", "request_revision", "goal", "intent", "status", "error", "answer", "source", "artifact", "application",
      ...optional(value, ["web_search", "artifact_reference"])]) || !id(value.run_id)
      || !integer(value.request_revision, revision) || !["answer", "report", "change_draft"].includes(value.intent)
      || !bounded(value.goal, value.intent === "answer" ? 4096 : 2048) || !value.goal.trim()
      || !["running", "complete", "stopped", "error"].includes(value.status)
      || !(value.error === null || typeof value.error === "string" && /^[a-z_]{1,64}$/.test(value.error))
      || !(value.answer === null || bounded(value.answer, value.intent === "answer" ? 4096 : 512) && value.answer.trim())
      || value.status === "complete" && (value.answer === null || value.error !== null)
      || value.status === "error" && value.error === null || value.status === "running" && value.error !== null
      || !(value.source === null || window.PactTrailTaskUI?.validSource(value.source))
      || !validArtifact(value.artifact, value)
      || Object.hasOwn(value, "artifact_reference") && !identity(value.artifact_reference)
      || Object.hasOwn(value, "web_search") && (value.status !== "complete" || value.intent !== "answer"
        || !window.PactTrailWebSearchUI?.validSearch(value.web_search))) return false;
    if (value.intent === "change_draft" && (!value.source || value.source.kind !== "selected_project")) return false;
    if (value.application !== null) {
      const draft = value.intent === "change_draft" && window.PactTrailDraftUI?.parse(value.artifact, value.run_id, value.source);
      if (!draft || !window.PactTrailDraftUI.validEditing(value.application, true)
        || value.application.run_id !== value.run_id || value.application.artifact_sha256 !== value.artifact.sha256
        || value.application.files.length !== draft.changes.length || !value.application.files.every((file, index) =>
          file.path === draft.changes[index].path && file.sha256 === draft.changes[index].new_sha256)) return false;
    }
    return true;
  }
  function validConversation(value, revision) {
    return Boolean(value && fields(value, ["id", "title", "project", "scope_state", "turns",
      ...optional(value, ["saved_input", "artifact_reference"])]) && id(value.id)
      && bounded(value.title, 80) && value.title.trim() && !/[\r\n\t]/.test(value.title)
      && ["empty", "confirmed", "review_required"].includes(value.scope_state)
      && (value.project === null ? value.scope_state === "empty"
        : value.scope_state !== "empty" && window.PactTrailTaskUI?.validProject(value.project, true))
      && Array.isArray(value.turns) && value.turns.length <= 32 && value.turns.every(turn => validTurn(turn, revision))
      && new Set(value.turns.map(turn => turn.run_id)).size === value.turns.length
      && (!Object.hasOwn(value, "saved_input") || window.PactTrailSavedInputUI?.valid(value.saved_input))
      && (!Object.hasOwn(value, "artifact_reference") || window.PactTrailArtifactReferenceUI?.validReference(value.artifact_reference))
      && bytes(JSON.stringify(value)) <= 262144);
  }
  function valid(value) {
    return Boolean(fields(value, ["version", "kind", "conversation_id", "revision", "archived"])
      && value.version === 1 && value.kind === "conversation_archive" && id(value.conversation_id)
      && integer(value.revision, 2147483647) && validConversation(value.archived, value.revision));
  }
  const handles = (path, body) => path === "/conversation" && body?.action === "restore_archive";
  const paragraph = text => { const node = document.createElement("p"); node.textContent = text; return node; };
  const pre = text => { const node = document.createElement("pre"); node.className = "source-evidence-text"; node.tabIndex = 0; node.textContent = text; return node; };
  function detail(title, text) {
    const node = document.createElement("details"), heading = document.createElement("summary");
    node.className = "supporting-details"; heading.textContent = title; node.append(heading, pre(text)); return node;
  }
  const statusLabel = { running: "运行记录", complete: "已完成", stopped: "已停止", error: "失败" };
  const errorText = code => ({ stale_request: "当前状态已变化，请重新查看。", busy: "当前任务或资源尚未退出，请稍后再操作。",
    history_limit: "活动会话或归档记录已满，请先归档一个活动会话再恢复，或核对记录空间。",
    history_unavailable: "会话记录暂不可用，当前输入保持。", invalid_request: "这份归档已变化，请刷新状态后核对。" })[code]
    || "暂时无法完成会话归档操作，当前输入保持；可刷新状态核对，不会自动重试。";
  function create({ state, request, action, updateControls }) {
    let owner = "", run = "", selected = "", generation = 0, pending = null, value = null, painted = "", listStamp = "";
    let unavailable = false, closed = false, confirmation = "", status = "";
    const panel = el("conversation-archive-panel"), preview = el("conversation-archive-preview"), nodes = new Map();
    const metadata = data => data?.conversation?.archive;
    const available = data => !closed && data?.conversation?.persistent === true && id(data.conversation_id)
      && typeof request === "function" && validMetadata(metadata(data), data.conversation.conversations);
    const unsettled = data => [data?.connection, data?.task?.model].some(ledger => ledger
      && (ledger.cost_known === false || ledger.reserved_nano_usd > 0));
    const blocked = (current, mutation = false) => unavailable || current.locked || current.mutating
      || mutation && (current.snapshot?.busy || unsettled(current.snapshot) || current.snapshot?.operation?.status === "running"
        || current.snapshot?.verification?.status === "running" || current.snapshot?.verification?.reaped === false
        || current.snapshot?.draft_precheck?.status === "running" || current.snapshot?.draft_precheck?.reaped === false);
    const signature = data => data.conversation_id + ":" + data.revision;
    function clearConfirmation() { confirmation = ""; el("conversation-archive-confirmation").hidden = true; }
    function clearPreview() {
      generation += 1; pending = null; value = null; painted = "";
      preview.open = false; preview.hidden = true; el("conversation-archive-content").hidden = true;
      for (const name of ["dialogue", "saved", "reference", "scope"]) el("conversation-archive-" + name).open = false;
      el("conversation-archive-turns").replaceChildren();
      for (const name of ["title", "saved-text", "reference-text", "scope-text"]) el("conversation-archive-" + name).textContent = "";
    }
    function renderTurn(turn, index) {
      const row = document.createElement("details"), heading = document.createElement("summary");
      row.className = "supporting-details"; heading.textContent = "第 " + (index + 1) + " 次 · " + statusLabel[turn.status] + " · " + turn.goal.slice(0, 28);
      row.append(heading, paragraph("运行身份：" + turn.run_id + " · " + turn.intent), paragraph("原问题"), pre(turn.goal));
      if (turn.answer !== null) row.append(paragraph("原回答"), pre(turn.answer));
      if (turn.error) row.append(paragraph("保存的结果：" + turn.error));
      if (turn.source !== null) row.append(detail("查看该次来源身份", JSON.stringify(turn.source, null, 2)));
      if (turn.artifact !== null) row.append(detail("查看保存成果原文 · " + turn.artifact.name,
        "SHA256 " + turn.artifact.sha256 + " · " + turn.artifact.bytes + " 字节\n\n" + turn.artifact.text));
      if (turn.application !== null) row.append(detail("查看该次应用收据（不授予新授权）", JSON.stringify(turn.application, null, 2)));
      if (turn.artifact_reference) row.append(detail("查看该次明确参考身份", JSON.stringify(turn.artifact_reference, null, 2)));
      if (turn.web_search) row.append(detail("查看保存的搜索来源", JSON.stringify(turn.web_search, null, 2)));
      return row;
    }
    function paint() {
      if (!value) return;
      const stamp = JSON.stringify(value.archived); if (stamp === painted) return; painted = stamp;
      const saved = value.archived;
      el("conversation-archive-title").textContent = saved.title;
      el("conversation-archive-note").textContent = "只读归档 · " + saved.turns.length + " 次提问 · " + saved.id
        + "。恢复仅放回活动列表，请手动选择；来源和参考需重新核对，不恢复执行或应用授权。";
      el("conversation-archive-turns").replaceChildren(...(saved.turns.length ? saved.turns.map(renderTurn) : [paragraph("这段会话尚无已发送的问题。") ]));
      el("conversation-archive-saved").hidden = !saved.saved_input;
      el("conversation-archive-saved-text").textContent = saved.saved_input
        ? "任务类型：" + saved.saved_input.task_intent + "\n新建路径：" + (saved.saved_input.creation_path ?? "无")
          + "\n保存项身份：" + saved.saved_input.id + "\n\n" + saved.saved_input.message : "";
      el("conversation-archive-reference").hidden = !saved.artifact_reference;
      el("conversation-archive-reference-text").textContent = saved.artifact_reference
        ? "原成果：" + saved.artifact_reference.artifact.name + "\n任务/运行：" + saved.artifact_reference.task_id + " / "
          + saved.artifact_reference.run_id + "\nSHA256 " + saved.artifact_reference.artifact.sha256 + "\n\n" + saved.artifact_reference.artifact.text : "";
      el("conversation-archive-scope").hidden = !saved.project;
      el("conversation-archive-scope-text").textContent = saved.project ? JSON.stringify(saved.project, null, 2) : "";
      el("conversation-archive-content").hidden = false;
    }
    function render() {
      const current = state(), data = current.snapshot, active = available(data), nextOwner = active ? data.conversation_id : "", nextRun = data?.task?.run_id || "";
      if (owner !== nextOwner || run !== nextRun) {
        owner = nextOwner; run = nextRun; selected = ""; status = ""; listStamp = ""; clearPreview(); clearConfirmation(); panel.open = false;
      }
      panel.hidden = !active; el("conversation-archive").hidden = !active;
      if (!active) { preview.hidden = true; clearConfirmation(); return; }
      const items = metadata(data).conversations;
      if (selected && !items.some(item => item.id === selected)) { selected = ""; clearPreview(); }
      const stamp = JSON.stringify(items);
      if (listStamp !== stamp) {
        listStamp = stamp;
        const next = items.map(item => {
          let button = nodes.get(item.id);
          if (!button) { button = document.createElement("button"); button.type = "button"; button.dataset.archiveId = item.id;
            button.addEventListener("click", () => open(item.id)); nodes.set(item.id, button); }
          button.textContent = item.title + " · " + item.turns + " 次"; return button;
        });
        for (const key of nodes.keys()) if (!items.some(item => item.id === key)) nodes.delete(key);
        el("conversation-archive-list").replaceChildren(...(next.length ? next : [paragraph("暂无归档会话。") ]));
      }
      const canMutate = !blocked(current, true) && !pending && [null, "context_limit"].includes(data.conversation.error);
      el("conversation-archive").disabled = !canMutate || items.length >= metadata(data).max_conversations;
      if (!canMutate || confirmation !== signature(data)) clearConfirmation();
      el("conversation-archive-confirm").disabled = !canMutate || !confirmation;
      el("conversation-archive-summary").textContent = "已归档的会话 · " + items.length + " / " + metadata(data).max_conversations;
      el("conversation-archive-status").textContent = status || (items.length >= metadata(data).max_conversations
        ? "归档已满，暂不能继续归档。请先恢复并整理旧会话；不会自动删除任何记录。"
        : "归档保留完整内容，不计入8个活动会话。");
      for (const button of nodes.values()) button.disabled = blocked(current) || Boolean(pending);
      preview.hidden = !selected; el("conversation-archive-load").disabled = !selected || blocked(current) || Boolean(pending);
      el("conversation-archive-restore").disabled = !selected || !value || !canMutate || data.conversation.conversations.length >= 8;
      el("conversation-archive-restore-note").textContent = data.conversation.conversations.length >= 8
        ? "活动会话已满，请先显式归档一个活动会话，再恢复；当前输入保持。"
        : "恢复后仍停留在当前对话，可从活动列表手动选择。";
      paint();
    }
    const owns = binding => !closed && owner === binding.owner && run === binding.run && generation === binding.generation
      && selected === binding.selected && state().snapshot?.conversation_id === binding.owner
      && (state().snapshot?.task?.run_id || "") === binding.run;
    async function verifyArtifacts(archived, binding) {
      const artifacts = archived.turns.filter(turn => turn.artifact).map(turn => turn.artifact);
      if (archived.artifact_reference) artifacts.push(archived.artifact_reference.artifact);
      for (const artifact of artifacts) {
        const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(artifact.text));
        if (!owns(binding)) return false;
        if (state().snapshot?.revision !== binding.revision) throw Error("stale_request");
        if (Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("") !== artifact.sha256) throw Error("invalid_response");
      }
      return true;
    }
    async function read() {
      render(); const current = state(), data = current.snapshot;
      if (!available(data) || !selected || blocked(current) || pending) return;
      const binding = { owner, run, selected, generation, revision: data.revision };
      pending = binding; status = "正在读取归档全文…"; updateControls();
      try {
        const response = await request("/conversation", { action: "read_archive", archived_id: selected, conversation_id: owner, revision: data.revision });
        if (!owns(binding)) return;
        if (state().snapshot?.revision !== binding.revision) throw Error("stale_request");
        if (!valid(response) || response.conversation_id !== binding.owner || response.revision !== binding.revision
          || response.archived.id !== binding.selected) throw Error("invalid_response");
        if (!await verifyArtifacts(response.archived, binding) || !owns(binding)) return;
        if (state().snapshot?.revision !== binding.revision) throw Error("stale_request");
        value = response; status = "已读取归档全文；查看不会发送问题或执行旧任务。";
      } catch (error) { if (owns(binding)) { value = null; painted = ""; el("conversation-archive-content").hidden = true; status = errorText(error.message); } }
      finally { if (pending === binding) pending = null; updateControls(); }
    }
    function open(archivedId) {
      render(); const current = state(), data = current.snapshot;
      if (!available(data) || blocked(current) || pending || !metadata(data).conversations.some(item => item.id === archivedId)) return;
      if (selected !== archivedId) { clearPreview(); selected = archivedId; }
      render(); panel.open = true; preview.open = true; el("conversation-archive-load").focus(); return read();
    }
    async function restore() {
      render(); const current = state(), data = current.snapshot;
      if (!selected || !value || blocked(current, true) || pending || data.conversation.conversations.length >= 8) return;
      const binding = { owner, selected, generation }; pending = binding; status = "正在恢复到活动列表…"; updateControls();
      try {
        await action("/conversation", { action: "restore_archive", archived_id: binding.selected });
        if (!closed && state().snapshot?.conversation_id === binding.owner) status = "已恢复到活动列表；当前输入保持，请手动选择会话。";
      } catch (error) { if (!closed && owner === binding.owner) status = errorText(error.message); }
      finally { if (pending === binding) pending = null; updateControls(); }
    }
    el("conversation-archive").addEventListener("click", () => {
      render(); const current = state(), data = current.snapshot;
      if (!available(data) || blocked(current, true) || pending || el("conversation-archive").disabled) return;
      confirmation = signature(data); const selectedItem = data.conversation.conversations.find(item => item.id === data.conversation.selected);
      el("conversation-archive-question").textContent = "归档“" + selectedItem.title + "”？完整内容和保存项保留，当前会话将移出活动列表。";
      el("conversation-archive-confirmation").hidden = false; el("conversation-archive-confirm").disabled = false;
      el("conversation-archive-confirm").focus();
    });
    el("conversation-archive-cancel").addEventListener("click", clearConfirmation);
    el("conversation-archive-confirm").addEventListener("click", async () => {
      const current = state(), data = current.snapshot;
      if (!available(data) || blocked(current, true) || !confirmation || confirmation !== signature(data) || pending) { clearConfirmation(); return; }
      clearConfirmation();
      try { await action("/conversation", { action: "archive", confirmed: true }); }
      catch (error) { status = errorText(error.message); updateControls(); }
    });
    el("conversation-archive-load").addEventListener("click", read);
    el("conversation-archive-restore").addEventListener("click", restore);
    return { render, controls(value) { unavailable = value; render(); }, close() { closed = true; selected = ""; clearConfirmation(); clearPreview(); render(); } };
  }
  window.PactTrailConversationArchiveUI = { valid, validConversation, validMetadata, handles, create };
})();
