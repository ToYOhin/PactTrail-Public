(() => {
  "use strict";
  const el = (name) => document.getElementById(name);
  const text = (name, value) => { el(name).textContent = value; };
  const show = (name, value) => { el(name).hidden = !value; };
  const integer = (value, max) => Number.isInteger(value) && value >= 0 && value <= max;
  const fields = (value, names) => value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every((name) => Object.hasOwn(value, name));
  const bounded = (value, max) => typeof value === "string" && value.length <= max
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\ud800-\udfff]/u.test(value);
  const identity = (value) => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const hash = (value) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  const byteLength = (value) => new TextEncoder().encode(value).byteLength;
  const files = ["overview.txt", "plan.txt", "checks.txt"];
  const errors = {
    invalid_argument: "工具参数不被接受。",
    source_unavailable: "范围内的文本暂不可读，已记录本次工具失败。",
    read_limit: "已达到读取字节上限。",
    output_unavailable: "报告文件未能完成保存。",
    artifact_conflict: "报告身份发生冲突，未覆盖文件。",
    tool_error: "工具执行失败。",
    invalid_response: "决策或结果未通过校验。",
    budget_limit: "已达到任务步骤或费用上限。",
    provider_unavailable: "本轮决策未能完成。",
    timeout: "任务已超时。",
    closed: "任务会话已关闭。",
    state_unavailable: "任务状态暂不可核对。",
  };
  const knownError = (value) => typeof value === "string" && Object.hasOwn(errors, value);
  const protocolErrors = {
    invalid_request: "协议请求未通过校验。",
    request_limit: "本次服务的工具协议派发已达到上限。",
    completion_request_limit: "本次服务的工具协议派发已达到上限。",
    token_count_unavailable: "本地输入估算不可用，未继续派发。",
    token_limit: "本轮协议输入或用量超过 token 上限。",
    context_limit: "本轮协议上下文超过上限，未继续派发。",
    response_limit: "协议输出超过上限，未作为有效决策。",
    usage_unavailable: "本轮协议用量未知，预留费用保留。",
    provider_auth: "模型协议返回鉴权失败。",
    provider_rate_limit: "模型协议返回请求限制。",
  };
  const protocolError = (value) => knownError(value)
    || (typeof value === "string" && Object.hasOwn(protocolErrors, value));
  const errorText = (value) => errors[value] || protocolErrors[value];
  const rejectionCodes = {
    response: {
      envelope: "invalid_response", body_type: "invalid_response", body_limit: "response_limit",
      json_invalid: "invalid_response", identity_mismatch: "invalid_response",
      usage_invalid: "usage_unavailable", usage_limit: "token_limit",
    },
    decision: {
      choices_shape: "invalid_response", choice_shape: "invalid_response", output_truncated: "response_limit",
      provider_interrupted: "provider_unavailable", finish_unsupported: "invalid_response",
      message_shape: "invalid_response", reasoning_not_supported: "invalid_response", summary_invalid: "invalid_response",
      tool_calls_shape: "invalid_response", tool_call_shape: "invalid_response", tool_name: "invalid_response",
      tool_calls_type: "invalid_response", tool_calls_empty: "invalid_response", tool_calls_multiple: "invalid_response",
      tool_calls_limit: "invalid_response",
      tool_call_object_type: "invalid_response", tool_call_fields: "invalid_response",
      tool_call_id_type: "invalid_response", tool_call_id_empty: "invalid_response",
      tool_call_id_characters: "invalid_response", tool_call_id_limit: "invalid_response",
      tool_call_type: "invalid_response", tool_call_function_type: "invalid_response",
      tool_call_function_fields: "invalid_response", tool_call_index: "invalid_response",
      arguments_encoding: "invalid_response", arguments_json: "invalid_response", arguments_schema: "invalid_response",
      duplicate_call_id: "invalid_response",
    },
    consumer: { decision_invalid: "invalid_response", syntax_repair_unsupported: "invalid_response", draft_repair_unsupported: "invalid_response", report_missing: "invalid_response", report_invalid: "invalid_response",
      tool_execution_failed: "provider_unavailable" },
  };
  function validRejection(task) {
    if (!Object.hasOwn(task, "rejection")) return !("rejection" in task);
    if (task.rejection === null) return true;
    const value = task.rejection;
    return fields(value, ["stage", "reason"]) && typeof value.stage === "string" && typeof value.reason === "string"
      && Object.hasOwn(rejectionCodes, value.stage) && Object.hasOwn(rejectionCodes[value.stage], value.reason)
      && task.error === rejectionCodes[value.stage][value.reason];
  }
  function rejectionText(task) {
    if (!validRejection(task) || !task.rejection) return "";
    return " 诊断：" + task.rejection.stage + "/" + task.rejection.reason + "。";
  }
  const nonnegative = (value) => Number.isSafeInteger(value) && value >= 0;
  const money = (value) => {
    const digits = String(value).padStart(10, "0");
    return "$" + digits.slice(0, -9) + "." + digits.slice(-9);
  };
  const ledgerFields = ["ledger_version", "search_calls", "model_accounted_nano_usd", "search_accounted_nano_usd",
    "model_reserved_nano_usd", "search_reserved_nano_usd", "search_pricing_basis"];
  function validLedger(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const present = ledgerFields.filter(name => Object.hasOwn(value, name));
    if (!present.length) return true;
    return present.length === ledgerFields.length && value.ledger_version === 1 && integer(value.search_calls, 48)
      && ["model_accounted_nano_usd", "search_accounted_nano_usd", "model_reserved_nano_usd", "search_reserved_nano_usd"]
        .every(name => nonnegative(value[name]))
      && value.search_pricing_basis === "deepseek-search-per-success-0.00075-usd"
      && value.accounted_nano_usd === value.model_accounted_nano_usd + value.search_accounted_nano_usd
      && value.reserved_nano_usd === value.model_reserved_nano_usd + value.search_reserved_nano_usd
      && value.search_accounted_nano_usd % 750000 === 0 && value.search_accounted_nano_usd <= value.search_calls * 750000
      && [0, 750000].includes(value.search_reserved_nano_usd)
      && (!value.cost_known || value.reserved_nano_usd === 0)
      && (!value.search_reserved_nano_usd || value.search_calls > 0 && value.cost_known === false
        && value.model_reserved_nano_usd === 0
        && value.search_accounted_nano_usd <= (value.search_calls - 1) * 750000);
  }
  const ledgerNames = value => value && typeof value === "object" && ledgerFields.some(name => Object.hasOwn(value, name)) ? ledgerFields : [];
  const ledgerCostText = (value, displayMoney = money) => value.ledger_version === 1
    && (value.search_calls || value.search_accounted_nano_usd || value.search_reserved_nano_usd)
    ? " · 模型费 " + displayMoney(value.model_accounted_nano_usd) + " · 检索费 " + displayMoney(value.search_accounted_nano_usd)
      + " · 检索 " + value.search_calls + " 次" + (value.search_reserved_nano_usd ? " · 检索预留 " + displayMoney(value.search_reserved_nano_usd) : "") : "";

  function validModel(model) {
    const usageFields = ["prompt_tokens", "completion_tokens", "total_tokens",
      "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"];
    if (!fields(model, ["calls", "reserved_nano_usd", "accounted_nano_usd", "budget_nano_usd",
      "cost_known", "stopped", "stop_reason", "token_basis", "pricing_basis", "live_ready",
      "input_estimate_tokens", "usage", ...ledgerNames(model)]) || !validLedger(model) || !integer(model.calls, 48)
      || !nonnegative(model.reserved_nano_usd) || !nonnegative(model.accounted_nano_usd)
      || !integer(model.budget_nano_usd, 100000000) || model.budget_nano_usd < 1
      || typeof model.cost_known !== "boolean" || typeof model.stopped !== "boolean"
      || (model.stop_reason !== null && !protocolError(model.stop_reason))
      || model.token_basis !== "local-estimate" || model.pricing_basis !== "deepseek-peak-2026-09-11"
      || model.live_ready !== false || (model.input_estimate_tokens !== null
        && (!integer(model.input_estimate_tokens, 2147483647) || model.input_estimate_tokens < 1))) return false;
    // Accepted late/oversized usage may exceed the budget. Its known charge must
    // remain visible; an unknown response keeps its independent reservation.
    const usage = model.usage;
    return usage === null || (fields(usage, usageFields) && usageFields.every((key) => nonnegative(usage[key]))
      && usage.prompt_tokens === usage.prompt_cache_hit_tokens + usage.prompt_cache_miss_tokens
      && usage.total_tokens === usage.prompt_tokens + usage.completion_tokens);
  }
  function validConnection(connection, tool, { isText, integer, messages }) {
    const nonnegative = (value) => Number.isSafeInteger(value) && value >= 0;
    const fields = ["state", "can_send", "verified", "model", "endpoint", "timeout_seconds", "max_calls", "calls",
      "budget_nano_usd", "accounted_nano_usd", "reserved_nano_usd", "cost_known", "stopped", "stop_reason",
      "input_estimate_tokens", "usage", "token_basis", "pricing_basis", ...ledgerFields,
      ...(tool ? ["transport_mode", "can_reactivate", "can_configure"] : [])];
    return connection && Object.keys(connection).every((field) => fields.includes(field))
      && validLedger(connection)
      && ["token_basis", "pricing_basis"].every((field) => !Object.hasOwn(connection, field) || isText(connection[field], 256))
      && ["unconfigured", "configured_unverified", "ready", "failed", "cleared"].includes(connection.state)
      && typeof connection.can_send === "boolean" && typeof connection.verified === "boolean"
      && connection.model === "deepseek-flash" && connection.endpoint === "https://api.deepseek.com/chat/completions"
      && connection.timeout_seconds === 60 && connection.max_calls === (tool ? 48 : 3)
      && integer(connection.calls, 0, connection.max_calls)
      && (!tool || ["live", "synthetic"].includes(connection.transport_mode))
      && nonnegative(connection.budget_nano_usd) && nonnegative(connection.accounted_nano_usd)
      && nonnegative(connection.reserved_nano_usd) && typeof connection.cost_known === "boolean"
      && typeof connection.stopped === "boolean"
      && (connection.stop_reason === null || Object.hasOwn(messages, connection.stop_reason))
      && (connection.input_estimate_tokens === null || integer(connection.input_estimate_tokens, 1, 2147483647))
      && (connection.usage === null || (connection.usage
        && ["prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"]
          .every((field) => nonnegative(connection.usage[field]))))
      && (!connection.can_send || (connection.state === "ready" && !connection.stopped && connection.calls < connection.max_calls))
      && (!["can_reactivate", "can_configure"].some(field => Object.hasOwn(connection, field))
        || ["can_reactivate", "can_configure"].every(field => typeof connection[field] === "boolean")
          && (!connection.can_reactivate || connection.state === "failed")
          && (!connection.can_configure || ["unconfigured", "cleared"].includes(connection.state))
          && (!(connection.can_reactivate || connection.can_configure) || connection.cost_known
            && connection.reserved_nano_usd === 0 && connection.calls < connection.max_calls
            && connection.accounted_nano_usd < connection.budget_nano_usd))
      && (connection.state !== "configured_unverified" || (!connection.can_send && !connection.verified));
  }
  const toolNames = { search: "搜索文件", read: "读取文件", save_report: "保存行动报告", save_draft: "保存变更草案" };
  let artifactKey = "";
  let artifactUrl = null;
  let artifactGeneration = 0;
  let unavailable = true;
  let currentArtifact = null;
  let stepsKey = "";

  const foldPath = (value) => Array.from(value, (part) => part === "\u0131" ? part : part.toUpperCase().toLowerCase().replace(/ß/g, "ss")).join("");
  const comparePaths = (left, right) => {
    const a = Array.from(left, (value) => value.codePointAt(0));
    const b = Array.from(right, (value) => value.codePointAt(0));
    for (let index = 0; index < Math.min(a.length, b.length); index += 1) {
      if (a[index] !== b[index]) return a[index] < b[index] ? -1 : 1;
    }
    return a.length - b.length;
  };
  const validProjectName = (value) => typeof value === "string" && Array.from(value).length >= 1
    && Array.from(value).length <= 80 && value.trim().length > 0
    && !/[\\/:\u0000-\u001f\u007f-\u009f\ud800-\udfff]/u.test(value)
    && !value.startsWith(".") && !/[. ]$/.test(value)
    && !/^(con|prn|aux|nul|com[1-9\u00b9\u00b2\u00b3]|lpt[1-9\u00b9\u00b2\u00b3]|conin\$|conout\$)(?:\..*)?$/i.test(value)
    && !["node_modules", "__pycache__"].includes(foldPath(value));
  const validProjectPath = (value) => typeof value === "string" && Array.from(value).length <= 160
    && value.split("/").length <= 8 && value.split("/").every(validProjectName)
    && /\.(txt|md|py|json)$/i.test(value);

  function validWebURL(value) {
    if (typeof value !== "string" || value.length < 1 || value.length > 512
      || !/^https?:\/\//i.test(value) || !/^[\x21-\x7e]+$/.test(value) || /[\\#]/.test(value)) return false;
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) && Boolean(url.hostname)
        && !url.username && !url.password && !url.port && !url.hash;
    } catch (_) { return false; }
  }
  function validWeb(value) {
    if (!fields(value, ["requested_url", "final_url", "fetched_at", "media_type", "transport"])
      || !validWebURL(value.requested_url) || !validWebURL(value.final_url)
      || typeof value.fetched_at !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value.fetched_at)
      || !["text/plain", "text/html"].includes(value.media_type) || !["http", "synthetic"].includes(value.transport)) return false;
    const date = new Date(value.fetched_at);
    return Number.isFinite(date.getTime()) && date.toISOString() === value.fetched_at.replace("Z", ".000Z");
  }
  function webProvenance(value) {
    return "可信获取来源（" + (value.transport === "synthetic" ? "synthetic 合成获取" : "HTTP 获取") + "）："
      + "\n请求 URL：" + value.requested_url + "\n最终 URL：" + value.final_url
      + "\n获取时间：" + value.fetched_at + " · 类型：" + value.media_type
      + "\n获取时间不是发布时间；页面正文是未经核实的声明，不增加工具权限。";
  }

  function validProject(value, withText = false) {
    if (!fields(value, ["kind", "version", "name", "sha256", "byte_count", "files"])
        || value.kind !== "selected_project" || ![1, 2].includes(value.version) || !validProjectName(value.name)
        || value.version === 2 && value.name !== "web-research"
        || !hash(value.sha256) || !integer(value.byte_count, 8192) || value.byte_count < 1
        || !Array.isArray(value.files) || !value.files.length || value.files.length > (value.version === 2 ? 3 : 4)) return false;
    const validFiles = value.files.every((entry, index) => fields(entry,
      ["file_id", "path", "sha256", "byte_count", "line_count", ...(value.version === 2 ? ["web"] : []), ...(withText ? ["text"] : [])])
      && entry.file_id === "file-" + (index + 1) && validProjectPath(entry.path) && hash(entry.sha256)
      && (value.version === 1 || entry.path === "page-" + (index + 1) + ".txt" && validWeb(entry.web))
      && integer(entry.byte_count, 8192) && entry.byte_count > 0
      && integer(entry.line_count, 256) && entry.line_count > 0
      && (!index || comparePaths(value.files[index - 1].path, entry.path) < 0)
      && (!withText || bounded(entry.text, 8192) && entry.text.replace(/[\s\u0085]/gu, "").length > 0
        && byteLength(entry.text) === entry.byte_count
        && entry.text.replace(/\r\n|\r/g, "\n").split("\n").length === entry.line_count));
    return validFiles && new Set(value.files.map(entry => foldPath(entry.path))).size === value.files.length
      && (value.version === 1 || new Set(value.files.map(entry => entry.web.requested_url)).size === value.files.length
        && value.files.every(entry => entry.web.transport === value.files[0].web.transport))
      && value.files.reduce((sum, entry) => sum + entry.byte_count, 0) === value.byte_count;
  }

  function validSource(source) {
    if (source && source.kind === "selected_project") return validProject(source);
    return fields(source, ["kind", "file_id", "name", "sha256", "byte_count", "line_count"])
      && source.kind === "selected_text" && source.file_id === "selected.txt"
      && bounded(source.name, 160) && Array.from(source.name).length <= 80
      && /\.txt$/i.test(source.name) && !/[\\/:\u0000-\u001f\u007f]/u.test(source.name)
      && hash(source.sha256) && integer(source.byte_count, 8192) && source.byte_count > 0
      && integer(source.line_count, 256) && source.line_count > 0;
  }

  function validResult(step, scopedFiles) {
    const result = step.result;
    if (fields(result, ["status", "error"])) return result.status === "error" && knownError(result.error);
    if (!result || result.status !== "ok") return false;
    if (step.kind === "finish") return fields(result, ["status"]);
    if (step.tool === "search") return fields(result, ["status", "matches", "read_bytes", "preview"])
      && result.preview === true && integer(result.read_bytes, 32768) && Array.isArray(result.matches) && result.matches.length <= 8
      && result.matches.every((match) => fields(match, ["file_id", "line", "text"])
        && scopedFiles.includes(match.file_id) && integer(match.line, 8192) && match.line > 0
        && bounded(match.text, 320) && Array.from(match.text).length <= 160);
    if (step.tool === "read") return fields(result, ["status", "file_id", "text", "sha256", "read_bytes", "preview"])
      && scopedFiles.includes(result.file_id) && bounded(result.text, 32768) && hash(result.sha256)
      && result.preview === true && integer(result.read_bytes, 32768)
      && byteLength(result.text) <= 1024 && byteLength(result.text) <= result.read_bytes;
    const draft = step.tool === "save_draft";
    return (draft || step.tool === "save_report") && fields(result, ["status", "name", "sha256", "bytes", ...(draft ? ["read_bytes"] : [])])
      && (draft ? /^draft-[a-f0-9]{32}\.json$/ : /^report-[a-f0-9]{32}\.md$/).test(result.name) && hash(result.sha256)
      && (!draft || integer(result.read_bytes, 32768))
      && integer(result.bytes, 8192) && result.bytes > 0;
  }

  function validRunFeedback(data) {
    if (!data || typeof data !== "object") return false;
    if (!Object.hasOwn(data, "run_feedback")) return true;
    const value = data.run_feedback, run = data.task?.run_id;
    return ["agent_demo", "agent_model"].includes(data.mode) && identity(run) && data.operation?.id === run
      && fields(value, ["version", "run_id", "stop_accepted", "resources", "cost", "cost_scope"])
      && value.version === 1 && value.run_id === run && typeof value.stop_accepted === "boolean"
      && ["active", "draining", "released", "unknown"].includes(value.resources)
      && ["pending", "known", "unknown", "not_applicable"].includes(value.cost) && value.cost_scope === "process";
  }
  function valid(data) {
    if (!validRunFeedback(data)) return false;
    if (Object.hasOwn(data, "continuation_target")) {
      const target = data.continuation_target;
      if (!target || Object.keys(target).length !== 2 || !/^[a-f0-9]{32}$/.test(target.task_id)
          || !/^[a-f0-9]{32}$/.test(target.run_id) || !data.agent_history_available
          || target.run_id !== data.task?.run_id || target.run_id !== data.operation?.id
          || !["stopped", "error"].includes(data.operation?.status)) return false;
    }
    if (window.PactTrailDraftUI?.validRecovered && !window.PactTrailDraftUI.validRecovered(data)) return false;
    const task = data.task;
    const connected = data.mode === "agent_model";
    if (data.mode !== "agent_demo" && !connected) return !Object.hasOwn(data, "task");
    const protocol = task && [2, 3].includes(task.version);
    const sourceField = task && Object.hasOwn(task, "source");
    const selected = sourceField && task.source !== null;
    const intent = task && Object.hasOwn(task, "intent");
    const draft = intent && task.intent === "change_draft";
    const projectSource = selected && task.source && task.source.kind === "selected_project";
    const scopedFiles = projectSource && validProject(task.source) ? task.source.files.map(entry => entry.file_id)
      : selected ? ["selected.txt"] : files;
    if (data.answer_mode !== "ordinary" || data.provider_id !== (connected ? "deepseek" : "offline")
        || ["history", ...(connected ? [] : ["connection"])].some((name) => Object.hasOwn(data, name))
        || (connected && (!data.connection || !task || task.version !== 3))
        || !fields(task, ["version", "decision_mode", "workspace", "run_id", "steps", "artifact", "budget", "error",
          ...(sourceField ? ["source"] : []),
          ...(intent ? ["intent"] : []),
          ...(Object.hasOwn(task, "parent") ? ["parent"] : []),
          ...(protocol ? ["model", ...(Object.hasOwn(task, "rejection") ? ["rejection"] : [])] : [])])
        || !validRejection(task)
        || (intent && (!["answer", "change_draft"].includes(task.intent) || Object.hasOwn(task, "parent")))
        || (draft && !window.PactTrailDraftUI?.localSource(task.source))
        || (sourceField && !selected && task.intent !== "answer")
        || (Object.hasOwn(task, "parent") && (!selected || !window.PactTrailFollowupUI?.validParent(task.parent, task.run_id)))
        || (protocol ? task.version !== (connected ? 3 : 2)
          || task.decision_mode !== (connected ? "model_protocol" : "synthetic_protocol") || !validModel(task.model)
          : task.version !== 1 || task.decision_mode !== "synthetic")
        || (selected ? !validSource(task.source) || task.workspace !== (projectSource ? "selected-project" : "selected-text") || task.run_id === null
          : task.workspace !== "synthetic-project")
        || (connected && (Object.keys(task.model).some((key) => !["live_ready", "stopped", "stop_reason"].includes(key)
          && JSON.stringify(task.model[key]) !== JSON.stringify(data.connection[key]))
          || (task.model.stopped && !data.connection.stopped)
          || (data.busy && data.connection.can_send)))
        || (task.run_id !== null && !identity(task.run_id))
        || (task.error !== null && !(protocol ? protocolError(task.error) : knownError(task.error)))
        || !Array.isArray(task.steps) || task.steps.length > 8
        || !task.steps.every((step, index) => fields(step, ["index", "kind", "tool", "reason", "result"])
          && integer(step.index, 8) && step.index > 0 && (!index || task.steps[index - 1].index < step.index)
          && bounded(step.reason, 8192)
          && Array.from(step.reason).length <= (step.kind === "finish" ? task.intent === "answer" ? 4096 : 2048 : 256)
          && (step.kind === "finish" ? step.tool === null : step.kind === "tool" && Object.hasOwn(toolNames, step.tool))
          && step.tool !== (draft ? "save_report" : "save_draft")
          && validResult(step, scopedFiles))) return false;
    const budget = task.budget;
    const limits = { max_decisions: 8, max_tools: 7, max_read_bytes: 32768,
      max_total_decisions: 48, max_total_tools: 42, max_total_read_bytes: 196608,
      model_dispatches: protocol ? task.model.calls : 0,
      model_cost_nano: protocol ? task.model.model_accounted_nano_usd ?? task.model.accounted_nano_usd : 0 };
    if (!fields(budget, ["decisions", "tools", "read_bytes", "total_decisions", "total_tools", "total_read_bytes",
      ...Object.keys(limits), "deadline_seconds"]) || !Object.entries(limits).every(([key, value]) => budget[key] === value)
      || !["decisions", "tools", "read_bytes", "total_decisions", "total_tools", "total_read_bytes"]
        .every((key) => integer(budget[key], budget["max_" + key]))
      || !Number.isFinite(budget.deadline_seconds) || budget.deadline_seconds <= 0 || budget.deadline_seconds > 60
      || (connected && budget.deadline_seconds !== 60)
      || budget.decisions > budget.total_decisions || budget.tools > budget.total_tools
      || budget.read_bytes > budget.total_read_bytes
      || task.steps.some((step) => step.index > budget.tools + (step.kind === "finish" ? 1 : 0))
      || (task.run_id === null && (task.steps.length || task.artifact || budget.decisions || budget.tools || budget.read_bytes))) return false;
    const artifact = task.artifact;
    return artifact === null || (fields(artifact, ["name", "path", "sha256", "bytes", "text"])
      && artifact.name === (draft ? "draft-" + task.run_id + ".json" : "report-" + task.run_id + ".md") && bounded(artifact.path, 4096) && artifact.path.length > 0
      && hash(artifact.sha256) && bounded(artifact.text, 8192) && integer(artifact.bytes, 8192)
      && artifact.bytes > 0 && byteLength(artifact.text) === artifact.bytes
      && (!draft || Boolean(window.PactTrailDraftUI.parse(artifact, task.run_id, task.source))));
  }

  function clearArtifact() {
    artifactGeneration += 1;
    if (artifactUrl !== null) URL.revokeObjectURL(artifactUrl);
    artifactUrl = null;
    currentArtifact = null;
    window.PactTrailDraftUI?.clear("task-draft");
    for (const name of ["task-open", "task-download"]) el(name).disabled = true;
  }

  function controls(disabled) {
    unavailable = disabled;
    for (const name of ["task-open", "task-download"]) el(name).disabled = disabled || artifactUrl === null;
  }

  async function verifyArtifact(artifact, generation, task) {
    try {
      const bytes = new TextEncoder().encode(artifact.text);
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      const actual = Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
      if (generation !== artifactGeneration) return;
      if (actual !== artifact.sha256) throw new Error("artifact hash mismatch");
      const draft = task.intent === "change_draft" ? await window.PactTrailDraftUI.verify(artifact, task.run_id, task.source) : null;
      if (generation !== artifactGeneration) return;
      if (task.intent === "change_draft" && !draft) throw new Error("draft hash mismatch");
      artifactUrl = URL.createObjectURL(new Blob([bytes], { type: "text/plain;charset=utf-8" }));
      currentArtifact = artifact;
      if (draft) window.PactTrailDraftUI.render(draft, "task-draft", artifact.sha256);
      text("task-artifact-state", "已保存 · 内容校验一致");
      controls(unavailable);
    } catch (_) {
      if (generation !== artifactGeneration) return;
      text("task-artifact-state", "成果内容校验失败，无法打开或下载。刷新状态可重新核对。");
      artifactKey = "";
      controls(true);
    }
  }

  function resultText(step, task) {
    const result = step.result;
    if (result.status === "error") return errors[result.error];
    if (step.kind === "finish") return task.intent === "answer" && !task.artifact ? "本轮回答完成，未保存独立成果。" : "本轮任务完成，保存情况见成果。";
    if (step.tool === "search") return "命中预览 " + result.matches.length + " 处 · 读取 " + result.read_bytes + " 字节"
      + result.matches.map((match) => "\n" + match.file_id + ":" + match.line + "  " + match.text).join("");
    if (step.tool === "read") return result.file_id + " · 共读取 " + result.read_bytes + " 字节（内容预览）\n" + result.text;
    return result.name + " · " + result.bytes + " 字节\nSHA-256 " + result.sha256;
  }

  function render(data) {
    const active = ["agent_demo", "agent_model"].includes(data.mode);
    show("task-panel", active);
    if (!active) return;
    const task = data.task;
    const draft = task.intent === "change_draft";
    const budget = task.budget;
    window.PactTrailFollowupUI?.renderParent(task.parent, "task-parent");
    const labels = {
      "workspace-caption": "项目任务", "workspace-eyebrow": "项目任务", "materials-tab-title": "文件与改动",
      "local-scope": "本机 · 实际文件工具", "welcome-eyebrow": "Agent 工作区",
      "welcome-title": "这次想做什么？",
      "welcome-description": "关联文件，开始工作。",
      "demo-note": "当前使用模拟决策，不连接真实模型；搜索、读取和保存会实际执行。尚未验证真实模型的规划能力。",
      "answer-mode-note": "确认材料后发送 · 报告可下载",
      "input-note": "Enter 发送 · Shift + Enter 换行",
      "mode-badge": "synthetic 决策 · 实际工具执行", "composer-provider": "工具任务",
      "model-note": "synthetic 决策器 · 不连接模型。根据工具结果继续、调整或结束。",
      "source-footnote": "打开或下载的是本次任务已保存并校验的报告。保存位置和文件信息可展开查看。",
      "storage-note-title": "任务记录与报告保存在本机",
      "storage-note": "刷新可查看任务进度和成果。记录、文本快照和报告保存到任务目录；新对话清空当前关联和视图。关闭后可从独立只读历史入口查看，不恢复执行。",
      "storage-limit-note": "每次任务最多 8 次决策、7 次工具调用、32 KiB 读取；新对话不会恢复进程累计额度。",
      "session-boundary-title": "任务与成果", "session-boundary-note": "停止和错误保留已写入的报告；关闭服务结束本次执行。",
      "memory-caption": "有界文本任务 · 报告保存到本机", "session-provider": "synthetic 决策 / 实际工具",
      "session-location": task.artifact ? task.artifact.path : "本次任务目录 / reports",
      "turn-count": "本轮决策 " + budget.decisions + " / 8 · 工具 " + budget.tools + " / 7",
      "task-budget": "本轮读取 " + budget.read_bytes + " / 32768 字节 · 期限 " + budget.deadline_seconds + " 秒",
      "task-total": "进程累计：决策 " + budget.total_decisions + " / 48 · 工具 " + budget.total_tools
        + " / 42 · 读取 " + budget.total_read_bytes + " / 196608 字节",
      "task-cost": "真实模型派发 0 次 · 模型费用 $0",
    };
    if (task.version === 2) {
      const model = task.model;
      labels["mode-badge"] = "synthetic 协议决策 · 实际工具执行";
      labels["demo-note"] = "使用 synthetic 传输验收模型工具协议；文件工具实际执行。尚未验证真实模型的规划能力。";
      labels["model-note"] = "DeepSeek 工具协议 · 本地输入估算 · synthetic 传输，根据实际工具结果继续决策。";
      labels["session-provider"] = "synthetic 工具协议 / 实际工具";
      labels["task-cost"] += " · 合成协议派发 " + model.calls + " / 48 次"
        + " · 模拟已核算 " + money(model.accounted_nano_usd) + " · 模拟预留 " + money(model.reserved_nano_usd)
        + " · 模拟预算 " + money(model.budget_nano_usd)
        + (model.cost_known ? " · 当前模拟费用已知" : " · 最终模拟费用未知，保留预留")
        + " · 最近派发输入估算 " + (model.input_estimate_tokens === null ? "尚无" : model.input_estimate_tokens + " tokens")
        + (model.usage ? " · 已核实模拟用量：输入 " + model.usage.prompt_tokens
          + " / 输出 " + model.usage.completion_tokens + " / 合计 " + model.usage.total_tokens + " tokens" : " · 尚无已核实模拟用量")
        + " · 价格快照 2026-09-11（local-estimate）"
        + (model.stopped ? " · 协议已停止：" + (errorText(model.stop_reason) || "不再派发。") : "");
    }
    if (task.version === 3) {
      const model = task.model;
      const synthetic = data.connection.transport_mode === "synthetic";
      labels["mode-badge"] = (synthetic ? "synthetic 连接验收" : "DeepSeek 工具决策") + " · 实际工具执行";
      labels["welcome-description"] = "启用模型，开始工作。";
      labels["demo-note"] = synthetic
        ? "当前使用 synthetic 传输验收连接组装；文件工具实际执行。尚未验证真实模型的规划能力。"
        : "仅发送目标才调用模型并执行范围内的文件工具。每任务最多 8 次模型决策，失败不重试。";
      labels["model-note"] = "deepseek-flash · 工具任务独立连接，配置与启用不会派发；状态与费用见连接面板。";
      labels["session-provider"] = synthetic ? "synthetic 连接验收 / 实际工具" : "DeepSeek / 实际工具";
      labels["task-cost"] = (synthetic ? "synthetic 模拟派发 " : "连接协议派发 ") + model.calls + " / 48 次"
        + " · " + (synthetic ? "模拟已核算 " : "已核算 ") + money(model.accounted_nano_usd)
        + ledgerCostText(model)
        + " · 预留 " + money(model.reserved_nano_usd) + " · 预算 " + money(model.budget_nano_usd)
        + (model.cost_known ? " · 当前费用已知" : " · 最终费用未知，保留预留")
        + (synthetic ? " · 真实模型请求 0 次" : "")
        + " · 价格快照 2026-09-11（local-estimate）";
      labels["storage-note"] = data.conversation?.persistent === true
        ? "步骤、费用快照与报告保存到任务目录，凭据不写入记录。历史只读，不恢复执行。"
        : "步骤记录、费用快照、文本快照和报告保存到任务目录，凭据不写入记录。新对话清空关联、视图与配置；关闭后可从独立只读历史入口查看，不恢复执行。";
      labels["session-boundary-note"] = data.conversation?.persistent === true
        ? "离页断开发送；如已安全保存，保存项保留。继续前需重新启用，显式清除才删除保存项。已保存报告保留。"
        : "清除配置、停止或错误保留已经保存的报告。刷新或离开页面会尝试清除配置；关闭服务结束执行。";
    }
    if (draft) {
      labels["welcome-description"] = "描述改动，审阅差异。";
      labels["answer-mode-note"] = "草案待审阅 · 未应用 · 未运行测试";
      labels["source-footnote"] = "草案保存在本次任务目录中。可展开查看原文件与新文本的校验信息；保存草案不代表已应用或测试通过。";
      labels["storage-note-title"] = "任务记录与草案保存在本机";
      labels["session-boundary-note"] = "停止或错误保留已经保存的草案；原项目未修改，未运行测试。";
      labels["memory-caption"] = "变更草案 · 未应用 · 未执行测试";
      if (data.editing) {
        labels["welcome-description"] = "审阅改动，再决定应用。";
        labels["answer-mode-note"] = "应用需单独确认 · 不运行测试";
        labels["session-boundary-note"] = "已保存草案会保留；文件应用另列结果，未运行测试。";
        labels["memory-caption"] = "文件改动 · 应用结果见右侧面板";
      }
    }
    document.title = "PactTrail · 自主工具任务";
    text("sources-toggle-label", "文件与改动");
    el("sources-toggle").setAttribute("aria-label", "切换文件与改动面板");
    for (const [name, value] of Object.entries(labels)) text(name, value);
    el("message").placeholder = data.project ? "描述任务或改动…" : data.material ? "问问这份文本…" : "输入任务，或关联文件…";
    el("provider").disabled = true;
    show("more-prompts", false);
    show("local-material", true);
    show("evidence-empty", false);
    show("evidence-intro", false);
    text("task-source", task.source && task.source.kind === "selected_project"
      ? "本轮" + (task.source.version === 2 ? "网页" : "项目") + "来源：" + task.source.name + " · 清单版本 " + task.source.version + " · " + task.source.files.length + " 份 · "
        + task.source.byte_count + " 字节 · SHA-256 " + task.source.sha256 + "。仅以下选定快照可读，不代表完整目录：\n"
        + task.source.files.map(entry => entry.file_id + " · " + entry.path + " · " + entry.byte_count + " 字节 · SHA-256 " + entry.sha256
          + (entry.web ? "\n" + webProvenance(entry.web) : "")).join("\n")
      : task.source ? "本轮来源：" + task.source.name + " · " + task.source.byte_count + " 字节 · " + task.source.line_count
        + " 行 · SHA-256 " + task.source.sha256 + "。工具仅可见 selected.txt 这一份快照。"
      : "本轮范围：固定样例 overview.txt、plan.txt、checks.txt。工具可见文件列表不代表目录全量。" );
    text("task-evidence-scope", "核对报告时，把材料声明、实际观察和缺失证据分开看。PASS / PENDING 按原文保留；选定材料不代表完整目录。文件是否已保存，以应用校验的信息为准。");
    text("pending-note", data.operation && data.operation.status === "running"
      ? "正在决策并执行文件工具…" : "正在结束上一轮任务；迟到结果不会继续执行工具。");
    if (task.error) { text("operation-note", window.PactTrailDraftUI?.failureText?.(task) || errorText(task.error) + rejectionText(task)); show("operation-note", true); }
    if (task.error === "state_unavailable" || data.operation && data.operation.error === "state_unavailable") {
      text("storage-note-title", "任务记录写入未能确认");
      text("storage-note", "本次记录写入失败或状态不可确认，已停止继续执行。只读历史仅能核对最后保存的记录；不要据此推断后续步骤或报告保存成功。");
    }
    if (data.operation && data.operation.status === "stopped") {
      text("operation-note", "任务已停止。迟到决策不会继续执行工具；已保存的报告保留。");
    }
    const nextStepsKey = JSON.stringify([task.run_id, task.steps]);
    if (nextStepsKey !== stepsKey) {
      stepsKey = nextStepsKey;
      el("task-steps").replaceChildren(...task.steps.map((step) => {
        const card = document.createElement("li"); card.className = "task-step";
        for (const [tag, content] of [["h3", step.index + ". " + (step.kind === "finish" ? "完成任务" : toolNames[step.tool])],
          ["p", step.reason], ["pre", resultText(step, task)]]) {
          const item = document.createElement(tag); item.textContent = content; card.appendChild(item);
        }
        return card;
      }));
    }
    show("task-empty", task.steps.length === 0);
    const artifactTask = window.PactTrailDraftUI?.effectiveDraft(data) || task;
    const artifact = artifactTask.artifact, artifactDraft = artifactTask.intent === "change_draft";
    text("task-artifact-heading", artifactTask.recovered ? "已恢复的草案" : artifactDraft ? "草案成果" : "保存的报告");
    text("task-open", artifactDraft ? "打开草案 JSON" : "打开报告");
    text("task-artifact-summary", artifactDraft ? "预览草案 JSON" : "预览报告内容");
    show("task-artifact", Boolean(artifact));
    const nextKey = JSON.stringify([artifactTask.recovered ? "recovered" : "live", data.conversation_id,
      artifactTask.run_id, artifactTask.intent, artifactTask.source, artifact]);
    if (nextKey !== artifactKey) {
      clearArtifact(); artifactKey = nextKey;
      if (artifact) {
        text("task-artifact-name", artifact.name);
        text("task-artifact-path", artifact.path || "");
        text("task-artifact-hash", "SHA-256 " + artifact.sha256 + " · " + artifact.bytes + " 字节");
        text("task-artifact-text", artifact.text);
        text("task-artifact-state", "正在核对已保存内容…");
        void verifyArtifact(artifact, artifactGeneration, artifactTask);
      }
    }
  }

  function progress(data, { stopping = false, locked = false } = {}) {
    const task = data?.task, operation = data?.operation;
    if (!data?.agent_history_available || !task?.run_id || operation?.id !== task.run_id
      || !data.messages?.some(message => message.role === "user" && message.operation_id === task.run_id)
      || !validRunFeedback(data)) return null;
    const feedback = validRunFeedback(data) ? data.run_feedback : null;
    const labels = { search: "搜索材料", read: "读取材料", save_report: "保存报告", save_draft: "保存草案" };
    const last = task.steps.filter(step => step.kind === "tool" && step.result.status === "ok").at(-1);
    const fact = last ? ({ search: "已搜索材料", read: "已读取材料", save_report: "报告已保存", save_draft: "草案已保存" })[last.tool] : "";
    const editing = data.editing, check = data.draft_precheck, artifact = task.artifact;
    const boundEdit = artifact && editing?.run_id === task.run_id && editing.artifact_sha256 === artifact.sha256;
    const unreadableEdit = artifact && task.intent === "change_draft" && operation.status === "complete"
      && editing?.state === "unavailable" && editing.run_id === null && editing.artifact_sha256 === null;
    const boundCheck = artifact && check?.run_id === task.run_id && check.artifact_sha256 === artifact.sha256;
    const uncertainSave = task.steps.some(step => ["save_report", "save_draft"].includes(step.tool)
      && step.result.status === "error" && step.result.error === "output_unavailable");
    let phase, title, note;
    if (uncertainSave || unreadableEdit || [task.error, operation.error].some(code => ["state_unavailable", "write_unknown"].includes(code))
      || boundEdit && ["unknown", "unavailable"].includes(editing.state)
      || boundCheck && check.status === "unknown") {
      phase = "unknown"; title = "结果待核对"; note = "先核对结果，不会自动重试。";
    } else if (boundEdit && editing.state === "partial") {
      phase = "partial"; title = "部分文件已应用"; note = "请核对逐文件结果。";
    } else if (operation.status === "stopped") {
      phase = "stopped"; title = "已停止";
      if (feedback?.stop_accepted && ["active", "draining"].includes(feedback.resources)) {
        title = "停止已接受 · 正在退出"; note = "等待当前执行退出，不会继续派发。";
      } else if (feedback?.stop_accepted && feedback.resources === "unknown") {
        title = "停止已接受 · 状态待核对"; note = "请核对执行状态与费用。";
      } else note = feedback?.resources === "released" ? "当前执行资源已释放，不会自动重试。"
        : "执行资源状态待核对，不会自动重试。";
    } else if (operation.status === "error") {
      phase = "error"; title = "本轮未完成";
      note = errorText(task.error || operation.error) || "展开详情核对原因。";
      if (feedback?.resources === "draining") note += " 正在释放当前执行资源。";
    } else if (boundCheck && check.status === "running") {
      phase = "checking"; title = "正在预检草案"; note = "仅检查语法，尚未应用。";
    } else if (boundCheck && !check.reaped) {
      phase = "checking"; title = "预检正在收尾"; note = "等待检查结束后再操作。";
    } else if (operation.status === "running") {
      phase = "processing"; title = "正在处理"; note = fact;
      if (data.web_search_state?.run_id === task.run_id && data.web_search_state.status === "searching") {
        title = "正在联网搜索"; note = "";
      }
    } else if (operation.status === "complete") {
      if (task.intent === "change_draft" && artifact) {
        phase = boundEdit && editing.state === "applied" ? "applied" : "review";
        title = phase === "applied" ? "文件已应用" : boundEdit && editing.state === "ready" ? "待审阅" : "草案已保存";
        note = phase === "applied" ? "" : boundCheck && check.status === "failed"
          ? "语法预检未通过；尚未应用。" : "尚未应用。";
      } else { phase = "completed"; title = "已完成"; note = artifact ? "报告已保存。" : ""; }
    } else return null;
    const requestingStop = stopping && operation.status === "running";
    if (locked) { phase = "unknown";title = "状态待核对";note = "保留上次记录，请先核对当前状态；不会自动重试。"; }
    else if (requestingStop) { title = "正在请求停止";note = "等待停止请求确认。"; }
    const lifecycle = locked ? "unknown" : feedback ? feedback.resources === "released" ? "terminal"
      : feedback.resources === "unknown" ? "unknown"
        : operation.status === "running" && !feedback.stop_accepted ? "running" : "exiting"
      : operation.status === "running" ? "running" : "unknown";
    let currentAction = "";
    if (locked) currentAction = "请先核对当前执行状态";
    else if (requestingStop) currentAction = "正在请求停止";
    else if (feedback?.stop_accepted && ["active", "draining"].includes(feedback.resources)) currentAction = "等待当前执行退出";
    else if (feedback?.resources === "draining") currentAction = "正在释放当前执行资源";
    else if (boundCheck && check.status === "running") currentAction = "正在预检草案";
    else if (boundCheck && !check.reaped) currentAction = "等待草案预检退出";
    else if (operation.status === "running") currentAction = data.web_search_state?.run_id === task.run_id
      && data.web_search_state.status === "searching" ? "正在联网检索并读取来源" : "等待决策或工具结果";
    else if (!feedback || feedback.resources === "unknown") currentAction = "执行资源状态待核对";
    const model = task.model || (data.mode === "agent_model" ? data.connection : null);
    const cost = locked ? "unknown" : feedback?.cost || (model && typeof model.cost_known === "boolean"
      ? model.cost_known ? "known" : operation.status === "running" ? "pending" : "unknown" : "not_applicable");
    const settlement = { state: cost === "not_applicable" ? "none" : cost,
      label: ({ pending: "费用正在结算", known: "当前费用已核清", unknown: "费用待核对", not_applicable: "暂无费用事实" })[cost],
      detail: "", scope: feedback || model ? "process" : "none" };
    const measured = model && nonnegative(model.accounted_nano_usd) && nonnegative(model.reserved_nano_usd);
    settlement.detail = measured ? (locked ? "上次已知费用快照 · " : "")
      + (task.decision_mode === "synthetic_protocol" || data.connection?.transport_mode === "synthetic"
      ? "合成费用快照 · " : "") + "本次服务累计已核算 " + money(model.accounted_nano_usd)
      + " · 预留 " + money(model.reserved_nano_usd) + ledgerCostText(model)
      + (cost === "unknown" ? "；未核清的预留继续保留。" : cost === "pending" ? "；当前请求尚未结算。" : "。")
      : feedback?.cost === "not_applicable" ? "本轮费用不适用。" : "尚无可核对的费用金额。";
    const steps = [], add = (id, label, state, status) => steps.push({ id: task.run_id + ":" + id, label, state, status });
    add("accepted", "请求已接收", "complete", "完成");
    if (requestingStop || feedback?.stop_accepted) add("stop-request", "停止请求", requestingStop ? "running" : "complete",
      requestingStop ? "等待确认" : "已接受");
    const search = data.web_search_state;
    if (search?.run_id === task.run_id) {
      const state = ({ searching: "running", ready: "complete", error: "error", cancelled: "stopped" })[search.status];
      if (state) add("web-search", "联网检索", state, ({ running: "进行中", complete: "完成", error: "未完成", stopped: "已停止" })[state]);
    }
    for (const step of task.steps) add("step:" + step.index,
      step.kind === "finish" ? "完成回答" : labels[step.tool] || "工具操作",
      step.result.status === "ok" ? "complete" : "error", step.result.status === "ok" ? "完成" : "未完成");
    if (artifact) add("artifact", task.intent === "change_draft" ? "草案成果" : "报告成果", "complete", "已保存");
    if (boundCheck) add("precheck", "草案语法预检", ({ running: "running", passed: "complete", failed: "error" })[check.status] || "unknown",
      ({ running: "进行中", passed: "通过", failed: "未通过", cancelled: "已取消", stale: "待重新核对", unknown: "结果待核对" })[check.status] || "待核对");
    if (boundEdit && ["applied", "partial", "unknown", "unavailable"].includes(editing.state)) add("application", "文件应用",
      editing.state === "applied" ? "complete" : editing.state === "partial" ? "error" : "unknown",
      ({ applied: "已应用", partial: "部分已应用", unknown: "结果待核对", unavailable: "结果待核对" })[editing.state]);
    add("lifecycle", "当前执行", lifecycle === "terminal" ? "complete" : lifecycle === "running" ? "running"
      : lifecycle === "exiting" ? "running" : "unknown", !locked && feedback?.resources === "released" ? "资源已释放"
        : lifecycle === "running" ? "进行中" : lifecycle === "exiting" ? "正在退出" : "资源状态待核对");
    const omittedSteps = Math.max(0, steps.length - 15);
    const visibleSteps = steps.length > 16 ? [steps[0], { id: task.run_id + ":omitted", label: "省略 " + omittedSteps + " 项",
      state: "summary", status: "查看详情中的完整记录" }, ...steps.slice(-14)] : steps;
    const actions = { details: !locked, reconcile: locked || ["unknown", "partial", "error", "stopped"].includes(phase)
      || settlement.state === "unknown" || lifecycle === "unknown", compose: !locked };
    return { phase, title, note, runId: task.run_id, lifecycle, statusLabel: title, currentAction,
      pendingNote: !locked && (["running", "exiting"].includes(lifecycle) || phase === "checking") ? currentAction : "",
      operationNote: ["unknown", "partial", "error", "stopped"].includes(phase) ? note : "",
      settlement, actions, steps: visibleSteps, omittedSteps: steps.length > 16 ? omittedSteps : 0 };
  }

  let progressOwner = "", progressRows = new Map();
  function renderProgress(data, context = {}) {
    const value = progress(data, context), card = el("task-progress");
    if (!card) return false;
    const quiet = value?.phase === "completed" && data.task.intent === "answer" && !data.task.artifact;
    const searchFailure = value?.phase === "error" && data.web_search_state?.run_id === data.task.run_id
      && data.web_search_state.status === "error" && data.operation.error === "source_unavailable";
    card.hidden = !value || quiet || searchFailure;
    const owner = value ? data.conversation_id + ":" + data.task.run_id : "";
    if (owner !== progressOwner) {
      progressOwner = owner; progressRows = new Map(); el("task-progress-details").open = false;
      el("task-progress-steps").replaceChildren();el("task-progress-steps").scrollTop = 0;el("task-progress-steps").scrollLeft = 0;
    }
    if (!value) {
      for (const id of ["task-progress-title", "task-progress-note", "task-progress-diagnostic", "task-progress-current",
        "task-progress-settlement", "task-progress-settlement-detail"]) text(id, "");
      for (const id of ["task-progress-reconcile", "task-progress-compose"]) show(id, false);
      return false;
    }
    const write = (id, content) => { if (el(id).textContent !== content) text(id, content); };
    card.dataset.phase = value.phase;
    card.dataset.lifecycle = value.lifecycle;
    write("task-progress-title", value.title);
    write("task-progress-note", value.note); show("task-progress-note", Boolean(value.note));
    write("task-progress-current", value.currentAction);show("task-progress-current", Boolean(value.currentAction));
    write("task-progress-settlement", value.settlement.label);
    write("task-progress-settlement-detail", value.settlement.detail);
    el("task-progress-settlement").dataset.state = value.settlement.state;
    show("task-progress-reconcile", value.actions.reconcile);show("task-progress-compose", value.actions.compose);
    write("task-progress-open", ["review", "applied", "partial"].includes(value.phase) ? "查看改动"
      : data.task.artifact ? "查看成果" : "查看详情");
    const diagnostic = data.task.error ? window.PactTrailDraftUI?.failureText?.(data.task)
      || (errorText(data.task.error) || "请核对任务记录。") + rejectionText(data.task) : "";
    write("task-progress-diagnostic", diagnostic); show("task-progress-diagnostic", Boolean(diagnostic));
    const list = el("task-progress-steps"), top = list.scrollTop, left = list.scrollLeft, next = new Map();
    value.steps.forEach((step, index) => {
      const item = progressRows.get(step.id) || document.createElement("li");
      item.dataset.stepId = step.id;item.dataset.state = step.state;item.className = "task-progress-step";item.tabIndex = -1;
      const content = step.label + " · " + step.status;
      if (item.textContent !== content) item.textContent = content;
      if (list.children[index] !== item) list.insertBefore(item, list.children[index] || null);
      next.set(step.id, item);
    });
    for (const [id, row] of progressRows) if (!next.has(id)) row.remove();
    progressRows = next;list.scrollTop = top;list.scrollLeft = left;
    show("task-progress-details", value.steps.length > 0 || Boolean(diagnostic));
    return searchFailure || !quiet;
  }

  function controlProgress(data, context = {}) {
    const value = progress(data, context), { unavailable, mutating, reading, locked, historyActive, stopping } = context;
    el("task-progress-open").disabled = Boolean(unavailable || !value?.actions.details);
    if (el("task-progress-reconcile")) el("task-progress-reconcile").disabled = Boolean(mutating || reading || !value?.actions.reconcile);
    if (el("task-progress-compose")) el("task-progress-compose").disabled = Boolean(locked || !data || historyActive || !value?.actions.compose);
    text("stop", stopping ? "正在请求停止…" : "停止");
    if (renderProgress(data, context)) { show("pending", false); show("operation-note", false); }
    else if (value) {
      show("pending", Boolean(data.busy)); text("pending-note", value.pendingNote);
      text("operation-note", value.operationNote); show("operation-note", value.phase !== "completed" && Boolean(value.operationNote));
    }
    return value;
  }

  function open(download) {
    if (unavailable || !artifactUrl || !currentArtifact) return;
    const link = document.createElement("a");
    link.href = artifactUrl;
    if (download) link.download = currentArtifact.name;
    else { link.target = "_blank"; link.rel = "noopener noreferrer"; }
    link.hidden = true;
    try { document.body.appendChild(link); link.click(); } finally { link.remove(); }
  }
  el("task-open").addEventListener("click", () => open(false));
  el("task-download").addEventListener("click", () => open(true));
  window.PactTrailTaskUI = { valid, render, controls, progress, renderProgress, controlProgress, validRunFeedback, validConnection, validSource, validResult, validProject, validProjectName, validProjectPath, validWebURL, webProvenance, foldPath, comparePaths, validRejection, rejectionText, validLedger, ledgerFields, ledgerCostText,
    error: (code) => protocolError(code) ? errorText(code) : null,
    close: () => { clearArtifact(); artifactKey = ""; } };
})();
