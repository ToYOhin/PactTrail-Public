(() => {
  "use strict";

  const bootstrap = document.querySelector('meta[name="pacttrail-session"]');
  const capability = bootstrap ? bootstrap.content : "";
  if (bootstrap) bootstrap.remove();
  const el = (id) => document.getElementById(id);
  const text = (id, value) => { el(id).textContent = value; };
  const show = (id, visible) => { el(id).hidden = !visible; };
  const connectionLabel = window.PactTrailModelConnectionUI.label;
  const providers = ["offline", "openai", "anthropic", "gemini", "deepseek", "qwen", "kimi", "glm"];
  const messages = {
    invalid_request: "请求未被接受。请刷新状态，再检查当前问题。",
    access_denied: "本地会话验证失败。请从当前服务地址重新打开页面。",
    sample_only: "当前是固定样例演示，请从“试着问”选择一个完整问题。",
    history_mode: "历史目录属于另一种回答模式。请用原来的模式打开，或为本模式选择独立目录；原记录未被转换。",
    live_scope_required: "这个模型尚未连接。请选择“离线演示”体验对话。",
    busy: "上一轮仍在结束中，请等待状态更新后再发送。",
    stale_request: "会话已经变化。请刷新状态，不要重复发送旧问题。",
    request_conflict: "这次请求与当前状态不一致。请刷新状态，不要重复发送。",
    turn_limit: "本段对话已达到 6 次提问。可以开始新对话，清空当前上下文。",
    request_limit: "已达到 24 次提问上限，不会自动重试。启用本地历史时仍可管理和导出会话；新建、删除或重启不会恢复额度。",
    context_limit: "本段对话已达到内容上限，请开始新对话。当前上下文不会被悄悄截断。",
    response_limit: "回答超过允许的大小，未作为有效结果显示。",
    state_unavailable: "无法确认当前状态。请检查本地服务，再点击“刷新状态”；不会自动重发。",
    timeout: "这一轮等待超时，迟到的回答不会加入对话。没有自动重试。",
    source_unavailable: "无法核对样例来源，这一轮没有可用回答。",
    invalid_response: "回答或来源未通过校验，这一轮没有可用结果。",
    closed: "本地会话已结束。请重新打开当前服务页面。",
    provider_auth: "模型连接验证失败，没有自动重试。",
    provider_rate_limit: "模型暂时限制请求，没有自动重试。",
    provider_unavailable: "这一轮未能完成，没有自动重试。",
    token_count_unavailable: "本地输入估算不可用，本轮未形成有效回答。不会自动重试。",
    token_limit: "本轮超过输入或输出 token 上限，未作为有效回答保留。已派发请求仍可能计费；不会自动重试。",
    budget_limit: "本次客户端预算不足或已超限，这一轮已停止。该预算不是服务端扣费上限；不会自动重试。",
    usage_unavailable: "无法核实本轮用量，不能确认最终费用。不会自动重试。",
    completion_request_limit: "本次模型调用已达到 3 次上限（失败也计入）。不会自动重试，新对话不会恢复调用次数。",
    history_unavailable: "本地会话记录暂不可用。请核对保存目录与服务状态，再刷新；没有自动重试。",
    history_in_use: "这个本地会话目录正由其他服务使用。请先检查现有服务，不会自动接管。",
    history_version: "本地会话版本不受支持，未覆盖原有记录。",
    history_corrupt: "本地会话记录未通过校验，未覆盖原有记录。",
    history_limit: "本地会话已达到 50 段上限。可先导出需要保留的会话，再明确删除不用的记录。",
    interrupted: "上次运行中断，这一轮已停止。没有补写回答，也不会自动重试。",
    material_invalid: "材料未通过校验。请选择文件名不含路径的非空 UTF-8 .txt 文本；内容不能包含控制字符。",
    material_limit: "材料超过限制：最多 8192 字节、256 行，文件名最多 80 个字符。",
    material_exists: "当前会话已有一份材料。请先移除，再选择新的文件。",
    material_unavailable: "当前会话没有可移除的材料，或当前模式不支持材料。请刷新状态。",
    project_location_invalid: "定位结果未通过校验，请重新定位。",
    project_location_unavailable: "绑定目录或文件暂不可读，请核对后重新定位。",
    project_location_limit: "选定内容超过上限，请减少文件后重试。",
    project_location_stale: "目录或文件已变化，请重新定位。",
  };
  let snapshot = null;
  let locked = true;
  let preserveConnectionDraft = false;
  let mutating = false;
  let mutationPath = null;
  let reading = false;
  let epoch = 0;
  let controller = null;
  let poll = null;
  let renderedPrompts = "";
  let renderedCatalog = "";
  let renderedEvidence = "";
  let extraPrompts = false;
  let renderedHistory = "";
  let renderedTitle = "";
  let renderedQuery = "";
  let deleteTarget = null;
  let materialDraft = null;
  let materialRead = 0;
  let materialReading = false;
  let materialPage = 0;
  let renderedMaterial = "";
  let renderedMaterialLines = "";
  let materialSearchQuery = "";
  let materialSearchMatches = [];
  let materialSearchIndex = -1;
  let materialSearchError = "";
  const materialPageSize = 24;
  const materialSearchInvalid = "关键词需为 1–128 个字符，不能只有空白或包含控制字符。";
  const count = (value) => Array.from(value).length;
  const taskMode = (data) => data && ["agent_demo", "agent_model"].includes(data.mode);
  const materialMode = (data) => data && data.answer_mode === "ordinary"
    && (taskMode(data) || (!data.connection && data.provider_id === "offline"));
  const materialConsentKey = (data) => JSON.stringify(data && [data.conversation_id, data.revision,
    data.material && data.material.sha256, data.material && data.material.name,
    data.project && data.project.sha256, data.project && data.project.name]);

  const { materialLines, validMaterialName, validMaterialText, validMaterial } = window.PactTrailSelectionUI;
  const projectUI = window.PactTrailSelectionUI.createProject({
    state: () => ({ snapshot, locked, mutating }), updateControls, action, request,
    materialBlocked: () => Boolean(materialDraft) || materialReading || webUI.blocksTask(),
  });
  const followupUI = window.PactTrailFollowupUI.create({ state: () => ({ snapshot, locked, mutating }), action, updateControls });
  const webUI = window.PactTrailWebUI.create({ state: () => ({ snapshot, locked, mutating }), action, updateControls,
    inputsBlocked: () => Boolean(materialDraft) || materialReading || projectUI.hasDraft() });
  const repair = window.PactTrailComposerUI.createRepair({ state: () => ({ snapshot, locked, mutating }), updateControls,
    ready: () => conversationUI.ready() && !materialDraft && !materialReading && !projectUI.hasDraft() && !webUI.blocksTask(),
    prepareChange: () => draftUI.prepareChange() });
  const draftUI = window.PactTrailDraftUI.create({ state: () => ({ snapshot, locked, mutating }), action, updateControls, ...repair });
  const conversationUI = window.PactTrailConversationUI.create({ state: () => ({ snapshot, locked, mutating }), action, request, updateControls });
  const composerDrafts = window.PactTrailConversationUI.createComposerDrafts({ readTaskDraft: draftUI.readTaskDraft, restoreTaskDraft: draftUI.restoreTaskDraft });
  const inputTools = window.PactTrailComposerUI.createInputs?.({ state: () => ({ snapshot, locked: locked || agentHistoryUI.active(), mutating, epoch }), request, action, taskDraft: draftUI, updateControls });
  const messageView = window.PactTrailConversationUI.createMessages({ state: () => ({ snapshot, locked, mutating }) });
  const searchUI = window.PactTrailWebSearchUI.create({ state: () => ({ snapshot, locked, mutating }), updateControls });
  const verificationUI = window.PactTrailVerificationUI
    ? window.PactTrailVerificationUI.create({ state: () => ({ snapshot, locked, mutating }), action, updateControls, ...repair })
    : { render() {}, controls() {}, ready: () => true, close() {} };
  const agentHistoryUI = window.PactTrailTaskHistory.createAgent({ state: () => snapshot, request, updateControls,
    continueTask: binding => inputTools?.openContinuation(binding), referenceArtifact: binding => inputTools?.openArtifact(binding),
    versionsArtifact: binding => inputTools?.openVersions(binding), sourcesArtifact: binding => inputTools?.openSources(binding) });
  const connectionUI = window.PactTrailModelConnectionUI.create({
    state: () => ({ snapshot, locked, mutating }), action, updateControls, notice,
    leaveRequest: body => request("/connection", body, undefined, true),
    openPanel: () => {
      if (!wideSidebar.matches && !wideInspector.matches) setPanel("inspector", false);
      setPanel("sidebar", true);
    },
  });

  function notice(code, repairRequest = false) {
    const repairNotice = repairRequest && window.PactTrailVerificationUI?.repairFailureText?.(code);
    if (repairNotice) { text("notice", repairNotice); show("notice", true); return; }
    const conversationNotice = window.PactTrailConversationUI.notice(snapshot, code);
    if (conversationNotice) { text("notice", conversationNotice); show("notice", true); return; }
    text("notice", code === "live_scope_required" && snapshot && snapshot.connection
      ? "模型尚未就绪。请点击“配置模型”核对配置和启用状态；本次服务不会切换成离线模拟回答。"
      : snapshot && snapshot.followup && window.PactTrailFollowupUI.error(code) ? window.PactTrailFollowupUI.error(code)
        : taskMode(snapshot) && window.PactTrailTaskUI.error(code)
        ? window.PactTrailTaskUI.error(code) : Object.hasOwn(messages, code) ? messages[code]
          : window.PactTrailArtifactReferenceUI?.error(code) || window.PactTrailVerificationUI?.error(code) || window.PactTrailWebUI.error(code) || messages.state_unavailable);
    show("notice", true);
  }

  const sendPreview = inputTools.createPreview({ searchFields: searchUI.fields, locateHistory: messageView.locate,
    locateSource: ref => projectUI.locate(ref, () => { selectTab("materials"); setPanel("inspector", true); }) });

  const composerUI = window.PactTrailComposerUI.create({
    state: () => ({ snapshot, locked, mutating, historyActive: agentHistoryUI.active() }),
    inputLimit: window.PactTrailConversationUI.inputLimit, validText: validOrdinaryText,
    ready: () => inputTools?.ready?.() !== false && followupUI.ready() && draftUI.ready() && conversationUI.ready() && verificationUI.ready() && !webUI.blocksTask() && !projectUI.hasDraft(),
    readTaskDraft: draftUI.readTaskDraft,
    openFiles: () => { if (!wideSidebar.matches) setPanel("sidebar", false); selectTab("materials"); setPanel("inspector", true, true); },
    focusComposer: () => {
      if (!wideSidebar.matches) setPanel("sidebar", false);
      if (!wideInspector.matches) setPanel("inspector", false);
    },
  });

  function updateControls() {
    composerDrafts.capture();
    const verificationRunning = snapshot && snapshot.verification && snapshot.verification.status === "running";
    const unavailable = locked || !snapshot || mutating || verificationRunning || draftUI.checking?.() || agentHistoryUI.active();
    const live = snapshot && snapshot.provider_id !== "offline";
    const connection = snapshot && snapshot.connection;
    const modelBlocked = live && !(connection && connection.can_send);
    const busy = snapshot && snapshot.busy;
    const historyLimit = snapshot && (snapshot.history?.requests_used >= 24 || snapshot.send_budget?.requests_used >= 24);
    composerUI.controls({ unavailable, modelBlocked, historyLimit });
    el("new-chat").disabled = unavailable;
    el("provider").disabled = unavailable || Boolean(connection) || taskMode(snapshot);
    connectionUI.controls(unavailable, busy);
    const historyUnavailable = unavailable || !snapshot.history;
    for (const name of ["history-query", "history-search", "history-title", "history-rename",
      "history-save", "history-export", "history-delete", "history-delete-yes", "history-delete-no"]) {
      el(name).disabled = historyUnavailable;
    }
    for (const child of el("history-list").children) child.disabled = historyUnavailable;
    const materialUnavailable = unavailable || busy || !materialMode(snapshot);
    const attached = snapshot && snapshot.material;
    el("task-use-material").disabled = unavailable || busy || !taskMode(snapshot) || !(attached || snapshot.project);
    el("material-file").disabled = materialUnavailable || Boolean(attached || snapshot.project || projectUI.hasDraft()) || materialReading;
    el("material-attach").disabled = materialUnavailable || !materialDraft || materialReading;
    el("material-remove").disabled = materialUnavailable || !attached;
    el("material-cancel").disabled = !materialDraft && !materialReading;
    const preview = materialDraft || attached;
    el("material-previous").disabled = !preview || materialPage === 0;
    el("material-next").disabled = !preview || (materialPage + 1) * materialPageSize >= preview.line_count;
    const findUnavailable = unavailable || !searchableMaterial();
    el("material-search").disabled = findUnavailable;
    el("material-match-previous").disabled = findUnavailable || materialSearchIndex <= 0;
    el("material-match-next").disabled = findUnavailable || materialSearchIndex < 0
      || materialSearchIndex >= materialSearchMatches.length - 1;
    el("material-search-clear").disabled = findUnavailable || (!el("material-search").value && !materialSearchError);
    projectUI.controls(unavailable);
    followupUI.controls(unavailable);
    webUI.controls(unavailable);
    draftUI.controls(locked || !snapshot || mutating || verificationRunning || agentHistoryUI.active());
    conversationUI.controls(unavailable);
    messageView.controls(unavailable);
    verificationUI.controls(locked || !snapshot || mutating || agentHistoryUI.active());
    agentHistoryUI.controls(unavailable || busy || reading || materialReading || snapshot?.web?.busy);
    el("refresh").disabled = mutating || reading;
    show("recover-state", locked || Boolean(snapshot?.operation?.error && !busy));
    el("recover-state").disabled = mutating || reading;
    text("refresh", reading ? "正在核对…" : "刷新状态");
    const running = snapshot && snapshot.operation && snapshot.operation.status === "running";
    show("stop", Boolean(running));
    el("stop").disabled = unavailable || !running;
    Array.from(el("prompts").children).forEach((child, index) => {
      child.disabled = unavailable || modelBlocked || busy
        || (live && connection && snapshot.answer_mode !== "ordinary" && index !== connection.calls);
    });
    const progress = window.PactTrailTaskUI.controlProgress(snapshot, { stopping: mutationPath === "/stop",
      locked, unavailable, mutating, reading, historyActive: agentHistoryUI.active() });
    const status = progress?.statusLabel || (locked || !snapshot ? "待刷新" : reading ? "正在核对" : busy ? "处理中"
      : live ? connection ? connectionLabel(connection) : "未连接" : historyLimit ? "提问额度用尽" : "就绪");
    text("conversation-status", status);
    text("session-state", status);
    window.PactTrailTaskUI.controls(unavailable);
    searchUI.controls(unavailable);
    sendPreview.render();
    inputTools?.render();
    composerUI.renderWorkbench();
  }

  function cancelRead() {
    epoch += 1;
    if (controller) controller.abort();
    if (poll !== null) clearTimeout(poll);
    poll = null;
    controller = new AbortController();
    return epoch;
  }

  function schedulePoll() {
    if (poll !== null) clearTimeout(poll);
    poll = null;
    if (snapshot && (snapshot.busy || snapshot.web && snapshot.web.busy) && !locked && !mutating && !reading) {
      poll = setTimeout(() => { poll = null; refresh(); }, 500);
    }
  }

  function isText(value, max) { return typeof value === "string" && value.length <= max; }
  const id = (value) => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const integer = (value, low, high) => Number.isInteger(value) && value >= low && value <= high;
  const titleText = (value) => typeof value === "string" && count(value) <= 80 && value.trim().length > 0;
  function validMessages(list, max = 12) {
    return Array.isArray(list) && list.length <= max && list.every((message) => message
      && ["user", "assistant"].includes(message.role)
      && ["complete", "stopped", "error"].includes(message.status)
      && isText(message.text, 65536) && id(message.operation_id)
      && Array.isArray(message.sources) && message.sources.length <= 3
      && message.sources.every((source) => source && isText(source.source, 256)
        && integer(source.line_start, 1, 100000) && integer(source.line_end, source.line_start, 100000)
        && isText(source.text, 32768)));
  }
  function validHistory(history) {
    return history && isText(history.location, 4096) && history.location.length > 0
      && titleText(history.current_title) && history.max_conversations === 50
      && integer(history.requests_used, 0, 24)
      && typeof history.query === "string" && count(history.query) <= 80
      && Array.isArray(history.items) && history.items.length <= 50
      && new Set(history.items.map((item) => item && item.id)).size === history.items.length
      && history.items.every((item) => item && id(item.id) && titleText(item.title)
        && integer(item.turns, 0, 6));
  }
  function validState(data) {
    const connection = data && Object.hasOwn(data, "connection");
    const local = data && data.mode === "local_model";
    const tool = data && data.mode === "agent_model";
    const ordinary = data && data.answer_mode === "ordinary";
    if (!data || !["offline_demo", "local_model", "agent_demo", "agent_model"].includes(data.mode)
        || !window.PactTrailTaskHistory.validAvailability(data)
        || !window.PactTrailTaskUI.valid(data) || !window.PactTrailFollowupUI.valid(data) || !window.PactTrailWebUI.valid(data)
        || !window.PactTrailConversationUI.valid(data)
        || window.PactTrailArtifactReferenceUI?.valid(data) === false
        || (Object.hasOwn(data, "web_search_provider") && (data.web_search_provider !== "deepseek"
          || data.web_search_available !== true || !tool))
        || (Object.hasOwn(data, "web_search_available") && (data.web_search_available !== true
          || !data.agent_history_available || !data.conversation
          || !window.PactTrailWebSearchUI.validState(data.web_search_state)))
        || (Object.hasOwn(data, "send_budget") && (!data.agent_history_available || !data.conversation
          || !data.send_budget || Object.keys(data.send_budget).length !== 2
          || !integer(data.send_budget.requests_used, 0, 24) || data.send_budget.max_requests !== 24))
        || (window.PactTrailVerificationUI && !window.PactTrailVerificationUI.valid(data))
        || (snapshot && (taskMode(snapshot) || taskMode(data)) && snapshot.mode !== data.mode)
        || (snapshot && (snapshot.answer_mode !== data.answer_mode || Boolean(snapshot.connection) !== connection))
        || (connection ? (!window.PactTrailTaskUI.validConnection(data.connection, tool, { isText, integer, messages })
          || Object.hasOwn(data, "history") || (!tool && (Object.hasOwn(data, "material") || Object.hasOwn(data, "project")))
          || data.live_available !== data.connection.can_send || (ordinary && !local && !tool)
          || (local || tool ? data.provider_id !== "deepseek"
            || (!ordinary && !["configured_unverified", "ready", "failed"].includes(data.connection.state))
            : !["unconfigured", "cleared", "failed"].includes(data.connection.state)))
          : local || tool || data.live_available !== false)
        || (Object.hasOwn(data, "answer_mode") && data.answer_mode !== "ordinary")
        || !id(data.conversation_id) || !integer(data.revision, 0, 2147483647)
        || !providers.includes(data.provider_id) || typeof data.busy !== "boolean"
        || !data.limits || data.limits.max_turns !== (data.conversation ? data.conversation.max_turns : 6) || data.limits.max_requests !== 24
        || !window.PactTrailConversationUI.validMessageLimit(data) || data.limits.max_context_bytes !== 32768
        || data.limits.max_response_bytes !== (window.PactTrailConversationUI.configuredCapacity(data) ? 524288 : 65536)
        || !Array.isArray(data.providers) || data.providers.length !== 8
        || new Set(data.providers.map((provider) => provider && provider.id)).size !== 8
        || !data.providers.every((provider) => provider && providers.includes(provider.id)
          && isText(provider.label, 64)
          && provider.availability === (provider.id === "offline" ? "offline_demo" : "not_configured"))
        || !Array.isArray(data.prompts) || data.prompts.length !== (taskMode(data) ? 2 : local ? 3 : 7)
        || !data.prompts.every((prompt) => isText(prompt, 256))
        || !validMessages(data.messages, data.conversation ? window.PactTrailConversationUI.configuredCapacity(data) ? 64 : 24 : 12)
        || (data.answer_mode === "ordinary" && !validOrdinaryMessages(data.messages, data.conversation ? 4096 : data.limits.max_message_chars))
        || (Object.hasOwn(data, "material") && (data.answer_mode !== "ordinary" || !validMaterial(data.material)))
        || (Object.hasOwn(data, "project") && (!taskMode(data) || Object.hasOwn(data, "material")
          || !window.PactTrailTaskUI.validProject(data.project, true)))
        || (Object.hasOwn(data, "history") && !validHistory(data.history))) return false;
    if (data.operation !== null && (!data.operation || !id(data.operation.id)
        || !["running", "complete", "stopped", "error"].includes(data.operation.status)
        || (data.operation.error !== null && !Object.hasOwn(messages, data.operation.error)
          && !(taskMode(data) && window.PactTrailTaskUI.error(data.operation.error))))) return false;
    return true;
  }

  function validOrdinaryText(value, max) {
    return typeof value === "string" && value.replace(/[\s\u0085]/gu, "").length > 0
      && count(value) <= max && !/[\u0000-\u0008\u000b-\u001f\u007f\ud800-\udfff]/u.test(value);
  }

  function validOrdinaryMessages(list, max = 256) {
    return list.every((message) => message.sources.length === 0
      && window.PactTrailWebSearchUI.validMessage(message)
      && validOrdinaryText(message.text, message.role === "user" ? max : 4096));
  }

  function clearMaterialDraft() {
    if (materialDraft || materialReading) materialPage = 0;
    materialRead += 1;
    materialDraft = null;
    materialReading = false;
    el("material-file").value = "";
    text("material-feedback", "");
    show("material-feedback", false);
  }

  function searchableMaterial() {
    return materialMode(snapshot) && snapshot.material && !materialDraft;
  }

  function clearMaterialSearch() {
    materialSearchQuery = "";
    materialSearchMatches = [];
    materialSearchIndex = -1;
    materialSearchError = "";
    el("material-search").value = "";
  }

  function findMaterial() {
    if (el("material-search").disabled || !searchableMaterial()) return;
    const query = el("material-search").value;
    materialSearchQuery = "";
    materialSearchMatches = [];
    materialSearchIndex = -1;
    materialSearchError = "";
    if (query.length) {
      if (query.length > 256 || count(query) > 128 || !query.replace(/[\s\u0085]/gu, "").length
          || /[\u0000-\u001f\u007f\ud800-\udfff]/u.test(query)) {
        materialSearchError = materialSearchInvalid;
      } else {
        materialSearchQuery = query;
        materialLines(snapshot.material.text).forEach((line, index) => {
          if (line.includes(query)) materialSearchMatches.push(index);
        });
        if (materialSearchMatches.length) {
          materialSearchIndex = 0;
          materialPage = Math.floor(materialSearchMatches[0] / materialPageSize);
        }
      }
    }
    renderMaterial(true);
    updateControls();
  }

  function moveMaterialMatch(delta) {
    if (locked || mutating || !searchableMaterial() || materialSearchIndex < 0) return;
    const next = materialSearchIndex + delta;
    if (next < 0 || next >= materialSearchMatches.length) return;
    materialSearchIndex = next;
    materialPage = Math.floor(materialSearchMatches[next] / materialPageSize);
    renderMaterial(true);
    updateControls();
  }

  function renderMaterial(revealMatch = false) {
    const ordinary = snapshot && snapshot.answer_mode === "ordinary";
    const tool = taskMode(snapshot);
    show("local-material", Boolean(materialMode(snapshot)));
    const boundInput = tool && (snapshot.material || snapshot.project);
    show("task-material-consent", Boolean(boundInput));
    text("task-material-consent-label", boundInput
      ? "本次目标使用「" + boundInput.name + "」" + (snapshot.project ? "选定文件清单" : "文本")
        + "快照；允许工具读取，并将所选内容和工具结果发送到本次"
        + (snapshot.mode === "agent_model" && snapshot.connection.transport_mode === "live" ? "模型连接。" : " synthetic 协议/决策器。")
      : "本次目标使用已关联的文本快照。");
    show("evidence-intro", !ordinary);
    if (!materialMode(snapshot)) {
      show("material-search-controls", false);
      return;
    }
    const material = materialDraft || snapshot.material;
    const searchable = Boolean(searchableMaterial());
    show("material-search-controls", searchable);
    const selectedLine = searchable && materialSearchIndex >= 0 ? materialSearchMatches[materialSearchIndex] : -1;
    text("material-search-status", materialSearchError || (materialSearchMatches.length
      ? "命中 " + materialSearchMatches.length + " 行 · 第 " + (materialSearchIndex + 1) + "/"
        + materialSearchMatches.length + " 处 · 原文第 " + (selectedLine + 1) + " 行"
      : materialSearchQuery ? "命中 0 行 · 未找到匹配的原文行。" : "输入关键词，查找当前材料原文。"));
    el("material-search").setAttribute("aria-invalid", String(Boolean(materialSearchError)));
    text("material-scope", tool
      ? "仅主动选择的这一份文本快照可用于任务；不读取其目录或其他文件。加入只在当前服务中关联，每次发送前需勾选使用范围；发送后快照与报告保存到本次任务目录。新对话清除当前关联，已保存文件保留。"
      : snapshot.history
      ? "加入后，材料快照会随当前会话保存到本机历史数据库，也会包含在会话导出中。原文件不会被修改。"
      : "加入后，材料只保存在当前服务内存中；新对话或关闭服务后不保留。原文件不会被修改。");
    text("material-empty", materialReading ? "正在本机读取和校验…" : "当前会话还没有材料。");
    show("material-empty", !material);
    show("material-preview", Boolean(material));
    show("material-attach", Boolean(materialDraft));
    show("material-cancel", Boolean(materialDraft) || materialReading);
    show("material-remove", Boolean(snapshot.material));
    text("material-picker-note", snapshot.material ? "每段会话最多一份材料。请先移除，再选择新的文件。"
      : "仅选择一份 UTF-8 .txt，最多 8192 字节、256 行。先预览，再加入当前会话。");
    if (!material) {
      el("material-lines").replaceChildren();
      renderedMaterialLines = "";
      text("material-name", "");
      text("material-metadata", "");
      return;
    }
    const lines = materialLines(material.text);
    materialPage = Math.min(materialPage, Math.ceil(lines.length / materialPageSize) - 1);
    const first = materialPage * materialPageSize;
    const last = Math.min(first + materialPageSize, lines.length);
    text("material-preview-state", materialDraft ? "预览 · 尚未加入会话" : tool ? "已关联快照 · 发送前请确认本次使用" : "已加入当前会话");
    text("material-name", material.name);
    text("material-metadata", material.byte_count + " 字节 · " + material.line_count + " 行"
      + (tool && material.sha256 ? " · SHA-256 " + material.sha256 : ""));
    text("material-page-label", "第 " + (first + 1) + "–" + last + " 行 / 共 " + lines.length + " 行");
    const query = searchable ? materialSearchQuery : "";
    const linesKey = JSON.stringify([material.text, materialPage, query, selectedLine]);
    if (linesKey !== renderedMaterialLines) {
      el("material-lines").replaceChildren(...window.PactTrailSelectionUI.materialRows(lines, first, last, query, selectedLine));
      renderedMaterialLines = linesKey;
    }
    if (revealMatch && selectedLine >= first && selectedLine < last) {
      const row = el("material-lines").children[selectedLine - first];
      const mark = Array.from(row.children[1].children).find((child) => typeof child.tagName === "string"
        && child.tagName.toLowerCase() === "mark");
      if (mark) mark.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }

  async function chooseMaterial() {
    if (locked || mutating || !snapshot || snapshot.busy || !materialMode(snapshot)
        || snapshot.material || snapshot.project || projectUI.hasDraft() || webUI.blocksTask()) return;
    const files = Array.from(el("material-file").files || []);
    clearMaterialDraft();
    renderMaterial();
    projectUI.render();
    if (!files || files.length === 0) { updateControls(); return; }
    const current = materialRead;
    const scope = { conversation_id: snapshot.conversation_id, revision: snapshot.revision };
    const isCurrent = () => current === materialRead && !locked && !mutating && snapshot
      && !snapshot.busy && !snapshot.material && materialMode(snapshot)
      && scope.conversation_id === snapshot.conversation_id && scope.revision === snapshot.revision;
    materialReading = true;
    renderMaterial();
    updateControls();
    try {
      if (files.length !== 1) throw new Error("material_invalid");
      const file = files[0];
      if (typeof file.name === "string" && count(file.name) > 80) throw new Error("material_limit");
      if (!validMaterialName(file.name)) throw new Error("material_invalid");
      const content = await window.PactTrailSelectionUI.readText(file, isCurrent);
      if (!content) return;
      materialDraft = { ...scope, name: file.name, ...content };
    } catch (error) {
      if (isCurrent()) {
        text("material-feedback", messages[error.message] || messages.material_invalid);
        show("material-feedback", true);
      }
    } finally {
      if (current === materialRead) { materialReading = false; renderMaterial(); updateControls(); }
    }
  }

  function node(tag, className, value) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (value !== undefined) result.textContent = value;
    return result;
  }

  const wideSidebar = window.matchMedia("(min-width: 760px)");
  const wideInspector = window.matchMedia("(min-width: 1200px)");
  const setPanel = window.PactTrailSelectionUI.setPanel;

  const selectTab = window.PactTrailSelectionUI.selectTab;

  function renderWorkspace(data) {
    const firstQuestion = data.messages.find((message) => message.role === "user");
    const title = data.history ? data.history.current_title : firstQuestion
      ? Array.from(firstQuestion.text).slice(0, 26).join("") + (count(firstQuestion.text) > 26 ? "…" : "")
      : "新的对话";
    text("conversation-title", title);
    text("sidebar-conversation", title);
    text("session-turns", data.messages.filter((message) => message.role === "user").length + " / 6");
    text("session-provider", data.providers.find((provider) => provider.id === data.provider_id).label
      + (data.mode === "local_model" ? " · " + connectionLabel(data.connection)
        : data.provider_id === "offline" ? "" : " · 未连接"));
    renderHistory(data);
    const materialKey = JSON.stringify([data.conversation_id, data.material && data.material.sha256,
      data.material && data.material.name]);
    if (materialKey !== renderedMaterial) {
      materialPage = 0;
      renderedMaterial = materialKey;
      clearMaterialSearch();
    }
    renderMaterial();
    projectUI.render();
    const completed = data.messages.filter((message) => message.role === "assistant" && message.status === "complete");
    const sources = completed.length ? completed[completed.length - 1].sources : [];
    const key = JSON.stringify([data.conversation_id, data.answer_mode, sources]);
    if (key === renderedEvidence) return;
    renderedEvidence = key;
    text("source-count", String(sources.length));
    show("evidence-empty", sources.length === 0 && (data.answer_mode !== "ordinary" || Boolean(data.connection)));
    if (!sources.length) {
      el("evidence-list").replaceChildren();
      return;
    }
    el("evidence-list").replaceChildren(...sources.map((source, index) => {
      const card = node("article", "evidence-card");
      card.appendChild(node("span", "evidence-index", String(index + 1).padStart(2, "0")));
      card.appendChild(node("h3", "evidence-title", source.source));
      card.appendChild(node("p", "evidence-location", "第 " + source.line_start
        + (source.line_end === source.line_start ? "" : "–" + source.line_end) + " 行"));
      card.appendChild(node("blockquote", "evidence-excerpt", source.text));
      return card;
    }));
  }

  function clearDelete() {
    deleteTarget = null;
    show("history-delete-confirm", false);
  }

  function renderHistory(data) {
    const history = data.history;
    show("history-workspace", Boolean(history));
    show("history-management", Boolean(history));
    text("memory-caption", history ? "本地历史已启用 · 固定离线样例" : "仅当前会话 · 暂不保存历史");
    text("storage-note-title", history ? "会话保存在本机" : "这段对话留在此刻");
    text("storage-note", history
      ? "会话会自动保存到你选择的位置；关闭服务后，再使用同一目录启动即可继续。打开多个标签页会看到同一段当前对话。"
      : "仅保存在当前服务的内存中。刷新可查看当前对话；关闭服务后不保留。打开多个标签页会看到同一段对话。");
    text("storage-limit-note", history
      ? "本地历史最多 50 段，合计最多 24 次提问。新建、删除或重启不会恢复提问额度；达到上限后仍可管理和导出。"
      : data.connection ? "新对话会清空上下文并清除配置。调用次数和费用保留；已有派发后，本次服务不能重新配置。"
      : "新对话会清空当前上下文，不会保存历史。");
    text("session-location", history ? history.location : "本机进程内存");
    text("session-boundary-title", history ? "保存在本机的离线记录" : "此刻的对话");
    text("session-boundary-note", history
      ? "自动保存当前记录；也可手动保存。导出为离线 JSON，删除只影响本地会话记录。这里仍是固定样例，没有真实模型连接。"
      : data.connection ? "仅保留当前服务内存中的对话，不保存本地历史。刷新或离开页面会尝试清除模型配置；关闭服务后不保留。"
      : "刷新页面可继续查看。新对话会清空当前上下文；关闭服务后不保留。");
    if (deleteTarget && (deleteTarget.id !== data.conversation_id || deleteTarget.revision !== data.revision)) clearDelete();
    if (!history) { clearDelete(); return; }
    const titleKey = JSON.stringify([data.conversation_id, history.current_title]);
    if (titleKey !== renderedTitle) {
      el("history-title").value = history.current_title;
      renderedTitle = titleKey;
    }
    if (history.query !== renderedQuery) {
      el("history-query").value = history.query;
      renderedQuery = history.query;
    }
    const listKey = JSON.stringify([data.conversation_id, history.items]);
    if (listKey !== renderedHistory) {
      el("history-list").replaceChildren(...history.items.map((item) => {
        const button = node("button", "history-item");
        button.type = "button";
        button.setAttribute("aria-current", String(item.id === data.conversation_id));
        button.appendChild(node("span", "history-item-title", item.title));
        button.appendChild(node("span", "history-item-turns", item.turns + " / 6"));
        button.addEventListener("click", () => {
          if (button.disabled || !snapshot || item.id === snapshot.conversation_id) return;
          return historyAction("select", { target_id: item.id }, true);
        });
        return button;
      }));
      renderedHistory = listKey;
    }
    show("history-empty", history.items.length === 0);
  }

  function render(data, { preserveComposer = false } = {}) {
    if (data && data.mode === "task_history") return window.PactTrailTaskHistory.mount(data, request);
    if (!validState(data)) throw new Error("invalid_response");
    const managedDraft = composerDrafts.render(data);
    const position = messageView.capture();
    if (materialConsentKey(snapshot) !== materialConsentKey(data)) el("task-use-material").checked = false;
    if (snapshot && (snapshot.conversation_id !== data.conversation_id || snapshot.revision !== data.revision
        || data.busy)) { clearMaterialDraft(); projectUI.clear(); }
    if (snapshot && snapshot.conversation_id !== data.conversation_id) {
      if (!managedDraft && !preserveComposer) el("message").value = "";
      connectionUI.clearInput();
      show("history-feedback", false);
    }
    snapshot = data;
    locked = false;
    show("notice", false);
    const ordinary = data.answer_mode === "ordinary";
    const local = data.mode === "local_model";
    connectionUI.render(data);
    text("materials-tab-title", ordinary && !local ? "材料" : "来源");
    show("source-count", !ordinary);
    show("answer-mode-note", ordinary);
    if (ordinary && local) {
      document.title = "PactTrail · 普通模型对话";
      text("workspace-caption", "普通模型对话");
      text("workspace-eyebrow", "普通模型对话");
      text("local-scope", "DeepSeek · 普通对话");
      text("source-footnote", "本模式没有材料来源；不接收或读取材料，也不生成来源引用。模型回答仅作为普通文本显示。");
      text("answer-mode-note", "普通模型对话 · 启用配置后可自由输入，最多派发 3 次；仅成功轮次进入上下文，不执行工具。未就绪时不能发送。");
      text("welcome-eyebrow", "普通模型对话");
      text("welcome-title", "这次想做什么？");
      text("welcome-description", "连接模型，开始对话。");
      text("demo-note", "仅点击发送才调用模型，失败也计入 3 次额度并可能计费；不会自动重试。停止不保证远端取消或零费用。");
      text("evidence-empty-text", "本次是普通文本对话，没有材料来源。回答不附材料引用。");
      text("input-note", "Enter 发送 · Shift + Enter 换行。最多 256 字；只带入当前会话的成功轮次，不发送材料，不执行工具。");
      el("message").placeholder = "输入普通文本，与 DeepSeek 对话…";
      el("message").maxLength = 512;
    } else if (ordinary) {
      document.title = "PactTrail · 普通文本离线演示";
      text("workspace-caption", "普通文本演示");
      text("workspace-eyebrow", "普通文本演示");
      text("local-scope", "本机 · 普通文本演示");
      text("source-footnote", "材料仅供本机查看，不发送给模型，不进入模拟回答或对话上下文，也不生成来源引用。移除不修改原文件或此前导出的副本。");
      text("answer-mode-note", "普通文本 · 离线模拟：可输入自己的测试问题，只返回固定模板和成功上下文，不是真实 AI 回答。");
      text("welcome-eyebrow", "离线对话演示");
      text("welcome-title", "这次想做什么？");
      text("welcome-description", "自由输入，体验离线演示。");
      text("demo-note", "这是固定模板演示，不会真正解释、翻译或执行输入的请求。");
      text("evidence-empty-text", "普通文本演示不生成来源。所选材料仅供本机查看。");
      text("input-note", "Enter 发送 · Shift + Enter 换行。最多 256 字；仅当前会话的成功轮次进入上下文，不执行工具。");
      el("message").placeholder = "输入普通文本，体验离线上下文…";
      el("message").maxLength = 512;
    } else {
      document.title = local ? "PactTrail · DeepSeek 样例对话" : "PactTrail · 对话";
      text("workspace-caption", "对话");
      text("workspace-eyebrow", "对话");
      text("local-scope", local ? "DeepSeek · 固定样例" : "本机 · 固定样例");
      text("welcome-eyebrow", "样例对话");
      text("welcome-title", "这次想做什么？");
      text("welcome-description", "选择示例，开始体验。");
      text("demo-note", local ? "按顺序发送下方三个固定问题。仅点击发送才调用模型；不自动重试。"
        : "回答由固定演示规则生成，不是真实 AI 回答。当前仅接受下方样例问题。");
      text("source-footnote", "仅使用三份虚构样例。引用是核对入口，请结合原文判断。");
      text("input-note", "Enter 发送 · Shift + Enter 换行。来源可展开核对；不会写入报告或执行工具。");
      el("message").placeholder = local ? "按顺序选择本轮固定问题…" : "选择一个样例问题，开始对话…";
      el("message").maxLength = 256;
    }
    text("mode-badge", local ? "DeepSeek · " + (ordinary ? "普通对话 · " : "")
      + connectionLabel(data.connection) : "离线演示 · 不连接模型");
    text("composer-provider", local ? "DeepSeek" : "离线演示");
    show("more-prompts", !local);
    const catalogKey = JSON.stringify([local, data.providers.map((provider) => [provider.id, provider.label])]);
    if (catalogKey !== renderedCatalog) {
      const options = data.providers.map((provider) => {
        const option = node("option", "", provider.label + (local && provider.id === "deepseek"
          ? " · 本次配置" : provider.id === "offline" ? "" : " · 未连接"));
        option.value = provider.id;
        return option;
      });
      el("provider").replaceChildren(...options);
      renderedCatalog = catalogKey;
    }
    el("provider").value = data.provider_id;
    text("model-note", local ? ordinary ? "deepseek-flash · 普通文本对话，最多调用 3 次；配置状态见下方。"
      : "deepseek-flash · 仅三轮固定样例；配置状态见下方。" : data.provider_id === "offline"
      ? (ordinary ? "普通文本固定模板；回答不使用所选材料，不连接模型。" : "固定演示回答，仅使用随包的三份虚构材料。")
      : "尚未配置真实模型，也不会发送请求。切换到离线演示即可继续。切换会开启新对话。");
    const promptsKey = JSON.stringify(data.prompts);
    if (promptsKey !== renderedPrompts) {
      el("prompts").replaceChildren(...data.prompts.map((prompt, index) => {
        const button = node("button", "prompt", prompt);
        button.type = "button";
        button.hidden = index >= 3 && !extraPrompts;
        button.addEventListener("click", () => {
          if (button.disabled) return;
          el("composer-fold").open = true;
          el("message").value = prompt;
          show("notice", false);
          updateControls();
          el("message").focus();
        });
        return button;
      }));
      renderedPrompts = promptsKey;
    }
    const reading = messageView.render(data, data.agent_history_available ? "PactTrail" + (data.connection ? "" : " · 演示")
      : taskMode(data) ? "PactTrail · 工具任务"
      : local ? "PactTrail · DeepSeek" : "PactTrail · 离线演示", position);
    show("welcome", data.messages.length === 0);
    show("pending", data.busy);
    text("pending-note", draftUI.pending?.() || (data.verification && data.verification.status === "running" ? "正在验证 Python 语法…"
      : data.operation && data.operation.status === "running"
      ? local ? "正在等待 DeepSeek 回答…" : "正在准备离线回答…"
      : "正在结束上一轮；完成后可以继续发送。新对话不会接收旧回答。"));
    const operation = data.operation;
    const note = operation && operation.error === "interrupted" ? messages.interrupted
      : operation && operation.status === "stopped"
      ? "已停止。本轮迟到的回答不会进入对话，也不会自动重试。" + (local ? "已派发请求仍可能计费。" : "")
      : operation && operation.status === "error" ? messages[operation.error] || messages.state_unavailable : "";
    text("operation-note", note);
    show("operation-note", Boolean(note));
    text("turn-count", local ? "模型调用 " + data.connection.calls + " / 3 · " + (ordinary ? "可自由输入" : "按顺序发送")
      : data.messages.filter((message) => message.role === "user").length + " / 6 次提问 · "
      + (data.history ? "本地累计 " + data.history.requests_used + " / 24" : "每次服务最多 24 次"));
    renderWorkspace(data);
    window.PactTrailTaskUI.render(data);
    followupUI.render(data);
    webUI.render(data);
    draftUI.render(data);
    conversationUI.render(data);
    if (data.agent_history_available) {
      text("composer-provider", data.connection ? "DeepSeek" : "演示");
      const scope = data.conversation?.scope;
      if (["empty", "confirmed"].includes(scope?.state) && window.PactTrailConversationUI.scopeReady(data)) show("conversation-scope", false);
    }
    verificationUI.render(data);
    updateControls();
    messageView.restore(reading);
  }

  async function request(path, body, signal, keepalive = false) {
    const options = {
      method: body === undefined ? "GET" : "POST", mode: "same-origin", credentials: "omit",
      cache: "no-store", redirect: "error", signal,
      ...(keepalive ? { keepalive: true } : {}),
      headers: { "X-PactTrail-Session": capability },
    };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) {
      const code = data && typeof data.error === "string" ? data.error.replace(/^chat_error: /, "") : "state_unavailable";
      throw new Error(Object.hasOwn(messages, code) || window.PactTrailTaskHistory?.error(code) || window.PactTrailFollowupUI.error(code) || window.PactTrailWebUI.error(code)
        || window.PactTrailVerificationUI?.error(code)
        || (taskMode(snapshot) && window.PactTrailTaskUI.error(code)) ? code : "state_unavailable");
    }
    return data;
  }

  async function refresh() {
    if (mutating || agentHistoryUI.active()) return;
    const current = cancelRead();
    reading = true;
    updateControls();
    try {
      const data = await request("/status", undefined, controller.signal);
      if (current === epoch) {
        render(data, { preserveComposer: preserveConnectionDraft });
        preserveConnectionDraft = false;
      }
    } catch (error) {
      if (current === epoch) { locked = true; notice(error.message); }
    } finally {
      if (current === epoch) { reading = false; updateControls(); schedulePoll(); }
    }
  }

  async function action(path, fields, clearInput = false) {
    if (mutating || locked || !snapshot || agentHistoryUI.active()) return;
    if (snapshot.verification && snapshot.verification.status === "running"
      && !(path === "/verify" && fields.action === "cancel")) return;
    if (draftUI.allowed?.(path, fields) === false) return;
    const submittedInput = clearInput ? composerUI.capture() : null;
    mutating = true;
    mutationPath = path;
    const current = cancelRead();
    reading = false;
    const body = { conversation_id: snapshot.conversation_id, revision: snapshot.revision, ...fields };
    if (path === "/send") composerDrafts.submitted(body);
    if (!["/continuation", "/artifact-reference"].includes(path) && !window.PactTrailSavedInputUI?.handles(path, fields)
      && !window.PactTrailHistoryReferenceUI?.handles(path, fields) && !window.PactTrailConversationArchiveUI?.handles(path, fields)) {
      el("task-use-material").checked = false;
      followupUI.clearConsent();
      webUI.invalidate();
      clearMaterialDraft();
      projectUI.clear();
    }
    renderMaterial();
    projectUI.render();
    show("history-feedback", false);
    clearDelete();
    updateControls();
    try {
      const data = await request(path, body, controller.signal);
      if (current === epoch) {
        if (path === "/history" && fields.action === "export") {
          downloadHistory(data, body.conversation_id);
          text("history-feedback", "已生成当前离线会话的 JSON 下载。请在浏览器中确认文件已保存。");
          show("history-feedback", true);
        } else {
          // Preserve page input when connection setup replaces its session.
          render(data, { preserveComposer: path === "/connection" });
          if (clearInput && !composerDrafts.active()) composerUI.clearIfUnchanged(submittedInput);
          if (path === "/history" && ["save", "rename"].includes(fields.action)) {
            text("history-feedback", fields.action === "save" ? "当前会话已保存到本机。" : "会话名称已保存。");
            show("history-feedback", true);
          }
        }
        return data;
      }
    } catch (error) {
      // Unknown POST results require an explicit status read; never replay.
      if (current === epoch) {
        preserveConnectionDraft = path === "/connection";
        locked = true;
        notice(error.message, path === "/send" && fields.task_intent === "change_draft"
          && typeof fields.message === "string" && fields.message.startsWith("修复 Python 语法验证失败；"));
      }
    } finally {
      if (current === epoch) { mutating = false; mutationPath = null; updateControls(); schedulePoll(); }
    }
  }

  function downloadHistory(data, conversationId) {
    if (!data || data.format !== "pacttrail-offline-conversation" || data.version !== 1
        || data.mode !== "offline_demo" || data.conversation_id !== conversationId
        || !titleText(data.title) || !validMessages(data.messages)
        || (data.answer_mode ?? "evidence") !== (snapshot.answer_mode ?? "evidence")
        || (data.answer_mode === "ordinary" && !validOrdinaryMessages(data.messages))
        || (Object.hasOwn(data, "material") && (data.answer_mode !== "ordinary" || !validMaterial(data.material)))) throw new Error("invalid_response");
    const exported = {
      format: data.format, version: data.version, mode: data.mode, title: data.title,
      conversation_id: conversationId,
      messages: data.messages.map((message) => ({ role: message.role, text: message.text,
        status: message.status, operation_id: message.operation_id,
        sources: message.sources.map((source) => ({ source: source.source, line_start: source.line_start,
          line_end: source.line_end, text: source.text })) })),
    };
    if (data.answer_mode === "ordinary") exported.answer_mode = "ordinary";
    if (data.material) exported.material = { name: data.material.name, text: data.material.text,
      sha256: data.material.sha256, byte_count: data.material.byte_count, line_count: data.material.line_count };
    const url = URL.createObjectURL(new Blob([JSON.stringify(exported, null, 2) + "\n"], { type: "application/json" }));
    const link = node("a");
    link.href = url;
    link.download = "pacttrail-offline-" + conversationId + ".json";
    link.hidden = true;
    try { document.body.appendChild(link); link.click(); }
    finally { link.remove(); setTimeout(() => URL.revokeObjectURL(url), 0); }
  }

  function historyAction(name, fields = {}, clearInput = false) {
    if (!snapshot || !snapshot.history) return;
    return action("/history", { action: name, ...fields }, clearInput);
  }

  function requestId() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }

  function send(event) {
    event.preventDefault();
    if (mutating || locked || !snapshot || agentHistoryUI.active() || snapshot.busy || (snapshot.provider_id !== "offline"
        && !(snapshot.connection && snapshot.connection.can_send))) return;
    if (snapshot.verification?.status === "running" || draftUI.checking?.()
        || inputTools?.ready?.() === false || !followupUI.ready() || !draftUI.ready() || !conversationUI.ready() || !verificationUI.ready() || webUI.blocksTask() || projectUI.hasDraft()) return;
    if (snapshot.history?.requests_used >= 24 || snapshot.send_budget?.requests_used >= 24) { notice("request_limit"); return; }
    const message = el("message").value;
    const intent = draftUI.fields().task_intent;
    const max = window.PactTrailConversationUI.inputLimit(snapshot, intent);
    if (!message.length) { text("notice", "先选择或输入一个样例问题。"); show("notice", true); return; }
    if (count(message) > max) { text("notice", window.PactTrailConversationUI.messageOverflow(snapshot, intent)); show("notice", true); return; }
    if (snapshot.answer_mode === "ordinary") {
      if (!validOrdinaryText(message, max)) { notice("invalid_request"); return; }
    } else if (!snapshot.prompts.includes(message) || (snapshot.mode === "local_model"
        && message !== snapshot.prompts[snapshot.connection.calls])) { notice("sample_only"); return; }
    let id;
    try { id = requestId(); } catch (_) { locked = true; notice("state_unavailable"); updateControls(); return; }
    messageView.follow();
    return action("/send", { request_id: id, message, ...followupUI.fields(), ...draftUI.fields(), ...searchUI.fields(), ...inputTools?.fields?.(),
      ...(taskMode(snapshot) && snapshot.material ? { material_sha256: snapshot.material.sha256 } : {}),
      ...(taskMode(snapshot) && snapshot.project ? { project_sha256: snapshot.project.sha256 } : {}) }, true);
  }

  el("composer").addEventListener("submit", send);
  el("task-use-material").addEventListener("change", updateControls);
  el("material-file").addEventListener("change", chooseMaterial);
  el("material-cancel").addEventListener("click", () => { clearMaterialDraft(); renderMaterial(); updateControls(); });
  el("material-attach").addEventListener("click", () => {
    if (!materialDraft || !snapshot || el("material-attach").disabled) return;
    if (materialDraft.conversation_id !== snapshot.conversation_id || materialDraft.revision !== snapshot.revision) {
      clearMaterialDraft(); renderMaterial(); updateControls(); return;
    }
    return action("/material", { action: "attach", name: materialDraft.name, data: materialDraft.data });
  });
  el("material-remove").addEventListener("click", () => {
    if (!snapshot || !snapshot.material || el("material-remove").disabled) return;
    return action("/material", { action: "remove" });
  });
  el("material-search").addEventListener("input", findMaterial);
  el("material-search").addEventListener("paste", (event) => {
    if (el("material-search").disabled || !searchableMaterial()) return;
    // Validate this explicit paste before the input strips CR/LF.
    const pasted = event.clipboardData && event.clipboardData.getData("text/plain");
    if (typeof pasted !== "string") return;
    const input = el("material-search");
    let invalid = pasted.length > 256 || count(pasted) > 128
      || /[\u0000-\u001f\u007f\ud800-\udfff]/u.test(pasted);
    if (!invalid) {
      const start = Number.isInteger(input.selectionStart) ? input.selectionStart : input.value.length;
      const end = Number.isInteger(input.selectionEnd) ? input.selectionEnd : start;
      invalid = input.value.length - (end - start) + pasted.length > 256;
      if (!invalid) {
        const candidate = input.value.slice(0, start) + pasted + input.value.slice(end);
        invalid = count(candidate) > 128 || /[\u0000-\u001f\u007f\ud800-\udfff]/u.test(candidate);
      }
    }
    if (!invalid) return;
    event.preventDefault();
    clearMaterialSearch();
    materialSearchError = materialSearchInvalid;
    renderMaterial(); updateControls();
  });
  el("material-search").addEventListener("keydown", (event) => {
    if (event.isComposing || !["Enter", "Escape"].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.key === "Escape") {
      clearMaterialSearch(); renderMaterial(); updateControls();
    } else moveMaterialMatch(event.shiftKey ? -1 : 1);
  });
  el("material-match-previous").addEventListener("click", () => moveMaterialMatch(-1));
  el("material-match-next").addEventListener("click", () => moveMaterialMatch(1));
  el("material-search-clear").addEventListener("click", () => {
    if (el("material-search-clear").disabled) return;
    clearMaterialSearch(); renderMaterial(); updateControls(); el("material-search").focus();
  });
  for (const [name, delta] of [["material-previous", -1], ["material-next", 1]]) {
    el(name).addEventListener("click", () => {
      if (el(name).disabled) return;
      materialPage += delta; renderMaterial(); updateControls();
    });
  }
  el("message").addEventListener("input", updateControls);
  el("message").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) send(event);
  });
  el("refresh").addEventListener("click", refresh);
  el("more-prompts").addEventListener("click", () => {
    extraPrompts = !extraPrompts;
    Array.from(el("prompts").children).forEach((button, index) => { button.hidden = index >= 3 && !extraPrompts; });
    el("more-prompts").setAttribute("aria-expanded", String(extraPrompts));
    text("more-prompts", extraPrompts ? "收起样例" : "更多样例");
  });
  el("new-chat").addEventListener("click", () => snapshot && snapshot.history
    ? historyAction("new", {}, true) : action("/reset", { provider_id: snapshot && (snapshot.connection ? "offline" : snapshot.provider_id) }, true));
  el("history-search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const query = el("history-query").value;
    if (count(query) > 80) { notice("invalid_request"); return; }
    return historyAction("search", { query });
  });
  el("history-title-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const title = el("history-title").value.trim();
    if (!titleText(title)) { text("notice", "会话名称需为 1 至 80 个字符。"); show("notice", true); return; }
    return historyAction("rename", { title });
  });
  el("history-save").addEventListener("click", () => historyAction("save"));
  el("history-export").addEventListener("click", () => historyAction("export"));
  el("history-delete").addEventListener("click", () => {
    if (locked || mutating || !snapshot || !snapshot.history) return;
    deleteTarget = { id: snapshot.conversation_id, revision: snapshot.revision };
    text("history-delete-label", "删除“" + snapshot.history.current_title + "”？");
    show("history-delete-confirm", true);
    el("history-delete-no").focus();
  });
  el("history-delete-no").addEventListener("click", () => { clearDelete(); el("history-delete").focus(); });
  el("history-delete-yes").addEventListener("click", () => {
    if (!deleteTarget || !snapshot || deleteTarget.id !== snapshot.conversation_id
        || deleteTarget.revision !== snapshot.revision) { clearDelete(); return; }
    return historyAction("delete", {}, true);
  });
  el("provider").addEventListener("change", () => {
    if (!snapshot) return;
    if (snapshot.connection || taskMode(snapshot)) { el("provider").value = snapshot.provider_id; return; }
    const provider = el("provider").value;
    if (!providers.includes(provider) || provider === snapshot.provider_id) { el("provider").value = snapshot.provider_id; return; }
    return action("/reset", { provider_id: provider }, true);
  });
  el("skip-link").addEventListener("click", () => {
    el("composer-fold").open = true; el("message").focus();
  });
  el("recover-state").addEventListener("click", () => el("refresh").click());
  el("stop").addEventListener("click", () => {
    if (!snapshot || !snapshot.operation || snapshot.operation.status !== "running") return;
    return action("/stop", { request_id: snapshot.operation.id });
  });
  for (const [name, toggleId, closeId, media] of [
    ["sidebar", "sidebar-toggle", "sidebar-close", wideSidebar],
    ["inspector", "sources-toggle", "sources-close", wideInspector],
  ]) {
    el(toggleId).addEventListener("click", () => {
      const opening = el(toggleId).getAttribute("aria-expanded") !== "true";
      if (opening && !media.matches && !(name === "sidebar" ? wideInspector : wideSidebar).matches) {
        setPanel(name === "sidebar" ? "inspector" : "sidebar", false);
      }
      setPanel(name, opening, true);
    });
    el(closeId).addEventListener("click", () => setPanel(name, false, true));
    media.addEventListener("change", (event) => {
      if (!event.matches) setPanel(name, false);
    });
    setPanel(name, false);
  }
  el("files-nav").addEventListener("click", () => {
    if (!wideSidebar.matches) setPanel("sidebar", false);
    selectTab("materials"); setPanel("inspector", true, true);
  });
  el("task-progress-open").addEventListener("click", () => {
    const progress = window.PactTrailTaskUI.progress(snapshot);
    if (el("task-progress-open").disabled || !progress) return;
    const draft = window.PactTrailDraftUI.effectiveDraft(snapshot);
    if (["review", "applied"].includes(progress.phase) && draft?.artifact
        && window.PactTrailDraftUI.requestReview(draft.run_id, draft.artifact.sha256)) return;
    if (!wideSidebar.matches) setPanel("sidebar", false);
    selectTab("materials"); setPanel("inspector", true, true);
  });
  el("task-progress-reconcile")?.addEventListener("click", () => {
    if (!el("task-progress-reconcile").disabled) return refresh();
  });
  el("task-progress-compose")?.addEventListener("click", () => {
    if (el("task-progress-compose").disabled || el("message").disabled) return;
    el("composer-fold").open = true; el("message").focus();
  });
  for (const name of ["materials", "session"]) {
    el("tab-" + name).addEventListener("click", () => selectTab(name));
    el("tab-" + name).addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      selectTab(event.key === "Home" ? "materials" : event.key === "End" ? "session"
        : name === "materials" ? "session" : "materials", true);
    });
  }
  selectTab("materials");
  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented || el("command-palette")?.open || el("draft-review-dialog")?.open) return;
    if (!wideInspector.matches && !el("inspector").inert) setPanel("inspector", false, true);
    else if (!wideSidebar.matches && !el("sidebar").inert) setPanel("sidebar", false, true);
  });
  window.addEventListener("pagehide", () => {
    inputTools?.close();
    sendPreview.close();
    messageView.close();
    composerDrafts.close();
    composerUI.close();
    agentHistoryUI.close();
    window.PactTrailTaskHistory?.close();
    window.PactTrailTaskUI.close();
    conversationUI.close();
    verificationUI.close();
    connectionUI.leave();
    cancelRead();
    webUI.invalidate();
    clearMaterialDraft();
    projectUI.clear();
    clearMaterialSearch();
    locked = true;
    renderMaterial();
    projectUI.render();
    updateControls();
  });
  if (/^[a-f0-9]{64}$/.test(capability)) refresh();
  else { notice("access_denied"); updateControls(); }
})();
