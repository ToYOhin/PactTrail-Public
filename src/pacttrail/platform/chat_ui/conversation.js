(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const fields = (value, names) => value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
  const id = value => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  const integer = (value, max) => Number.isSafeInteger(value) && value >= 0 && value <= max;
  const bounded = (value, max) => typeof value === "string" && Array.from(value).length <= max
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\ud800-\udfff]/u.test(value);
  const bytes = value => new TextEncoder().encode(value).byteLength;
  const errors = {
    history_unavailable: "会话记录暂不可用，请核对保存目录。", history_in_use: "会话记录正由其他服务使用。",
    history_version: "这份会话记录版本暂不支持。", history_corrupt: "会话记录无法核对，不能继续发送。",
    history_limit: "已达到 8 个会话或记录大小上限，请使用新的会话目录。",
    context_limit: "本次上下文已满，请调整历史参考或切换会话。若刚发生发送拒绝，请先点击“刷新状态”核对；不会重发原请求。",
    stale_request: "摘要参考已变化，请重新核对或撤回摘要后发送。",
  };
  const enabled = data => Boolean(data && Object.hasOwn(data, "conversation"));
  const capacityFields = ["max_context_bytes", "context_bytes", "overflow_policy"];
  const configuredCapacity = data => enabled(data) && Object.hasOwn(data.conversation, "max_context_bytes");
  const turnLimit = data => data?.conversation?.max_turns ?? 12;
  const readableTurns = data => configuredCapacity(data) ? 32 : 12;
  const contextLimit = data => configuredCapacity(data) ? data.conversation.max_context_bytes : 16384;
  const contextFull = data => enabled(data) && (data.conversation.error === "context_limit"
    || configuredCapacity(data) && data.conversation.context_bytes > contextLimit(data));
  const answerLimit = data => data.limits?.max_answer_chars ?? 512;
  const inputLimit = (data, intent = "answer") => enabled(data) && intent !== "answer"
    ? 2048 : data?.limits?.max_message_chars ?? 256;
  function validMessageLimit(data) {
    const hasAnswerLimit = Object.hasOwn(data.limits, "max_answer_chars");
    if (!enabled(data)) return data.limits.max_message_chars === 256 && !hasAnswerLimit;
    return integer(data.limits.max_message_chars, 4096) && data.limits.max_message_chars > 0
      && (!hasAnswerLimit || integer(data.limits.max_answer_chars, 4096) && data.limits.max_answer_chars > 0)
      && (!Object.hasOwn(data, "messages") || Array.isArray(data.messages)
        && data.messages.every(message => message && (message.role !== "assistant" || bounded(message.text, hasAnswerLimit ? 4096 : 512))));
  }
  const messageOverflow = (data, intent) => "问题最多 " + inputLimit(data, intent) + " 个字符，请缩短后再发送。";
  function notice(data, code) {
    const limits = { turn_limit: "这段对话已达到 " + turnLimit(data) + " 次提问（失败或中断也计入），请开始新对话。若刚发生发送拒绝，请先点击“刷新状态”核对；不会重发原请求。",
      context_limit: errors.context_limit, history_limit: errors.history_limit,
      request_limit: "当前服务已达到 24 次提问上限，请结束服务后重新启动。" };
    return enabled(data) && Object.hasOwn(limits, code) ? limits[code] : null;
  }
  function valid(data) {
    if (!enabled(data)) return true;
    const value = data.conversation;
    const extended = value && capacityFields.some(name => Object.hasOwn(value, name));
    if (!["agent_demo", "agent_model"].includes(data.mode)
      || !fields(value, ["version", "persistent", "selected", "conversations", "scope", "context_turns", "max_turns", "restored", "results", "result", "error",
        ...(extended ? capacityFields : []), ...(Object.hasOwn(value, "saved_input") ? ["saved_input"] : []),
        ...(Object.hasOwn(value, "artifact_reference") ? ["artifact_reference"] : []),
        ...["history_selection", "history_candidates", "summary_plan"].filter(name => Object.hasOwn(value, name)),
        ...(Object.hasOwn(value, "archive") ? ["archive"] : [])])
      || value.version !== 1 || typeof value.persistent !== "boolean" || !id(value.selected)
      || Object.hasOwn(value, "saved_input") && (!value.persistent || !window.PactTrailSavedInputUI?.valid(value.saved_input))
      || Object.hasOwn(value, "artifact_reference") && (!value.persistent || !window.PactTrailArtifactReferenceUI?.validReference(value.artifact_reference))
      || (Object.hasOwn(value, "history_selection") || Object.hasOwn(value, "history_candidates") || Object.hasOwn(value, "summary_plan"))
        && (!value.persistent || window.PactTrailHistoryReferenceUI?.validMetadata(value) !== true)
      || (extended ? !integer(value.max_turns, 32) || value.max_turns < 1
        || !integer(value.max_context_bytes, 65536) || value.max_context_bytes < 1024
        || !integer(value.context_bytes, 1048576) || value.overflow_policy !== "reject" : value.max_turns !== 12)
      || !Array.isArray(value.conversations) || value.conversations.length < 1 || value.conversations.length > 8
      || !value.conversations.every(item => fields(item, ["id", "title", "turns"]) && id(item.id)
        && bounded(item.title, 80) && integer(item.turns, readableTurns(data)))
      || new Set(value.conversations.map(item => item.id)).size !== value.conversations.length
      || Object.hasOwn(value, "archive") && !window.PactTrailConversationArchiveUI?.validMetadata(value.archive, value.conversations)
      || !value.conversations.some(item => item.id === value.selected)
      || !integer(value.context_turns, readableTurns(data)) || typeof value.restored !== "boolean"
      || !(value.error === null || typeof value.error === "string" && Object.hasOwn(errors, value.error))
      || value.error === "stale_request" && value.summary_plan?.state !== "stale"
      || data.followup && data.followup.selected || data.material
      || data.project && (data.project.version !== 1 || !window.PactTrailTaskUI.validProject(data.project, true))) return false;
    if (value.context_turns > value.conversations.find(item => item.id === value.selected).turns) return false;
    const scope = value.scope, attached = data.project;
    if (!fields(scope, ["state", "name", "sha256", "can_refresh", "can_confirm"])
      || !["empty", "confirmed", "review_required"].includes(scope.state)
      || typeof scope.can_refresh !== "boolean" || typeof scope.can_confirm !== "boolean"
      || (scope.state === "empty" ? scope.name !== null || scope.sha256 !== null || Boolean(attached) || scope.can_confirm
        : !bounded(scope.name, 80) || !scope.name.length || !hash(scope.sha256))
      || scope.state === "confirmed" && (!attached || scope.sha256 !== attached.sha256 || scope.name !== attached.name || scope.can_confirm)
      || scope.can_confirm && (scope.state !== "review_required" || !data.project || scope.sha256 !== data.project.sha256)) return false;
    if (!Array.isArray(value.results) || value.results.length > readableTurns(data)
      || !value.results.every(item => fields(item, ["run_id", "title", "kind"]) && id(item.run_id)
        && bounded(item.title, 256) && ["report", "change_draft"].includes(item.kind))
      || new Set(value.results.map(item => item.run_id)).size !== value.results.length) return false;
    const result = value.result;
    if (result === null) return true;
    if (!fields(result, ["run_id", "artifact", "application", "source"])) return false;
    const summary = value.results.find(item => item.run_id === result.run_id), artifact = result.artifact;
    const draft = summary && summary.kind === "change_draft"
      ? window.PactTrailDraftUI.parse(artifact, result.run_id, result.source) : null;
    return Boolean(summary)
      && (result.source === null || window.PactTrailTaskUI.validSource(result.source))
      && fields(artifact, ["name", "sha256", "bytes", "text"])
      && artifact.name === (summary.kind === "change_draft" ? "draft-" + result.run_id + ".json" : "report-" + result.run_id + ".md")
      && hash(artifact.sha256) && integer(artifact.bytes, 8192) && artifact.bytes > 0
      && bounded(artifact.text, 8192) && bytes(artifact.text) === artifact.bytes
      && (summary.kind !== "change_draft" || Boolean(draft))
      && (result.application === null || summary.kind === "change_draft" && window.PactTrailDraftUI.validEditing(result.application, true)
        && result.application.run_id === result.run_id && result.application.artifact_sha256 === artifact.sha256
        && result.application.files.length === draft.changes.length && result.application.files.every((file, index) =>
          file.path === draft.changes[index].path && file.sha256 === draft.changes[index].new_sha256));
  }
  function scopeReady(data) {
    return !enabled(data) || valid(data) && data.conversation.error === null
      && !contextFull(data)
      && data.conversation.scope.state !== "review_required"
      && data.conversation.conversations.find(item => item.id === data.conversation.selected).turns < turnLimit(data);
  }

  function exportable(data) {
    return enabled(data) && valid(data) && !data.busy && data.operation?.status !== "running"
      && Number.isSafeInteger(data.revision) && data.revision >= 0 && data.conversation_id === data.conversation.selected
      && Array.isArray(data.messages) && data.messages.length > 0 && data.messages.length <= 2 * readableTurns(data)
      && data.messages.every(message => message && ["user", "assistant"].includes(message.role)
        && ["complete", "stopped", "error"].includes(message.status) && id(message.operation_id)
        && (!Object.hasOwn(message, "web_search") || window.PactTrailWebSearchUI?.validMessage(message))
        && bounded(message.text, 4096) && Array.isArray(message.sources) && message.sources.length <= 3
        && message.sources.every(source => source && bounded(source.source, 256)
          && integer(source.line_start, 100000) && source.line_start > 0
          && integer(source.line_end, 100000) && source.line_end >= source.line_start && bounded(source.text, 32768)));
  }
  function exportText(data) {
    if (!exportable(data)) return null;
    const selected = data.conversation.conversations.find(item => item.id === data.conversation.selected);
    const indent = value => value.replace(/\r\n?/g, "\n").split("\n").map(line => "  " + line).join("\n");
    const statuses = { complete: "已完成", stopped: "已停止", error: "失败" };
    const lines = ["PactTrail 当前会话文本", "会话 ID：" + selected.id, "名称：", indent(selected.title),
      "范围：当前会话已加载的可见消息和来源；不附加成果文件正文或内部执行记录。", "正文与引文按行缩进，状态字段由消息记录提供。"];
    data.messages.forEach((message, index) => {
      lines.push("", "消息 " + (index + 1), "角色：" + (message.role === "user" ? "用户" : "助手"),
        "状态：" + statuses[message.status], "正文：", indent(message.text));
      for (const source of message.sources) lines.push("来源：", indent(source.source),
        "行号：" + source.line_start + "–" + source.line_end, "引文：", indent(source.text));
      if (message.web_search) {
        const search = message.web_search;
        lines.push("联网搜索：", "检索词：", indent(search.query), "检索时间：" + search.searched_at,
          "搜索服务：" + window.PactTrailWebSearchUI.providerLabel(search.provider) + " · " + (search.transport === "synthetic" ? "合成演示" : "公开联网"),
          "证据范围：" + (search.results.some(result => result.page) ? "已读取网页选段，非全文。" : "搜索摘要，未读取全文。"));
        if (search.version === 2) lines.push("实际查询：", ...search.queries.map(indent), "限定域名：", ...search.official_domains.map(indent));
        for (const result of search.results) {
          lines.push("网页来源：", indent(result.title), "URL：", indent(result.url), "搜索摘要：", indent(result.snippet));
          if (result.page) lines.push("已读取网页选段：", "最终URL：", indent(result.page.url),
            "获取时间：" + result.page.fetched_at, "SHA256：" + result.page.sha256, indent(result.page.text));
        }
      }
    });
    return lines.join("\n") + "\n";
  }
  function create({ state, action, request, updateControls }) {
    const archiveUI = window.PactTrailConversationArchiveUI?.create({ state, action, request, updateControls });
    let unavailable = false, generation = 0, resultKey = "", url = null, artifact = null, recovering = false;
    let titleOwner = "", originalTitle = "";
    let exportOwner = "", deleteOwner = "";
    const exportUrls = new Set();
    const exportIdentity = data => data ? data.conversation_id + ":" + data.revision : "";
    function clearExports() { for (const value of exportUrls) URL.revokeObjectURL(value); exportUrls.clear(); }
    const renameTitle = () => typeof el("conversation-name").value === "string" ? el("conversation-name").value.trim() : "";
    const validTitle = () => bounded(renameTitle(), 80) && renameTitle().length > 0
      && !/[\u0000-\u001f\u007f]/u.test(el("conversation-name").value);
    const blocked = () => { const current = state(); return unavailable || current.locked || current.mutating
      || !enabled(current.snapshot) || !valid(current.snapshot) || current.snapshot.busy
      || ![null, "context_limit"].includes(current.snapshot.conversation.error); };
    function canRecover(data) {
      const result = data?.conversation?.result;
      return Boolean(!recovering && !blocked() && Object.hasOwn(data, "recovered_draft") && artifact && result && data.operation === null
        && data.task?.run_id === null && !data.task?.artifact && data.editing
        && data.conversation.scope.state === "confirmed" && data.conversation.scope.sha256 === data.project?.sha256
        && artifact.name === "draft-" + result.run_id + ".json" && artifact.sha256 === result.artifact.sha256
        && window.PactTrailDraftUI.localSource(result.source) && result.source.sha256 === data.project?.sha256);
    }
    function clearDelete() {
      deleteOwner = ""; el("conversation-delete-confirmation").hidden = true;
      el("conversation-delete-confirm").disabled = true;
    }
    function clearResult() {
      generation += 1;
      if (url) URL.revokeObjectURL(url);
      url = null; artifact = null;
      el("conversation-result-text").textContent = "";
      el("conversation-result-text").hidden = true;
      el("conversation-result-status").textContent = "";
      el("conversation-result-open").disabled = true;
      el("conversation-result-download").disabled = true;
      el("conversation-result-review").hidden = true; el("conversation-result-review").disabled = true;
      window.PactTrailDraftUI.clear("conversation-result-draft");
      window.PactTrailDraftUI.renderApplication(null, "conversation-result-application");
    }
    function controls(value) {
      unavailable = value;
      archiveUI?.controls(value);
      const data = state().snapshot, active = enabled(data), disabled = blocked();
      const scope = active && valid(data) && data.conversation.scope;
      const selected = scope && data.conversation.conversations.find(item => item.id === data.conversation.selected);
      const canDelete = !disabled && selected && data.conversation_id === selected.id
        && Number.isSafeInteger(data.revision) && data.revision >= 0
        && data.operation?.status !== "running" && exportOwner === exportIdentity(data);
      el("conversation-delete").disabled = !canDelete;
      if (!canDelete || deleteOwner !== exportIdentity(data)) clearDelete();
      el("conversation-delete-confirm").disabled = !canDelete || !deleteOwner;
      el("conversation-delete-note").textContent = selected
        ? data.conversation.conversations.length + " / 8 段会话。删除仅移除当前对话，保留任务历史、成果和累计额度。"
        : "只可删除已核对的当前会话。";
      el("conversation-export").disabled = disabled || exportOwner !== exportIdentity(data) || !exportable(data);
      el("conversation-export-note").textContent = data && data.busy ? "会话处理中，完成或停止后可导出。"
        : "只导出已显示消息和来源，不附加成果文件。";
      const canRename = Boolean(selected && selected.turns > 0 && titleOwner === selected.id && originalTitle === selected.title);
      el("conversation-title-form").hidden = !canRename;
      el("conversation-name").disabled = disabled || !canRename;
      el("conversation-rename").disabled = disabled || !canRename || !validTitle() || renameTitle() === selected.title;
      el("conversation-name-note").textContent = validTitle() ? "只改名称，最多80字。" : "请输入1–80字的名称，不含换行或控制字符。";
      for (const name of ["conversation-list", "conversation-results"]) {
        for (const child of el(name).children) child.disabled = disabled;
      }
      el("conversation-refresh-files").disabled = disabled || !scope || !["confirmed", "review_required"].includes(scope.state) || !scope.can_refresh;
      el("conversation-confirm-scope").disabled = disabled || !scope || !scope.can_confirm;
      el("conversation-result-open").disabled = disabled || !url;
      el("conversation-result-download").disabled = disabled || !url;
      const draftResult = Boolean(data?.conversation?.result?.artifact?.name?.startsWith("draft-"));
      el("conversation-result-review").hidden = !draftResult || !data || !Object.hasOwn(data, "recovered_draft");
      el("conversation-result-review").disabled = !canRecover(data);
      if (active) {
        if (selected) el("input-note").textContent = contextFull(data) ? errors.context_limit
          : selected.turns >= turnLimit(data) ? "本段已达到 " + turnLimit(data) + " 次提问，请开始新对话或切换其他会话。"
            : "Enter 发送 · Shift + Enter 换行 · 问题最多 " + inputLimit(data, el("task-intent").value)
              + " 字 · 完整上下文最多 " + contextLimit(data) / 1024 + " KiB，满额时"
              + (configuredCapacity(data) ? "保留全文并拒绝新提问" : "保留历史并开始新对话");
        el("task-material-consent").hidden = true;
        el("task-use-material").checked = false;
        el("task-use-material").disabled = true;
        for (const name of ["local-material", "web-panel", "followup-panel", "task-followup-consent"]) el(name).hidden = true;
      }
    }
    async function verify(result, token) {
      try {
        const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(result.artifact.text));
        const actual = Array.from(new Uint8Array(digest), item => item.toString(16).padStart(2, "0")).join("");
        if (token !== generation) return;
        if (actual !== result.artifact.sha256) throw new Error("digest");
        const draft = result.artifact.name.startsWith("draft-")
          ? await window.PactTrailDraftUI.verify(result.artifact, result.run_id, result.source) : null;
        if (token !== generation) return;
        if (result.artifact.name.startsWith("draft-") && !draft) throw new Error("draft");
        artifact = result.artifact;
        url = URL.createObjectURL(new Blob([artifact.text], { type: "text/plain;charset=utf-8" }));
        el("conversation-result-status").textContent = artifact.name + " · 已核对保存内容";
        if (draft) window.PactTrailDraftUI.render(draft, "conversation-result-draft");
        else { el("conversation-result-text").textContent = artifact.text; el("conversation-result-text").hidden = false; }
        window.PactTrailDraftUI.renderApplication(result.application, "conversation-result-application");
      } catch (_) {
        if (token !== generation) return;
        el("conversation-result-status").textContent = "保存内容未通过校验，不能打开或下载。";
      }
      controls(unavailable); updateControls();
    }
    function render(data) {
      archiveUI?.render();
      const active = enabled(data);
      for (const name of ["conversation-navigation", "conversation-scope", "conversation-results-panel"]) el(name).hidden = !active;
      if (!active) { clearResult(); clearDelete(); resultKey = ""; titleOwner = originalTitle = exportOwner = "";
        el("conversation-name").value = ""; el("conversation-title-form").hidden = true;
        el("conversation-scope-actions").hidden = true; return; }
      if (!valid(data)) {
        clearResult(); resultKey = "";
        el("conversation-scope").textContent = "会话状态无法核对，不能发送。";
        controls(unavailable); return;
      }
      const value = data.conversation, scope = value.scope;
      exportOwner = exportIdentity(data);
      el("message").removeAttribute?.("maxlength");
      el("web-fetch").disabled = true;
      el("material-file").disabled = true;
      el("conversation-list").replaceChildren(...value.conversations.map(item => {
        const node = document.createElement("button"); node.type = "button"; node.dataset.conversationId = item.id;
        node.textContent = item.title || "新对话"; node.setAttribute("aria-current", item.id === value.selected ? "page" : "false");
        node.addEventListener("click", () => { if (!blocked() && state().snapshot.conversation.conversations.some(entry => entry.id === item.id))
          return action("/conversation", { action: "select", selected: item.id }); });
        return node;
      }));
      const selected = value.conversations.find(item => item.id === value.selected);
      if (titleOwner !== selected.id || originalTitle !== selected.title) {
        titleOwner = selected.id; originalTitle = selected.title; el("conversation-name").value = selected.title;
      }
      el("conversation-title").textContent = selected.title || "新的对话";
      document.title = "PactTrail · 对话";
      el("workspace-caption").textContent = "对话";
      el("workspace-eyebrow").textContent = "对话";
      el("sidebar-conversation").textContent = selected.title || "新的对话";
      el("memory-caption").textContent = value.persistent ? "保存在本机 · 可继续这段对话" : "当前服务内的对话";
      el("session-location").textContent = value.persistent ? "指定的本机会话目录" : "本机进程内存";
      el("session-turns").textContent = selected.turns + " / " + value.max_turns;
      el("turn-count").textContent = "已完成上下文 " + value.context_turns + " 轮 · 每段最多 " + value.max_turns
        + " 次提问（失败或中断也计入）" + (configuredCapacity(data)
          ? " · 完整上下文 " + value.context_bytes + " / " + value.max_context_bytes + " 字节（UTF-8）；满额拒绝新提问，保留全文。" : "");
      if ((!data.task || data.task.error !== "state_unavailable") && (!data.operation || data.operation.error !== "state_unavailable")) {
        el("storage-note-title").textContent = "会话保存在本机";
        el("storage-note").textContent = "保存提问、可见回答与项目快照；重开后可查看并继续。不会自动重跑旧请求。";
      }
      el("storage-limit-note").textContent = "新对话保留已有会话。模型原始消息和凭据不保存。" + (configuredCapacity(data)
        ? "每次启动最多 24 次提问；超过 24 次需重开后继续，本次启动的调用与费用额度仍保持原有限制。" : "");
      el("session-boundary-note").textContent = "继续提问会参考本段对话和已关联文件；应用修改仍需单独确认。";
      el("welcome-description").textContent = "从一个想法开始。";
      el("answer-mode-note").textContent = (data.mode === "agent_demo" ? "当前为离线模拟回复，不代表真实 AI 问答。" : "")
        + "普通回答最多 " + answerLimit(data) + " 字；保存报告或修改文件的问题最多 2048 字。后续提问沿用本段对话和已关联文件。需要修改文件时，先查看改动再确认应用。";
      el("source-footnote").textContent = "仅使用已关联的项目快照；保存成果不代表已应用修改或通过测试。";
      if (data.task && !data.task.source) el("task-source").textContent = "本轮未关联项目文件。";
      el("message").placeholder = "输入问题，或接着刚才的回答继续…";
      el("conversation-scope").textContent = value.error || contextFull(data) ? errors[value.error || "context_limit"] : (value.restored ? "已恢复这段对话。" : "")
        + (scope.state === "review_required" ? scope.can_refresh
          ? scope.can_confirm ? "已读取最新文件，请在右侧核对并确认范围。" : "项目文件需要核对，请在右侧核对最新文件后继续。"
          : "项目范围需要核对，请重新选择文件。"
          : scope.state === "confirmed" ? "当前项目：" + scope.name + " · 后续提问沿用已选文件。" : "可直接提问，也可以关联项目文件。")
        + (selected.turns >= turnLimit(data) ? "已达到 " + turnLimit(data) + " 次提问，请开始新对话或切换其他会话。" : "");
      el("conversation-scope-actions").hidden = !["confirmed", "review_required"].includes(scope.state);
      el("conversation-results").replaceChildren(...value.results.map(item => {
        const node = document.createElement("button"); node.type = "button"; node.dataset.conversationRunId = item.run_id;
        node.textContent = (item.kind === "change_draft" ? "改动 · " : "报告 · ") + item.title;
        node.addEventListener("click", () => { if (!blocked() && state().snapshot.conversation.results.some(entry => entry.run_id === item.run_id))
          return action("/conversation", { action: "show", run_id: item.run_id }); });
        return node;
      }));
      const key = JSON.stringify([data.conversation_id, data.revision, value.selected, value.result]);
      if (key !== resultKey) {
        clearResult(); resultKey = key;
        if (value.result) { el("conversation-result-status").textContent = "正在核对保存内容…"; void verify(value.result, generation); }
      }
      controls(unavailable);
    }
    el("conversation-delete").addEventListener("click", () => {
      controls(unavailable);
      if (el("conversation-delete").disabled) return;
      const data = state().snapshot;
      deleteOwner = exportIdentity(data);
      const selected = data.conversation.conversations.find(item => item.id === data.conversation.selected);
      el("conversation-delete-question").textContent = "删除“" + selected.title
        + "”的对话与消息？此操作不可撤销；任务历史、成果文件及已下载文件仍保留，累计额度不恢复。";
      el("conversation-delete-confirmation").hidden = false;
      el("conversation-delete-confirm").disabled = false;
      el("conversation-delete-cancel").focus?.();
    });
    el("conversation-delete-cancel").addEventListener("click", () => {
      clearDelete(); el("conversation-delete").focus?.();
    });
    el("conversation-delete-confirm").addEventListener("click", () => {
      controls(unavailable);
      if (el("conversation-delete-confirm").disabled) return;
      clearDelete();
      return action("/conversation", { action: "delete", confirmed: true });
    });
    el("conversation-export").addEventListener("click", () => {
      controls(unavailable);
      if (el("conversation-export").disabled) return;
      const data = state().snapshot, text = exportText(data);
      if (text === null) return;
      let value = null, link = null;
      try {
        value = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
        exportUrls.add(value); link = document.createElement("a"); link.href = value;
        link.download = "pacttrail-conversation-" + data.conversation.selected + ".txt";
        link.hidden = true; document.body.appendChild(link); link.click();
        el("conversation-export-note").textContent = "已生成当前会话文本下载。";
      } catch (_) { el("conversation-export-note").textContent = "未能生成文本下载，请稍后再试。"; }
      finally {
        if (link) link.remove();
        if (value) setTimeout(() => { if (exportUrls.delete(value)) URL.revokeObjectURL(value); }, 1000);
      }
    });
    el("conversation-name").addEventListener("input", () => controls(unavailable));
    el("conversation-title-form").addEventListener("submit", event => {
      event.preventDefault(); controls(unavailable);
      if (!el("conversation-rename").disabled) return action("/conversation", { action: "rename", title: renameTitle() });
    });
    el("conversation-refresh-files").addEventListener("click", () => {
      controls(unavailable);
      if (!el("conversation-refresh-files").disabled) return action("/conversation", { action: "refresh" });
    });
    el("conversation-confirm-scope").addEventListener("click", () => {
      controls(unavailable);
      if (!el("conversation-confirm-scope").disabled) return action("/conversation", {
        action: "confirm", project_sha256: state().snapshot.project.sha256, confirmed: true,
      });
    });
    el("conversation-result-review").addEventListener("click", async () => {
      controls(unavailable);
      const data = state().snapshot;
      if (!canRecover(data)) return;
      const result = data.conversation.result, runId = result.run_id, sha = result.artifact.sha256;
      const current = data.recovered_draft;
      if (current?.run_id === runId && current.artifact.sha256 === sha) {
        window.PactTrailDraftUI.requestReview(runId, sha); return;
      }
      recovering = true; controls(unavailable);
      try {
        await action("/conversation", { action: "recover_draft", run_id: runId, artifact_sha256: sha,
          conversation_id: data.conversation_id, revision: data.revision });
        const next = state().snapshot;
        if (next?.conversation_id === data.conversation_id && next.recovered_draft?.run_id === runId
          && next.recovered_draft.artifact.sha256 === sha) window.PactTrailDraftUI.requestReview(runId, sha);
      } finally { recovering = false; controls(unavailable); updateControls(); }
    });
    function open(download) {
      if (blocked() || !url || !artifact) return;
      const link = document.createElement("a"); link.href = url;
      if (download) link.download = artifact.name; else { link.target = "_blank"; link.rel = "noopener noreferrer"; }
      link.hidden = true;
      try { document.body.appendChild(link); link.click(); } finally { link.remove(); }
    }
    el("conversation-result-open").addEventListener("click", () => open(false));
    el("conversation-result-download").addEventListener("click", () => open(true));
    return { render, controls, ready: () => scopeReady(state().snapshot), close: () => { archiveUI?.close(); clearResult(); clearExports(); clearDelete(); exportOwner = ""; resultKey = ""; } };
  }
  function createComposerDrafts({ readTaskDraft = () => ({ intent: "answer", creationPath: "" }),
    restoreTaskDraft = () => {} } = {}) {
    const input = el("message"), drafts = new Map(), pending = new Map();
    let owner = "", version = 0;
    function capture(edited = false) {
      if (!owner) return null;
      const task = readTaskDraft();
      let draft = drafts.get(owner);
      if (!draft || edited || draft.text !== input.value || draft.intent !== task.intent
          || draft.creationPath !== task.creationPath) {
        draft = { text: input.value, intent: task.intent, creationPath: task.creationPath,
          version: ++version }; drafts.set(owner, draft);
      }
      return draft;
    }
    function close() {
      const managed = Boolean(owner);
      if (managed) input.value = "";
      owner = ""; drafts.clear(); pending.clear();
      if (managed) restoreTaskDraft({ intent: "answer", creationPath: "" }, null);
      el("composer-draft-note").hidden = true;
    }
    function render(data) {
      if (!enabled(data) || !valid(data) || data.conversation_id !== data.conversation.selected
          || !Array.isArray(data.messages)) { close(); return false; }
      capture();
      const ids = new Set([...data.conversation.conversations, ...(data.conversation.archive?.conversations || [])].map(item => item.id));
      for (const key of drafts.keys()) if (!ids.has(key)) drafts.delete(key);
      for (const key of pending.keys()) if (!ids.has(key)) pending.delete(key);
      const selected = data.conversation.selected, submitted = pending.get(selected);
      if (submitted && data.messages.some(message => message.role === "user"
          && message.operation_id === submitted.requestId && message.text === submitted.text)) {
        const draft = drafts.get(selected);
        if (draft && draft.version === submitted.version && draft.text === submitted.text
            && draft.intent === submitted.intent && draft.creationPath === submitted.creationPath)
          drafts.set(selected, { ...draft, text: "", version: ++version });
        pending.delete(selected);
      }
      const changed = owner !== selected;
      owner = selected;
      let draft = drafts.get(owner);
      if (!draft) {
        draft = { text: "", intent: "answer", creationPath: "", version: ++version };
        drafts.set(owner, draft);
      }
      if (input.value !== draft.text) input.value = draft.text;
      // Restore editing fields only when entering their owner. Capability and
      // approval checks belong to the task UI and are repeated against data.
      if (changed) restoreTaskDraft({ intent: draft.intent, creationPath: draft.creationPath }, data);
      el("composer-draft-note").hidden = false;
      return true;
    }
    function submitted({ conversation_id, request_id, message }) {
      const draft = capture();
      if (owner === conversation_id && id(request_id) && draft?.text === message)
        pending.set(owner, { ...draft, requestId: request_id });
    }
    input.addEventListener("input", () => capture(true));
    return { capture, render, submitted, active: () => Boolean(owner), close };
  }
  function createMessages({ state }) {
    const viewport = el("conversation-scroll"), list = el("messages");
    let scope = "", stamp = "", entries = new Map();
    let painted = "", unavailable = false, pending = null;
    const identity = data => JSON.stringify([data.mode, data.conversation_id, data.revision]);
    const node = (tag, className, value = "") => {
      const result = document.createElement(tag); result.className = className; result.textContent = value; return result;
    };
    const reconcile = (parent, children) => {
      children.forEach((child, index) => {
        if (parent.children[index] !== child) parent.insertBefore(child, parent.children[index] || null);
      });
      while (parent.children.length > children.length) parent.children[parent.children.length - 1].remove();
    };
    const key = (value, counts) => {
      const index = counts.get(value) || 0; counts.set(value, index + 1); return value + ":" + index;
    };
    function renderBody(body, text, formatted, options) {
      if (!formatted) { body.textContent = text; return { codeBlocks: [], citations: [] }; }
      if (window.PactTrailMessageFormat) return window.PactTrailMessageFormat.render(body, text, options);
      const children = [];
      const paragraphs = value => {
        for (const part of value.split(/(\r?\n[\t ]*\r?\n)/)) {
          if (!part) continue;
          children.push(/^[\r\n\t ]+$/.test(part) ? document.createTextNode(part)
            : node("p", "message-paragraph", part));
        }
      };
      // Only fenced code and paragraphs are interpreted. Model-provided HTML,
      // links and inline markup remain inert text; the original stays on entry.
      const fences = /(^|\n)[\t ]*```[a-zA-Z0-9_+.#-]*[^\S\n]*\r?\n([\s\S]*?)\r?\n[\t ]*```[\t ]*(?=\r?\n|$)/g;
      let cursor = 0, match;
      while ((match = fences.exec(text))) {
        paragraphs(text.slice(cursor, match.index));
        const pre = node("pre", "message-code"); pre.appendChild(node("code", "", match[2]));
        children.push(pre); cursor = fences.lastIndex;
      }
      paragraphs(text.slice(cursor)); body.replaceChildren(...children);
      return { codeBlocks: [], citations: [] };
    }
    function setCollapsed(entry, value) {
      entry.collapsed = value; entry.body.hidden = value; entry.list.hidden = value;
      if (entry.webSearch) entry.webSearch.hidden = value;
      entry.preview.hidden = !value;
      if (entry.actions) entry.actions.hidden = value;
      entry.toggle.textContent = value ? "展开回答" : "收起回答";
      entry.toggle.setAttribute("aria-expanded", String(!value));
    }
    function capture() {
      return { top: viewport.scrollTop, follow: viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop <= 48 };
    }
    function copyState() {
      const current = state(), data = current.snapshot;
      return !unavailable && !current.locked && !current.mutating && data && enabled(data) && valid(data)
        && data.conversation_id === data.conversation.selected && identity(data) === painted
        && Array.isArray(data.messages) ? data : null;
    }
    function owns(entry, data, binding = null, block = null) {
      const message = data?.messages[entry.index];
      return Boolean(message && entry.copyEligible && entries.get(entry.key) === entry
        && entry.actions?.parentNode === entry.article && message.role === "assistant" && message.status === "complete"
        && message.operation_id === entry.runId && message.text === entry.text
        && (!block || entry.codeBlocks?.includes(block))
        && (!binding || (block || entry).binding === binding && binding.owner === painted && binding.entry === entry
          && binding.text === entry.text && binding.block === block && binding.payload === (block ? block.text : entry.text)));
    }
    function feedback(entry, text, binding = null, block = null) {
      const target = block || entry;
      if (target.feedback.textContent !== text) {
        const reading = capture(); target.feedback.textContent = text; restore(reading);
      }
      target.binding = binding;
    }
    function controls(value) {
      unavailable = value;
      const data = copyState();
      for (const entry of entries.values()) {
        const eligible = owns(entry, data);
        if (entry.button) entry.button.disabled = Boolean(pending) || !eligible;
        if (entry.binding && !owns(entry, data, entry.binding)) feedback(entry, "");
        for (const block of entry.codeBlocks || []) {
          block.button.disabled = Boolean(pending) || !eligible;
          if (block.binding && !owns(entry, data, block.binding, block)) feedback(entry, "", null, block);
        }
        for (const citation of entry.citations || []) citation.button.disabled = !eligible;
      }
    }
    async function copy(entry, block = null) {
      controls(unavailable);
      if (pending || (block || entry).button.disabled || !owns(entry, copyState(), null, block)) return;
      const binding = { owner: painted, entry, text: entry.text, block, payload: block ? block.text : entry.text };
      pending = binding; feedback(entry, "正在复制…", binding, block); controls(unavailable);
      try {
        if (typeof navigator === "undefined" || !navigator.clipboard
            || typeof navigator.clipboard.writeText !== "function") throw new Error("clipboard_unavailable");
        await navigator.clipboard.writeText(binding.payload);
        if (owns(entry, copyState(), binding, block)) feedback(entry, "已复制。", binding, block);
      } catch (_) {
        if (owns(entry, copyState(), binding, block)) feedback(entry, "未能复制，请手动选择正文。", binding, block);
      } finally {
        if (pending === binding) pending = null;
        controls(unavailable);
      }
    }
    function openCitation(entry, index) {
      const data = copyState(), search = data?.messages[entry.index]?.web_search;
      if (!owns(entry, data) || entry.collapsed || !entry.webSearch || entry.searchStamp !== JSON.stringify(search ?? null)
        || !window.PactTrailWebSearchUI?.validSearch(search)
        || !Number.isInteger(index) || index < 1 || index > search.results.length) return;
      const content = entry.webSearch.children[1];
      const results = Array.from(content?.children || []).find(value => value.className === "web-search-result-list");
      const target = results?.children[index - 1];
      if (!target) return;
      entry.webSearch.open = true; target.setAttribute("tabindex", "-1");
      target.scrollIntoView?.({ block: "nearest" }); target.focus?.({ preventScroll: true });
    }
    function render(data, label, reading) {
      painted = identity(data);
      const nextScope = JSON.stringify([data.conversation_id, data.mode]), changed = nextScope !== scope;
      if (changed) { scope = nextScope; stamp = ""; entries = new Map(); list.replaceChildren(); }
      const nextStamp = JSON.stringify(data.messages);
      if (nextStamp !== stamp) {
        const counts = new Map(), next = new Map();
        for (const [index, message] of data.messages.entries()) {
          const messageKey = key(JSON.stringify([message.operation_id, message.role]), counts);
          let entry = entries.get(messageKey);
          if (!entry) {
            entry = { article: node("article", "message message-" + message.role),
              heading: node("div", "message-heading"), label: node("div", "message-label"),
              body: node("div", "message-text"), preview: node("p", "message-preview"), collapsed: false,
              status: node("p", "message-status"), list: node("div", "source-list"), sources: new Map() };
            entry.preview.hidden = true; entry.heading.appendChild(entry.label);
            entry.article.appendChild(entry.heading); entry.article.appendChild(entry.body);
            entry.article.appendChild(entry.preview); entry.article.appendChild(entry.status);
            if (message.role === "assistant") {
              entry.toggle = node("button", "text-button message-toggle", "收起回答"); entry.toggle.type = "button";
              const bodyId = "answer-" + message.operation_id + "-" + index;
              entry.body.setAttribute("id", bodyId); entry.toggle.setAttribute("aria-controls", bodyId);
              entry.toggle.setAttribute("aria-expanded", "true"); entry.heading.appendChild(entry.toggle);
              entry.toggle.addEventListener("click", () => {
                if (entries.get(entry.key) !== entry) return;
                const currentReading = capture(); setCollapsed(entry, !entry.collapsed); restore(currentReading);
              });
            }
          }
          const heading = message.role === "user" ? "你" : label;
          if (entry.label.textContent !== heading) entry.label.textContent = heading;
          const searchStamp = JSON.stringify(message.web_search ?? null);
          if (entry.text !== message.text || entry.searchStamp !== searchStamp) {
            const trusted = message.role === "assistant" && message.status === "complete" && message.sources.length === 0
              && window.PactTrailWebSearchUI?.validSearch(message.web_search);
            Object.assign(entry, renderBody(entry.body, message.text, message.role === "assistant", {
              citationCount: trusted ? message.web_search.results.length : 0,
              onCodeCopy: block => copy(entry, block), onCitation: index => openCitation(entry, index),
            }));
            const summary = Array.from(message.text.replace(/\s+/gu, " ").trim());
            entry.preview.textContent = summary.slice(0, 64).join("") + (summary.length > 64 ? "…" : "");
          }
          Object.assign(entry, { key: messageKey, index, runId: message.operation_id, text: message.text,
            copyEligible: enabled(data) && message.role === "assistant" && message.status === "complete" && message.text.length > 0 });
          if (entry.copyEligible) {
            if (!entry.actions) {
              entry.actions = node("div", "message-actions");
              entry.button = node("button", "text-button message-copy", "复制完整正文"); entry.button.type = "button";
              entry.feedback = node("span", "message-copy-feedback"); entry.feedback.setAttribute("role", "status");
              entry.actions.appendChild(entry.button); entry.actions.appendChild(entry.feedback);
              entry.button.addEventListener("click", () => copy(entry));
            }
            if (entry.actions.parentNode !== entry.article) entry.article.insertBefore(entry.actions, entry.status);
          } else entry.actions?.remove();
          const status = message.status === "complete" ? "" : message.status === "stopped" ? "已停止 · 本轮不会继续加入回答" : "本轮未完成";
          if (entry.status.textContent !== status) entry.status.textContent = status;
          entry.status.hidden = !status;
          const sourceCounts = new Map(), sources = new Map();
          for (const source of message.sources) {
            const sourceKey = key(JSON.stringify([source.source, source.line_start, source.line_end, source.text]), sourceCounts);
            let details = entry.sources.get(sourceKey);
            if (!details) {
              details = node("details", "source");
              details.appendChild(node("summary", "", source.source + " · 第 " + source.line_start
                + (source.line_end === source.line_start ? "" : "–" + source.line_end) + " 行"));
              details.appendChild(node("blockquote", "", source.text));
            }
            sources.set(sourceKey, details);
          }
          reconcile(entry.list, [...sources.values()]); entry.sources = sources;
          if (sources.size) { if (entry.list.parentNode !== entry.article) entry.article.appendChild(entry.list); }
          else entry.list.remove();
          if (entry.searchStamp !== searchStamp) {
            entry.webSearch?.remove(); entry.webSearch = message.web_search
              ? window.PactTrailWebSearchUI?.renderSearch(message.web_search) : null;
            entry.searchStamp = searchStamp;
          }
          if (entry.webSearch && entry.webSearch.parentNode !== entry.article) entry.article.appendChild(entry.webSearch);
          if (entry.toggle) setCollapsed(entry, entry.collapsed);
          next.set(messageKey, entry);
        }
        reconcile(list, [...next.values()].map(entry => entry.article)); entries = next; stamp = nextStamp;
      }
      controls(unavailable);
      return changed ? { top: 0, follow: data.messages.length > 0 } : reading;
    }
    function follow() { viewport.scrollTop = viewport.scrollHeight; }
    function restore(reading) { if (reading.follow) follow(); else viewport.scrollTop = reading.top; }
    function locate(reference) {
      const data = copyState();
      if (!data || reference.kind !== "history" || reference.conversation_id !== data.conversation_id
          || reference.revision !== data.revision) return false;
      const entry = Array.from(entries.values()).find(item => item.runId === reference.run_id && item.copyEligible);
      if (!entry || !owns(entry, data)) return false;
      setCollapsed(entry, false);
      entry.article.setAttribute("tabindex", "-1");
      entry.article.scrollIntoView?.({ block: "start" }); entry.article.focus?.({ preventScroll: true });
      return true;
    }
    return { capture, render, restore, follow, controls, locate, close: () => { painted = ""; controls(true); } };
  }
  window.PactTrailConversationUI = { enabled, configuredCapacity, valid, scopeReady, create, notice, validMessageLimit, inputLimit, messageOverflow, createMessages, createComposerDrafts, exportText };
})();
