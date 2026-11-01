(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const fields = (value, names) => value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
  const integer = (value, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(value) && value >= 0 && value <= max;
  const optional = value => value === null || integer(value);
  const text = (value, max) => typeof value === "string" && Array.from(value).length <= max
    && !/[\u0000-\u0008\u000b-\u001f\u007f\ud800-\udfff]/u.test(value);
  const codes = {
    busy: "当前任务仍在处理，请稍后发送。", closed: "服务已关闭。", stale_request: "状态已变化，请刷新后核对。",
    turn_limit: "本段尝试已满，请新建或切换会话。", context_limit: "本次上下文已满，请调整历史参考或切换会话。",
    request_limit: "本次启动提问额度已满，请重开服务后继续。", history_limit: "保存空间已满，请使用新的工作区。",
    completion_request_limit: "本次启动模型调用已满，请重开服务后继续。",
    message_limit: "当前输入超过此任务上限，请缩短后发送。", history_unavailable: "历史记录暂不可用，请核对保存目录。",
    token_limit: "本次内容过长，请缩短问题或新建会话。",
    budget_limit: "剩余费用不足以预留本次调用。", usage_unavailable: "费用或用量尚未核清，请先核对状态。",
    provider_unavailable: "模型连接尚未就绪，请核对配置。", live_scope_required: "请先配置并启用模型。",
    scope_required: "请先核对并确认文件范围。", source_unavailable: "文件快照不可用，请重新核对范围。",
    artifact_reference_required: "请重新查看并关联成果原文。",
    context_summary_stale: "摘要来源已变化，请重核或撤回摘要。",
    invalid_request: "请核对输入内容、任务类型和文件范围。", state_unavailable: "状态暂不可用，请刷新后核对。",
  };
  const code = value => typeof value === "string" && /^[a-z_]{1,64}$/.test(value);
  const pair = (value, maximum) => fields(value, ["used", "limit"]) && integer(value.used, maximum)
    && integer(value.limit, maximum) && value.limit > 0;
  const identifier = value => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  function validPlan(plan, value) {
    if (!fields(plan, ["version", "question", "history", "sources", "first_request",
      ...["artifact_reference", "summary_plan", "duplicates"].filter(name => Object.hasOwn(plan || {}, name))]) || plan.version !== 1) return false;
    const { question, history, sources, first_request: first } = plan;
    const artifact = plan.artifact_reference;
    if (artifact && (!fields(artifact, ["task_id", "run_id", "kind", "name", "sha256", "bytes", "content_bytes", "confirmed", "reference"])
      || !identifier(artifact.task_id) || !identifier(artifact.run_id) || !["report", "change_draft"].includes(artifact.kind)
      || !text(artifact.name, 64) || !artifact.name.length || !hash(artifact.sha256)
      || !integer(artifact.bytes, 1048576) || !integer(artifact.content_bytes, 8192) || !artifact.content_bytes
      || artifact.bytes < artifact.content_bytes || typeof artifact.confirmed !== "boolean"
      || !fields(artifact.reference, ["kind", "task_id", "run_id", "sha256"]) || artifact.reference.kind !== "artifact"
      || artifact.reference.task_id !== artifact.task_id || artifact.reference.run_id !== artifact.run_id
      || artifact.reference.sha256 !== artifact.sha256)) return false;
    if (Object.hasOwn(plan, "artifact_reference") && !artifact) return false;
    if (!fields(question, ["bytes", "chars", "limit_chars", "excess_chars", "reference"])
      || !integer(question.bytes, 16384) || question.chars !== value.message_chars || question.limit_chars !== value.message_limit
      || question.excess_chars !== Math.max(0, question.chars - question.limit_chars)
      || !fields(question.reference, ["kind"]) || question.reference.kind !== "question"
      || !fields(history, ["bytes", "limit_bytes", "excess_bytes", "framing_bytes", "excluded_turns", "items",
        ...(Object.hasOwn(history || {}, "unselected_turns") ? ["unselected_turns"] : []),
        ...(artifact ? ["artifact_reference_bytes"] : [])])
      || history.bytes !== value.context.bytes || history.limit_bytes !== value.context.limit
      || history.excess_bytes !== Math.max(0, history.bytes - history.limit_bytes)
      || !integer(history.framing_bytes, 1048576) || !integer(history.excluded_turns, 32)
      || Object.hasOwn(history, "unselected_turns") && (!integer(history.unselected_turns)
        || history.unselected_turns > history.excluded_turns)
      || !Array.isArray(history.items) || history.items.length !== value.context.successful_turns
      || history.items.length + history.excluded_turns !== value.attempts.used
      || !history.items.every(item => fields(item, ["run_id", "intent", "bytes", "question_bytes", "answer_bytes",
        "artifact_bytes", "application_bytes", "search_bytes", "reference",
        ...(Object.hasOwn(item, "representation") ? ["representation", "summary_id", "summary_version", "original_bytes", "summary_bytes", "state"] : [])])
        && identifier(item.run_id) && ["answer", "report", "change_draft"].includes(item.intent)
        && ["bytes", "question_bytes", "answer_bytes", "artifact_bytes", "application_bytes", "search_bytes"].every(name => integer(item[name], 1048576))
        && fields(item.reference, ["kind", "run_id"]) && item.reference.kind === "history" && item.reference.run_id === item.run_id
        && (!Object.hasOwn(item, "representation") || item.representation === "summary" && identifier(item.summary_id)
          && integer(item.summary_version, 16) && item.summary_version > 0 && integer(item.original_bytes, 1048576)
          && integer(item.summary_bytes, 2048) && item.summary_bytes > 0 && ["current", "stale"].includes(item.state)
          && ["question_bytes", "answer_bytes", "artifact_bytes", "application_bytes", "search_bytes"].every(name => item[name] === 0)))
      || new Set(history.items.map(item => item.run_id)).size !== history.items.length
      || artifact && history.artifact_reference_bytes !== artifact.bytes
      || history.bytes !== history.framing_bytes + history.items.reduce((sum, item) => sum + item.bytes, 0) + (artifact?.bytes || 0)
      || !fields(sources, ["sha256", "confirmed", "manifest_bytes", "content_bytes", "first_request", "later", "items"])
      || typeof sources.confirmed !== "boolean"
      || !integer(sources.manifest_bytes, 1048576) || !integer(sources.content_bytes, 8192)
      || sources.first_request !== value.sources.content || !Array.isArray(sources.items)
      || sources.items.length !== value.sources.files.length
      || !sources.items.every((item, index) => fields(item, ["file_id", "path", "sha256", "manifest_bytes", "content_bytes", "reference"])
        && /^file-[1-4]$/.test(item.file_id) && item.path === value.sources.files[index] && hash(item.sha256)
        && integer(item.manifest_bytes, 1048576) && integer(item.content_bytes, 8192)
        && fields(item.reference, ["kind", "sha256", "file_id", "path"]) && item.reference.kind === "source"
        && item.reference.sha256 === sources.sha256 && item.reference.file_id === item.file_id && item.reference.path === item.path)
      || sources.content_bytes !== sources.items.reduce((sum, item) => sum + item.content_bytes, 0)
      || sources.manifest_bytes < sources.items.reduce((sum, item) => sum + item.manifest_bytes, 0)
      || new Set(sources.items.map(item => item.file_id)).size !== sources.items.length
      || !(sources.first_request === "none" ? sources.sha256 === null && !sources.confirmed && sources.later === "none" && sources.manifest_bytes === 0 && sources.items.length === 0
        : hash(sources.sha256) && sources.later === "tool_read_only" && sources.items.length > 0)
      || !fields(first, ["bytes", "other_bytes", "search", "input_limit_tokens", "excess_input_tokens",
        ...["history_wire_bytes", "shared_bytes", "sharing_overhead_bytes"].filter(name => Object.hasOwn(first, name))])
      || !optional(first.bytes) || !optional(first.other_bytes) || !optional(first.input_limit_tokens) || !optional(first.excess_input_tokens)
      || first.input_limit_tokens === 0
      || (first.excess_input_tokens !== null && (first.input_limit_tokens === null || value.model.estimated_input_tokens === null
        || first.excess_input_tokens !== Math.max(0, value.model.estimated_input_tokens - first.input_limit_tokens)))
      || (first.bytes === null) !== (first.other_bytes === null)
      || !["none", "acquired_at_send"].includes(first.search)
      || (first.search === "acquired_at_send") !== Object.hasOwn(value, "search")
      || (Object.hasOwn(first, "history_wire_bytes") !== Object.hasOwn(first, "shared_bytes"))
      || (Object.hasOwn(first, "history_wire_bytes") !== Object.hasOwn(first, "sharing_overhead_bytes"))
      || Object.hasOwn(first, "history_wire_bytes") && (!optional(first.history_wire_bytes) || !optional(first.shared_bytes)
        || !optional(first.sharing_overhead_bytes)
        || (first.history_wire_bytes === null) !== (first.bytes === null) || (first.shared_bytes === null) !== (first.bytes === null)
        || (first.sharing_overhead_bytes === null) !== (first.bytes === null)
        || first.bytes !== null && (first.history_wire_bytes + first.shared_bytes !== history.bytes + first.sharing_overhead_bytes
          || first.shared_bytes > 0 && first.sharing_overhead_bytes > 0))
      || (first.bytes !== null && first.bytes !== question.bytes + history.bytes + sources.manifest_bytes + first.other_bytes
        - (first.shared_bytes || 0) + (first.sharing_overhead_bytes || 0))) return false;
    if (Object.hasOwn(plan, "summary_plan") && !window.PactTrailHistoryReferenceUI?.validSummaryPlan(plan.summary_plan, value.conversation_id)) return false;
    for (const item of history.items) if (item.representation === "summary") {
      const record = plan.summary_plan?.items.find(record => record.id === item.summary_id && record.adopted);
      if (!record || record.run_id !== item.run_id || record.summary_version !== item.summary_version || record.state !== item.state
        || new TextEncoder().encode(record.text).byteLength !== item.summary_bytes) return false;
    }
    if (Object.hasOwn(plan, "duplicates") && !validDuplicates(plan.duplicates, plan)) return false;
    return true;
  }
  function validDuplicates(items, plan) {
    const reference = value => {
      if (value?.kind === "history") return fields(value, ["kind", "run_id"]) && plan.history.items.some(item => item.run_id === value.run_id);
      if (value?.kind === "source") return fields(value, ["kind", "sha256", "file_id", "path"])
        && plan.sources.items.some(item => item.reference.sha256 === value.sha256 && item.file_id === value.file_id && item.path === value.path);
      return value?.kind === "artifact" && fields(value, ["kind", "task_id", "run_id", "sha256"])
        && plan.artifact_reference?.task_id === value.task_id && plan.artifact_reference?.run_id === value.run_id
        && plan.artifact_reference?.sha256 === value.sha256;
    };
    return Array.isArray(items) && items.length <= 16 && items.every(item => fields(item,
      ["kind", "sha256", "content_bytes", "wire_saving_bytes", "recommendation", "references"])
      && ["history_answer", "history_artifact", "source_content", "shared_artifact"].includes(item.kind)
      && hash(item.sha256) && integer(item.content_bytes, 1048576) && item.content_bytes > 0
      && [null, 0].includes(item.wire_saving_bytes)
      && item.recommendation === (item.kind === "source_content" ? "review_selected_files"
        : item.kind === "shared_artifact" ? "already_shared" : "review_selected_history")
      && (item.kind !== "shared_artifact" || item.wire_saving_bytes === 0)
      && Array.isArray(item.references) && item.references.length >= 2 && item.references.length <= 40
      && new Set(item.references.map(value => JSON.stringify(value))).size === item.references.length
      && item.references.every(reference));
  }
  function valid(value) {
    if (!fields(value, ["version", "mode", "conversation_id", "revision", "message_chars", "message_limit", "answer_chars",
      "attempts", "context", "requests", "model", "sources", "blockers", "estimate_error",
      ...(Object.hasOwn(value || {}, "search") ? ["search"] : []),
      ...(Object.hasOwn(value || {}, "context_plan") ? ["context_plan"] : [])])
      || value.version !== 1 || value.mode !== "send_preview" || !/^[a-f0-9]{32}$/.test(value.conversation_id)
      || !integer(value.revision, 2147483647) || !integer(value.message_chars, 4096)
      || !integer(value.message_limit, 4096) || !value.message_limit || !integer(value.answer_chars, 4096) || !value.answer_chars
      || !pair(value.attempts, 32) || !pair(value.requests, 24) || value.requests.limit !== 24
      || !fields(value.context, ["bytes", "limit", "successful_turns"]) || !integer(value.context.bytes, 1048576)
      || !integer(value.context.limit, 65536) || value.context.limit < 1024 || !integer(value.context.successful_turns, 32)
      || !fields(value.model, ["calls_remaining", "estimated_input_tokens", "output_reserved_tokens", "reservation_nano_usd",
        "budget_remaining_nano_usd", "cost_known", "token_basis", "pricing_basis"])
      || !optional(value.model.calls_remaining) || (value.model.calls_remaining !== null && value.model.calls_remaining > 48)
      || !optional(value.model.estimated_input_tokens) || !optional(value.model.output_reserved_tokens)
      || (value.model.output_reserved_tokens !== null && value.model.output_reserved_tokens > 4096)
      || !optional(value.model.reservation_nano_usd) || !optional(value.model.budget_remaining_nano_usd)
      || ![null, true, false].includes(value.model.cost_known)
      || !(value.model.token_basis === null || text(value.model.token_basis, 80))
      || !(value.model.pricing_basis === null || text(value.model.pricing_basis, 80))
      || !fields(value.sources, ["history_turns", "files", "content"]) || !integer(value.sources.history_turns, 32)
      || !["manifest_only", "none"].includes(value.sources.content) || !Array.isArray(value.sources.files)
      || value.sources.files.length > 4 || !value.sources.files.every(path => text(path, 320) && path.length > 0)
      || !Array.isArray(value.blockers) || value.blockers.length > 16 || !value.blockers.every(code)
      || !(value.estimate_error === null || code(value.estimate_error))) return false;
    if (Object.hasOwn(value, "search")) {
      const search = value.search;
      if (!fields(search, ["provider", "max_calls", "reservation_nano_usd", "pricing_basis", "model_estimate_excludes_search"])
        || !["bing", "deepseek"].includes(search.provider) || search.max_calls !== 2
        || search.model_estimate_excludes_search !== true
        || search.reservation_nano_usd !== (search.provider === "deepseek" ? 1500000 : 0)
        || search.pricing_basis !== (search.provider === "deepseek" ? "deepseek-search-per-success-0.00075-usd" : "no-api-fee")) return false;
    }
    if (Object.hasOwn(value, "context_plan") && !validPlan(value.context_plan, value)) return false;
    return true;
  }
  const kib = value => Number((value / 1024).toFixed(1)).toString();
  const number = value => value === null ? "未知" : String(value);
  const money = value => value === null ? "未知" : "$" + (value / 1000000000).toFixed(6);
  function create({ state, fields: inputFields, request, locate }) {
    let owner = null, generation = 0, pending = false, controller = null, result = null, closed = false;
    let planRows = new Map(), usageRows = new Map();
    const enabled = data => Boolean(data?.agent_history_available && data.conversation
      && Number.isSafeInteger(data.conversation.max_context_bytes));
    const identity = () => {
      const { snapshot, locked, mutating } = state();
      return JSON.stringify([snapshot?.conversation_id, snapshot?.revision, inputFields(), locked, mutating,
        snapshot?.busy, snapshot?.send_budget, snapshot?.conversation?.scope,
        snapshot?.conversation?.context_bytes, snapshot?.conversation?.artifact_reference, snapshot?.conversation?.summary_plan,
        snapshot?.connection, snapshot?.web_search_provider]);
    };
    function resetPlan() {
      el("send-preview-plan-items").replaceChildren(); planRows = new Map(); el("send-preview-plan").hidden = true;
      el("send-preview-usage-items").replaceChildren(); usageRows = new Map();
      for (const id of ["send-preview-plan-summary", "send-preview-plan-scope", "send-preview-plan-limit", "send-preview-usage-limit"]) el(id).textContent = "";
      el("send-preview-plan-limit").hidden = true; el("send-preview-usage-limit").hidden = true;
      if (el("send-preview-duplicates")) { el("send-preview-duplicates").hidden = true; el("send-preview-duplicate-items").replaceChildren(); }
    }
    function clear(message) {
      generation += 1;
      if (controller) controller.abort();
      controller = null; pending = false; result = null;
      el("send-preview-values").replaceChildren(); el("send-preview-context").replaceChildren();
      resetPlan();
      el("send-preview-status").textContent = message;
    }
    const row = (label, value) => {
      const term = document.createElement("dt"), detail = document.createElement("dd");
      term.textContent = label; detail.textContent = value;
      el("send-preview-values").appendChild(term); el("send-preview-values").appendChild(detail);
    };
    function paintPlan(value) {
      const plan = value.context_plan, section = el("send-preview-plan");
      section.hidden = !plan;
      if (!plan) { resetPlan(); return; }
      const { question, history, sources, first_request: first } = plan, entries = [], originals = [];
      const add = (key, label, bytes, detail, reference = null, original = false) => {
        entries.push({ key, label, bytes, detail });
        if (reference || original) originals.push({ key, label, reference });
      };
      add("question", "本次问题", question.bytes, question.chars + "/" + question.limit_chars + " 字符", question.reference);
      history.items.forEach((item, index) => {
        if (item.representation === "summary") {
          add("history:" + item.run_id, "成功历史 " + (index + 1) + " · 摘要 v" + item.summary_version,
            item.bytes, "摘要正文 " + item.summary_bytes + " 字节；原记录 " + item.original_bytes + " 字节；含绑定与 JSON 编码"
              + (item.state === "stale" ? "；来源已变化，需重核或撤回" : ""), item.reference);
          return;
        }
        const parts = ["问题 " + item.question_bytes, "回答 " + item.answer_bytes];
        if (item.artifact_bytes) parts.push("成果 " + item.artifact_bytes);
        if (item.application_bytes) parts.push("应用收据 " + item.application_bytes);
        if (item.search_bytes) parts.push("搜索来源 " + item.search_bytes);
        add("history:" + item.run_id, "成功历史 " + (index + 1) + " · " + ({ answer: "问答", report: "报告", change_draft: "草案" }[item.intent]),
          item.bytes, parts.join(" · ") + " 字节；本行含记录字段与 JSON 编码", item.reference);
      });
      const artifact = plan.artifact_reference;
      if (artifact) add("artifact:" + artifact.task_id + ":" + artifact.run_id, "成果原文 · " + artifact.name,
        artifact.bytes, artifact.confirmed ? "已明确关联，仅作参考；原授权不继承" : "待重新查看并关联", artifact.reference);
      add("history-frame", "历史固定前缀与包络", history.framing_bytes,
        (artifact ? "历史与成果参考合计 " : "成功历史合计 ") + history.bytes + "/" + history.limit_bytes + " 字节"
        + (history.unselected_turns ? "；" + history.unselected_turns + " 轮成功历史未选入本次参考" : "")
        + (history.excluded_turns > (history.unselected_turns || 0) ? "；" + (history.excluded_turns - (history.unselected_turns || 0))
          + " 次失败、停止或未完成尝试保留在记录，不作为成功历史发送" : ""));
      for (const item of sources.items) add("source:" + item.file_id,
        (sources.confirmed ? "已确认文件 · " : "待确认快照清单 · ") + item.path, item.manifest_bytes,
        sources.confirmed ? "首请求仅此文件的清单记录；正文 " + item.content_bytes + " 字节，后续工具按需读取"
          : "待确认快照的清单记录；正文 " + item.content_bytes + " 字节，核对并重新确认范围后才可供后续工具读取",
        sources.confirmed ? item.reference : null, true);
      if (sources.first_request === "manifest_only") add("source-frame", "文件清单包络",
        sources.manifest_bytes - sources.items.reduce((sum, item) => sum + item.manifest_bytes, 0),
        "首请求文件清单合计 " + sources.manifest_bytes + " 字节；"
        + (sources.confirmed ? "授权正文" : "待确认快照正文") + "合计 " + sources.content_bytes + " 字节");
      if (first.other_bytes !== null) add("request-frame", "系统指引、工具定义与请求编码", first.other_bytes,
        "含固定指引、工具定义、消息结构及 JSON 转义；合计扣除无损共享，并加入正文引用的绑定开销");
      if (first.shared_bytes) add("shared", "无损共享扣除", -first.shared_bytes,
        "从逻辑参考合计扣除 " + first.shared_bytes + " 字节，得到实际请求合计；未删除原文、摘要或来源");
      if (first.sharing_overhead_bytes) add("sharing-overhead", "正文共享绑定", first.sharing_overhead_bytes,
        "短正文的引用指针开销已计入实际请求，不作为节省");
      const list = el("send-preview-plan-items"), next = new Map();
      for (const entry of originals) {
        let node = planRows.get(entry.key);
        if (!node) {
          const item = document.createElement("li"), button = document.createElement("button");
          button.type = "button"; item.appendChild(button);
          node = { item, button, reference: null };
          button.addEventListener("click", () => {
            if (!closed && result?.context_plan && owner === identity() && planRows.get(entry.key) === node
              && node.reference) {
              if (node.reference.kind === "question") { el("composer-fold").open = true; el("message").focus(); }
              else if (typeof locate === "function") locate({ ...node.reference, conversation_id: result.conversation_id, revision: result.revision });
            }
          });
        }
        node.reference = entry.reference;
        node.button.textContent = entry.label;
        node.button.disabled = !entry.reference || (entry.reference.kind !== "question" && typeof locate !== "function");
        next.set(entry.key, node);
      }
      const sameOrder = planRows.size === next.size && [...planRows.keys()].every((key, index) => key === originals[index].key);
      if (!sameOrder) list.replaceChildren(...[...next.values()].map(node => node.item));
      planRows = next;
      const usage = el("send-preview-usage-items"), nextUsage = new Map();
      for (const entry of entries) {
        let item = usageRows.get(entry.key);
        if (!item) item = document.createElement("li");
        const content = entry.label + "：" + entry.bytes + " 字节；" + entry.detail;
        if (item.textContent !== content) item.textContent = content;
        nextUsage.set(entry.key, item);
      }
      if (usageRows.size !== nextUsage.size || [...usageRows.keys()].some((key, index) => key !== entries[index].key))
        usage.replaceChildren(...nextUsage.values());
      usageRows = nextUsage;
      el("send-preview-plan-summary").textContent = first.bytes === null
        ? "尚未形成可发送的首请求；以下为完整内容计划（UTF-8 字节）。"
        : "首请求共 " + first.bytes + " 字节（实际 JSON 的 UTF-8 编码；token 另行本地估算）。";
      el("send-preview-plan-scope").textContent = "完整问题、" + history.items.length
        + (Object.hasOwn(history, "unselected_turns") ? " 轮明确选择的成功历史" : " 轮成功历史")
        + (artifact ? "、1 份成果原文参考" : "")
        + (sources.items.length ? "及 " + sources.items.length + (sources.confirmed
          ? " 份已确认文件清单；文件正文按需读取。" : " 份待确认文件；请先确认范围。") : "；未关联文件。")
        + (first.search === "acquired_at_send" ? "联网来源在发送后获取。" : "");
      const summaries = history.items.filter(item => item.representation === "summary");
      if (summaries.length) el("send-preview-plan-scope").textContent += "其中 " + summaries.length + " 轮使用明确采用的摘要。";
      paintDuplicates(plan);
      const limits = [];
      if (sources.items.length && !sources.confirmed) limits.push("快照范围待重新确认；历史恢复不会自动恢复本次文件授权。");
      if (artifact && !artifact.confirmed) limits.push(codes.artifact_reference_required);
      if (plan.summary_plan?.state === "stale") limits.push(codes.context_summary_stale);
      if (question.excess_chars) limits.push("问题超过 " + question.limit_chars + " 字符上限，超出 " + question.excess_chars + " 字符。");
      if (history.excess_bytes) limits.push("完整上下文超过 " + history.limit_bytes + " 字节上限，超出 " + history.excess_bytes + " 字节。");
      if (first.excess_input_tokens) limits.push("首请求本地估算 " + value.model.estimated_input_tokens + " tokens，超过 "
        + first.input_limit_tokens + " 上限，超出 " + first.excess_input_tokens + " tokens。");
      const { reservation_nano_usd: reservation, budget_remaining_nano_usd: balance } = value.model;
      const shortage = reservation !== null && balance !== null ? Math.max(0, reservation + (value.search?.reservation_nano_usd || 0) - balance) : null;
      if (value.blockers.includes("budget_limit") && shortage) limits.push("调用与检索需预留 "
        + money(reservation + (value.search?.reservation_nano_usd || 0)) + "，余额 " + money(balance) + "，缺少 " + money(shortage) + "。");
      for (const reason of value.blockers) if (!["message_limit", "context_limit"].includes(reason)
        && !(reason === "token_limit" && first.excess_input_tokens) && !(reason === "budget_limit" && shortage))
        limits.push(codes[reason] || "存在发送限制，请核对状态。");
      el("send-preview-usage-limit").textContent = limits.join(" "); el("send-preview-usage-limit").hidden = !limits.length;
      const reason = plan.summary_plan?.state === "stale" ? "context_summary_stale" : artifact && !artifact.confirmed ? "artifact_reference_required" : sources.items.length && !sources.confirmed ? "scope_required"
        : question.excess_chars ? "message_limit" : history.excess_bytes ? "context_limit"
        : first.excess_input_tokens ? "token_limit" : value.blockers[0];
      el("send-preview-plan-limit").textContent = reason ? codes[reason] || "请核对状态后发送。" : "";
      el("send-preview-plan-limit").hidden = !reason;
    }
    function paintDuplicates(plan) {
      const section = el("send-preview-duplicates"); if (!section) return;
      const items = plan.duplicates || []; section.hidden = !items.length;
      el("send-preview-duplicate-items").replaceChildren(...items.map(item => {
        const row = document.createElement("li"), text = document.createElement("span");
        text.textContent = ({ history_answer: "相同历史回答", history_artifact: "相同历史成果", source_content: "相同文件正文", shared_artifact: "同轮关联成果" }[item.kind])
          + " · " + item.content_bytes + " B · " + (item.recommendation === "already_shared" ? "后续请求无损共享；首包保留完整参考"
            : item.recommendation === "review_selected_files" ? "可核对文件选择；首请求只含清单" : "可核对历史选择，不自动移除");
        row.appendChild(text);
        for (const reference of item.references) {
          const button = document.createElement("button"); button.type = "button"; button.className = "text-button";
          button.textContent = reference.kind === "source" ? reference.path : reference.kind === "artifact" ? "成果原文" : "历史原文";
          button.disabled = typeof locate !== "function";
          button.addEventListener("click", () => {
            if (!closed && result?.context_plan === plan && owner === identity())
              locate?.({ ...reference, conversation_id: result.conversation_id, revision: result.revision });
          }); row.appendChild(button);
        }
        return row;
      }));
    }
    function paint(value) {
      el("send-preview-values").replaceChildren();
      row("输入 / 回答上限", value.message_chars + "/" + value.message_limit + " 字符 · 回答 " + value.answer_chars + " 字符");
      row(value.context_plan?.artifact_reference ? "历史与成果参考"
        : Object.hasOwn(value.context_plan?.history || {}, "unselected_turns") ? "所选成功历史" : "完整成功历史",
        value.context.successful_turns + " 轮 · " + value.context.bytes + "/" + value.context.limit + " 字节（UTF-8，含固定前缀）");
      row("首个请求 tokens", number(value.model.estimated_input_tokens) + "（本地估算）");
      row("输出保留", number(value.model.output_reserved_tokens) + " tokens");
      row("剩余模型调用", number(value.model.calls_remaining));
      row("费用余额 / 调用需预留", money(value.model.budget_remaining_nano_usd) + " / " + money(value.model.reservation_nano_usd));
      row("费用状态", value.model.cost_known === true ? "已核清；此预览不占用额度" : "未知；不推算退款或余额");
      if (value.model.pricing_basis) row("计价依据", value.model.pricing_basis);
      if (value.search) row("联网检索费用上限", (value.search.provider === "deepseek" ? "DeepSeek Search · " : "Bing · ")
        + money(value.search.reservation_nano_usd) + "（最多两次；正式发送逐次准入）");
      if (value.blockers.length) row("当前限制", value.blockers.map(reason => codes[reason] || "暂不能发送，请刷新后核对。").join(" "));
      paintPlan(value);
      const context = el("send-preview-context"); context.replaceChildren();
      if (!value.context_plan) {
        const scope = document.createElement("p");
        scope.textContent = "完整问题、" + value.sources.history_turns + " 轮成功历史"
          + (value.sources.content === "manifest_only" ? "及文件清单；正文由工具按需读取。" : "；本次未关联文件。");
        context.appendChild(scope);
        if (value.sources.files.length) {
          const list = document.createElement("ul");
          for (const path of value.sources.files) { const item = document.createElement("li"); item.textContent = path; list.appendChild(item); }
          context.appendChild(list);
        }
      }
      row("估算说明", (value.search ? "模型 token 估算不含尚未取得的搜索来源；检索与模型共用总预算。" : "")
        + "后续工具观察和调用费用尚未产生。失败或中断也计尝试；满额保留全文并拒绝继续。");
      el("send-preview-status").textContent = value.estimate_error ? "部分信息暂不可用。"
        : value.blockers.length ? "请先处理发送限制。" : "已核对。";
    }
    function blocker(data) {
      const value = data.conversation, selected = value.conversations.find(item => item.id === value.selected);
      const input = inputFields(), limit = input.task_intent === "answer" ? data.limits.max_message_chars : 2048;
      if (Array.from(input.message).length > limit) return "message_limit";
      if (value.error) return value.error;
      if (value.context_bytes > value.max_context_bytes) return "context_limit";
      if (selected?.turns >= value.max_turns) return "turn_limit";
      if (data.send_budget?.requests_used >= 24) return "request_limit";
      if (["review_required", "unavailable"].includes(value.scope.state)) return "scope_required";
      if (value.artifact_reference?.state === "review_required") return "artifact_reference_required";
      if (value.summary_plan?.state === "stale") return "context_summary_stale";
      if (data.connection?.cost_known === false) return "usage_unavailable";
      if (data.connection?.calls >= 48) return "completion_request_limit";
      if (data.connection?.stopped) return data.connection.stop_reason || "provider_unavailable";
      if (data.connection && !data.connection.can_send && !data.busy) return "live_scope_required";
      return result?.blockers[0] || null;
    }
    function render() {
      const { snapshot, locked, mutating } = state(), active = enabled(snapshot);
      el("send-preview").hidden = !snapshot;
      el("send-budget-line").hidden = !active;
      el("send-preview-summary").textContent = "本次参考";
      for (const id of ["send-preview-refresh", "send-preview-status", "send-preview-values", "send-preview-context", "send-preview-note"])
        el(id).hidden = !active;
      if (!active) {
        if (owner !== null) clear("点击核对，查看本次参考。");
        owner = null; el("send-preview-refresh").disabled = true; el("send-preview-blocker").hidden = true; return;
      }
      const next = identity();
      if (owner !== next) { clear(owner === null ? "点击核对，查看本次参考。" : "内容已变化，请重新核对。"); owner = next; }
      const value = snapshot.conversation, selected = value.conversations.find(item => item.id === value.selected);
      const metrics = ["尝试 " + (selected?.turns ?? 0) + "/" + value.max_turns,
        "上下文 " + kib(value.context_bytes) + "/" + kib(value.max_context_bytes) + " KiB",
        "本次请求 " + (snapshot.send_budget?.requests_used ?? "—") + "/24"];
      const line = el("send-budget-line");
      if (line.textContent !== metrics.join(" · ")) {
        line.replaceChildren();
        metrics.forEach((metric, index) => { const span = document.createElement("span"); span.textContent = (index ? " · " : "") + metric; line.appendChild(span); });
      }
      const reason = blocker(snapshot);
      el("send-preview-blocker").textContent = reason ? codes[reason] || "当前存在限制，请展开详情核对。" : "";
      el("send-preview-blocker").hidden = !reason;
      el("send-preview-refresh").disabled = closed || pending || locked || mutating || snapshot.busy || !inputFields().message;
    }
    async function refresh() {
      render();
      if (closed || el("send-preview-refresh").disabled) return;
      const { snapshot } = state(), binding = identity(), current = ++generation;
      const body = { conversation_id: snapshot.conversation_id, revision: snapshot.revision, ...inputFields() };
      controller = new AbortController(); pending = true; result = null;
      el("send-preview-values").replaceChildren(); el("send-preview-context").replaceChildren();
      el("send-preview-status").textContent = "正在本机核对…"; render();
      try {
        const data = await request("/preview", body, controller.signal);
        if (closed || current !== generation || binding !== identity()) return;
        if (!valid(data) || data.conversation_id !== body.conversation_id || data.revision !== body.revision
          || data.message_chars !== Array.from(body.message).length
          || (data.context_plan && data.context_plan.question.bytes !== new TextEncoder().encode(body.message).length)
          || Boolean(body.web_search) !== Object.hasOwn(data, "search")
          || (data.search && data.search.provider !== (snapshot.web_search_provider || "bing")))
          throw new Error("invalid_response");
        result = data; paint(data);
      } catch (_) {
        if (!closed && current === generation && binding === identity())
          el("send-preview-status").textContent = "预览暂不可用，请重试。";
      } finally {
        if (!closed && current === generation && binding === identity()) { pending = false; controller = null; render(); }
      }
    }
    el("send-preview-refresh").addEventListener("click", refresh);
    return { render, close() { closed = true; clear("预览已关闭。"); el("send-preview-refresh").disabled = true; } };
  }
  window.PactTrailSendPreviewUI = { create, valid };
})();
