(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const text = (id, value) => { el(id).textContent = value; };
  const show = (id, visible) => { el(id).hidden = !visible; };
  const fields = (value, names) => value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
  const integer = (value, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(value) && value >= 0 && value <= max;
  const bounded = (value, max) => typeof value === "string" && Array.from(value).length <= max
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\ud800-\udfff]/u.test(value);
  const identity = value => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  const statuses = { running: "原运行未结束", complete: "记录为完成", stopped: "已停止", error: "执行出错" };
  const recoveries = { terminal: "已记录结束状态", interrupted: "原运行已中断", write_unknown: "保存结果未知，不重发保存动作" };
  const integrities = { verified: "已核对范围内快照与报告身份", source_changed: "来源快照已变更或不可核对",
    artifact_changed: "报告已变更或不可核对", both_changed: "来源快照与报告均已变更或不可核对" };
  const errors = {
    record_corrupt: "任务记录损坏，未展示未经确认的内容。",
    record_partial: "发现部分写入，保存状态未能确认；原文件保留。",
    record_version: "记录版本不受支持，未转换或覆盖。",
    record_unavailable: "任务记录暂不可用，请核对本地目录后刷新。",
    record_missing: "这份任务记录已不存在，请刷新列表。",
    record_read_only: "当前入口只读，不接受修改记录。",
    record_conflict: "记录身份或版本发生冲突，未合并记录。",
    record_report_unavailable: "没有可核对的已保存报告。",
    record_artifact_changed: "报告文件与保存身份不一致，未提供正文或下载。",
    invalid_response: "历史状态未通过校验，无法确认当前内容。",
    state_unavailable: "无法确认历史状态，请检查本地服务后刷新。",
    access_denied: "本地会话验证失败，请从当前服务地址重新打开。",
    invalid_request: "读取请求未被接受，请刷新记录。",
    closed: "只读服务已关闭，请重新打开当前服务页面。",
    busy: "任务当时仍在处理中。", stale_request: "当时的请求状态已过期。",
    request_conflict: "当时的请求身份有冲突。", turn_limit: "当时已达到当前会话轮数上限。",
    interrupted: "当时的任务已中断。",
  };
  const recordError = value => typeof value === "string" && value.startsWith("record_") && Object.hasOwn(errors, value);
  const knownError = value => value === null || typeof value === "string"
    && (Object.hasOwn(errors, value) || Boolean(window.PactTrailTaskUI.error(value)));
  const summaryFields = ["run_id", "revision", "created_at", "updated_at", "goal", "status", "recovery", "integrity", "has_report", "error"];
  const keys = (object, value) => typeof value === "string" && Object.hasOwn(object, value);
  const timestamp = value => integer(value, 253402300799);

  function validSummary(value) {
    if (!fields(value, summaryFields) || !identity(value.run_id)) return false;
    if (value.error !== null) return recordError(value.error) && value.revision === 0 && value.created_at === 0
      && value.updated_at === 0 && value.goal === "" && value.status === "unavailable"
      && value.recovery === "unavailable" && value.integrity === "unavailable" && value.has_report === false;
    return integer(value.revision, 256) && value.revision > 0 && timestamp(value.created_at)
      && timestamp(value.updated_at) && value.updated_at >= value.created_at && bounded(value.goal, 4096)
      && value.goal.trim().length > 0 && keys(statuses, value.status) && keys(recoveries, value.recovery)
      && keys(integrities, value.integrity) && typeof value.has_report === "boolean";
  }

  function validCost(value) {
    const names = ["scope", "mode", "calls", "accounted_nano_usd", "reserved_nano_usd", "budget_nano_usd", "cost_known",
      "stopped", "stop_reason", "usage", "input_estimate_tokens", "token_basis", "pricing_basis"];
    const ledger = value && window.PactTrailTaskUI.ledgerFields.some(name => Object.hasOwn(value, name));
    if (ledger) names.push(...window.PactTrailTaskUI.ledgerFields);
    if (!fields(value, names) || !window.PactTrailTaskUI.validLedger(value) || ledger && value.mode === "synthetic"
      || value.scope !== "process" || !["synthetic", "synthetic_protocol", "model_protocol"].includes(value.mode)
      || !integer(value.calls, 48) || !integer(value.accounted_nano_usd) || !integer(value.reserved_nano_usd)
      || !integer(value.budget_nano_usd) || typeof value.cost_known !== "boolean"
      || typeof value.stopped !== "boolean" || !knownError(value.stop_reason)
      || !(value.input_estimate_tokens === null || integer(value.input_estimate_tokens))) return false;
    if (value.mode === "synthetic" && !(value.calls === 0 && value.accounted_nano_usd === 0
      && value.reserved_nano_usd === 0 && value.budget_nano_usd === 0
      && value.token_basis === "synthetic-no-model" && value.pricing_basis === "synthetic-no-model")) return false;
    if (!["synthetic-no-model", "synthetic-test-only", "local-estimate"].includes(value.token_basis)
      || !["synthetic-no-model", "deepseek-peak-2026-09-11"].includes(value.pricing_basis)) return false;
    const usage = value.usage;
    const usageFields = ["prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"];
    return usage === null || fields(usage, usageFields) && usageFields.every(name => integer(usage[name]))
      && usage.total_tokens === usage.prompt_tokens + usage.completion_tokens
      && usage.prompt_tokens === usage.prompt_cache_hit_tokens + usage.prompt_cache_miss_tokens;
  }

  function validArtifact(value, runId, withText = false, draft = false) {
    return fields(value, ["name", "sha256", "bytes", ...(withText ? ["text"] : [])])
      && value.name === (draft ? "draft-" + runId + ".json" : "report-" + runId + ".md") && hash(value.sha256) && integer(value.bytes, 8192) && value.bytes > 0
      && (!withText || bounded(value.text, 8192) && new TextEncoder().encode(value.text).byteLength === value.bytes);
  }

  const draftVersion = version => [4, 6].includes(version);
  function validTask(task, record) {
    const draft = draftVersion(record.version);
    if (!fields(task, ["workspace", "source", "steps", "artifact", "budget", ...(draft || record.version === 5 ? ["intent"] : []),
        ...(record.version === 6 ? ["rejection"] : [])])
      || record.version === 5 && task.intent !== "answer"
      || draft && (task.intent !== "change_draft" || !window.PactTrailDraftUI?.localSource(task.source))) return false;
    if (record.version === 6 && (!window.PactTrailTaskUI.validRejection({ error: record.error, rejection: task.rejection })
      || task.rejection !== null && (record.status !== "error" || record.pending !== null))) return false;
    const source = task.source;
    if (source === null ? task.workspace !== "synthetic-project"
      : !window.PactTrailTaskUI.validSource(source) || task.workspace !== (source.kind === "selected_project" ? "selected-project" : "selected-text")) return false;
    const fileIds = source === null ? ["overview.txt", "plan.txt", "checks.txt"]
      : source.kind === "selected_project" ? source.files.map(item => item.file_id) : ["selected.txt"];
    if (!Array.isArray(task.steps) || task.steps.length > 8 || !task.steps.every((step, index) =>
      fields(step, ["index", "kind", "tool", "reason", "result"]) && integer(step.index, 8) && step.index > 0
      && (!index || task.steps[index - 1].index < step.index)
      && bounded(step.reason, step.kind === "finish" ? record.version === 5 ? 4096 : 2048 : 256)
      && (step.kind === "finish" ? step.tool === null : step.kind === "tool" && ["search", "read", draft ? "save_draft" : "save_report"].includes(step.tool))
      && (fields(step.result, ["status", "error"]) ? step.result.status === "error" && step.result.error !== null && knownError(step.result.error)
        : window.PactTrailTaskUI.validResult(step, fileIds)))) return false;
    const limits = { max_decisions: 8, max_tools: 7, max_read_bytes: 32768,
      max_total_decisions: 48, max_total_tools: 42, max_total_read_bytes: 196608 };
    const counters = ["decisions", "tools", "read_bytes", "total_decisions", "total_tools", "total_read_bytes"];
    const budget = task.budget;
    return fields(budget, [...Object.keys(limits), ...counters, "model_dispatches", "model_cost_nano", "deadline_seconds"])
      && Object.entries(limits).every(([key, limit]) => budget[key] === limit)
      && counters.every(key => integer(budget[key], limits["max_" + key]))
      && budget.decisions <= budget.total_decisions && budget.tools <= budget.total_tools && budget.read_bytes <= budget.total_read_bytes
      && integer(budget.model_dispatches, 48) && integer(budget.model_cost_nano)
      && Number.isFinite(budget.deadline_seconds) && budget.deadline_seconds > 0 && budget.deadline_seconds <= 3600
      && (task.artifact === null || validArtifact(task.artifact, record.run_id, false, draft));
  }

  function validDetail(value) {
    if (!fields(value, ["record", "recovery", "integrity", "report", ...(value && Object.hasOwn(value, "application") ? ["application"] : [])])
      || !keys(recoveries, value.recovery) || !keys(integrities, value.integrity)) return false;
    const record = value.record;
    if (!fields(record, ["version", "run_id", "revision", "created_at", "updated_at", "goal", "status", "error", "task", "pending", "cost",
        ...(record && [2, 3].includes(record.version) ? ["parent"] : []), ...(record && record.version === 3 ? ["followup_context"] : []),
        ...(record && Object.hasOwn(record, "artifact_reference") ? ["artifact_reference"] : [])])
      || ![1, 2, 3, 4, 5, 6].includes(record.version) || ([2, 3].includes(record.version) && !window.PactTrailFollowupUI?.validParent(record.parent, record.run_id))
      || !validSummary({ ...Object.fromEntries(summaryFields.filter(key => key in record).map(key => [key, record[key]])),
        error: null, recovery: value.recovery, integrity: value.integrity, has_report: record.task && record.task.artifact !== null })
      || !bounded(record.goal, record.version === 5 ? 4096 : 2048)
      || Object.hasOwn(record, "artifact_reference") && record.artifact_reference !== null
        && !window.PactTrailArtifactVersionsUI?.validIdentity(record.artifact_reference)
      || !knownError(record.error) || !validCost(record.cost) || !validTask(record.task, record)) return false;
    if (record.version === 3 && !window.PactTrailFollowupUI.validContext(record.followup_context, record.parent, record.task.source, record.run_id)) return false;
    const pending = record.pending;
    const draft = draftVersion(record.version);
    if (record.cost.ledger_version === 1 && record.task.budget.model_cost_nano !== record.cost.model_accounted_nano_usd) return false;
    if (pending !== null) {
      if (pending.kind === "search") {
        if (!fields(pending, ["kind", "tool", "index", "request_id", "reservation_nano_usd"])
          || pending.tool !== null || !integer(pending.index, 48) || pending.index < 1 || !identity(pending.request_id)
          || pending.reservation_nano_usd !== 750000 || record.cost.ledger_version !== 1
          || pending.index !== record.cost.search_calls || pending.reservation_nano_usd !== record.cost.search_reserved_nano_usd) return false;
      } else if (!fields(pending, ["kind", "tool", "index"]) || !integer(pending.index, 8) || pending.index < 1
        || (pending.kind === "model" ? pending.tool !== null : pending.kind !== "tool" || !["search", "read", draft ? "save_draft" : "save_report"].includes(pending.tool))) return false;
    }
    if (value.recovery === "write_unknown") {
      if (!pending || pending.kind !== "tool" || pending.tool !== (draft ? "save_draft" : "save_report")) return false;
    } else if (record.status === "running" || pending !== null ? value.recovery !== "interrupted" : value.recovery !== "terminal") return false;
    const artifact = record.task.artifact;
    if (Object.hasOwn(value, "application") && (!draft || !window.PactTrailDraftUI.validEditing(value.application, true)
      || value.application.run_id !== record.run_id || !artifact || value.application.artifact_sha256 !== artifact.sha256)) return false;
    return value.report === null || artifact !== null && value.recovery !== "write_unknown" && !["artifact_changed", "both_changed"].includes(value.integrity)
      && validArtifact(value.report, record.run_id, true, draft) && ["name", "sha256", "bytes"].every(key => value.report[key] === artifact[key])
      && (!draft || Boolean(window.PactTrailDraftUI.parse(value.report, record.run_id, record.task.source)));
  }

  function valid(data) {
    if (!fields(data, ["version", "mode", "read_only", "records", "selected", "error"])
      || data.version !== 1 || data.mode !== "task_history" || data.read_only !== true
      || !Array.isArray(data.records) || data.records.length > 24 || !data.records.every(validSummary)
      || new Set(data.records.map(item => item.run_id)).size !== data.records.length
      || !(data.error === null || recordError(data.error))) return false;
    if (data.selected === null) return true;
    if (data.error !== null || !validDetail(data.selected)) return false;
    const record = data.selected.record;
    const summary = data.records.find(item => item.run_id === record.run_id);
    return Boolean(summary) && summary.error === null && ["revision", "created_at", "updated_at", "goal", "status"]
      .every(key => summary[key] === record[key]) && summary.recovery === data.selected.recovery
      && summary.integrity === data.selected.integrity && summary.has_report === (record.task.artifact !== null && data.selected.recovery !== "write_unknown");
  }

  let request = null;
  let requestEpoch = 0;
  let current = null;
  let generation = 0;
  let busy = false;
  let artifactUrl = null;
  let artifact = null;
  let initialized = false;
  let filterActive = false;
  let filterQuery = "";
  let filterStatus = "all";
  let continueRecord = null;
  let referenceRecord = null;
  let versionsRecord = null;
  let sourcesRecord = null;
  const continuable = detail => Boolean(detail && (detail.record.status !== "complete" || detail.recovery !== "terminal"));
  const money = value => { const digits = String(value).padStart(10, "0"); return "$" + digits.slice(0, -9) + "." + digits.slice(-9); };
  const date = value => new Date(value * 1000).toISOString().replace("T", " ").replace(".000Z", " UTC");
  const paragraph = value => { const node = document.createElement("p"); node.textContent = value; return node; };
  const agentErrors = { history_unavailable: "无法核对本工作区的启动记录，原记录保留。请返回当前会话后重新打开；不会恢复或重试旧执行。" };
  const errorText = code => errors[code] || agentErrors[code] || window.PactTrailTaskUI.error(code) || errors.state_unavailable;

  function draftNotice(detail) {
    const record = detail.record;
    if (!draftVersion(record.version)) return "";
    const reconcile = detail.recovery === "write_unknown"
      ? "保存结果未知；请先核对任务记录与草案文件，不重发保存或应用，不恢复旧执行。"
      : ["unknown", "unavailable"].includes(detail.application?.state)
        ? "应用结果未确认；请先核对文件应用记录与目标文件，不重发或应用旧草案，不恢复旧执行。" : "";
    if (record.version !== 6 || record.task.rejection === null) return reconcile;
    const task = { error: record.error, rejection: record.task.rejection };
    const reason = record.task.rejection;
    return (reconcile ? reconcile + " " : "") + (reason.stage === "consumer" && reason.reason === "syntax_repair_unsupported"
      ? "离线模拟暂不支持此语法错误，仅支持失败行的空参数 def name() 缺冒号。"
      : reason.stage === "consumer" && reason.reason === "draft_repair_unsupported"
        ? "离线模拟仅支持草案受检行的空参数 def name() 缺冒号且仍有实际变更；本次未形成可用修复草案。"
      : "已保存的失败原因。") + window.PactTrailTaskUI.rejectionText(task) + "历史只读，不重发、不应用、不恢复旧执行。";
  }

  function clearReport() {
    if (artifactUrl !== null) URL.revokeObjectURL(artifactUrl);
    artifactUrl = null;
    artifact = null;
    window.PactTrailDraftUI?.clear("task-history-draft");
    text("task-history-report-text", "");
    show("task-history-report-text", false);
    for (const id of ["task-history-report-open", "task-history-report-download", "task-history-report-load"]) el(id).disabled = true;
  }

  function clearContext() {
    show("task-history-context", false);
    el("task-history-rounds").replaceChildren(); text("task-history-context-status", "");
  }

  async function renderContext(context, token) {
    show("task-history-context", true);
    text("task-history-context-status", "正在核对保存窗口内各轮报告正文…");
    try {
      const api = window.PactTrailFollowupUI;
      const verified = await api.verifyContext(context);
      if (token !== generation) return;
      if (!verified) throw new Error("digest");
      const rounds = api.contextRounds(context);
      api.renderRounds(rounds.map(item => item.parent), "task-history-rounds", rounds.map(item => item.report));
      text("task-history-context-status", "已核对保存的原目标与报告身份；以下从旧到新排列。旧报告是历史声明，不是本次工具观察。");
    } catch (_) {
      if (token !== generation) return;
      el("task-history-rounds").replaceChildren();
      text("task-history-context-status", "保存窗口的报告正文未通过 SHA256 校验，未展示未经确认的内容。");
    }
  }

  function controls() {
    el("task-history-refresh").disabled = busy || !request;
    const canFilter = filterActive && Boolean(request) && Boolean(current);
    show("task-history-filters", filterActive);
    for (const id of ["task-history-query", "task-history-filter-status"]) el(id).disabled = !canFilter;
    el("task-history-filter-clear").disabled = !canFilter || !el("task-history-query").value && filterStatus === "all";
    if (filterActive && !current) {
      el("task-history-list").replaceChildren();
      text("task-history-filter-count", "已加载摘要不可用，请刷新。");
      show("task-history-filter-empty", false); show("task-history-filter-selection", false);
    }
    const detail = current && current.selected;
    show("task-history-continue", Boolean(continueRecord) && continuable(detail));
    el("task-history-continue").disabled = busy || !continueRecord || !continuable(detail);
    show("task-history-reference", Boolean(referenceRecord) && Boolean(detail?.record.task.artifact));
    el("task-history-reference").disabled = busy || !referenceRecord || !artifact || !artifactUrl;
    show("task-history-versions", Boolean(versionsRecord) && Boolean(detail?.record.task.artifact));
    el("task-history-versions").disabled = busy || !versionsRecord || !detail?.record.task.artifact;
    show("task-history-sources", Boolean(sourcesRecord) && Boolean(detail));
    el("task-history-sources").disabled = busy || !sourcesRecord || !detail;
    el("task-history-report-load").disabled = busy || !detail || !detail.record.task.artifact
      || detail.recovery === "write_unknown"
      || ["artifact_changed", "both_changed"].includes(detail.integrity);
    for (const id of ["task-history-report-open", "task-history-report-download"]) el(id).disabled = busy || artifactUrl === null;
  }

  function renderSource(source) {
    const scope = el("task-history-source");
    if (source === null) { scope.replaceChildren(paragraph("固定样例：overview.txt、plan.txt、checks.txt；仅这些范围内文本。")); return; }
    const rows = [paragraph(source.kind === "selected_project" ? source.version === 2
      ? "已保存的网页快照与可信获取来源；只读查看不重新获取网页。" : "用户勾选的文件快照，不代表整个目录或项目。" : "用户主动选择的一份文本快照。"),
      paragraph(source.name + " · " + source.byte_count + " 字节 · SHA256 " + source.sha256)];
    if (source.kind === "selected_project") source.files.forEach(file => rows.push(paragraph(file.file_id + " · " + file.path
      + " · " + file.byte_count + " 字节 / " + file.line_count + " 行 · SHA256 " + file.sha256
      + (file.web ? "\n" + window.PactTrailTaskUI.webProvenance(file.web) : ""))));
    else rows.push(paragraph(source.file_id + " · " + source.line_count + " 行"));
    scope.replaceChildren(...rows);
  }

  function renderSteps(steps) {
    el("task-history-steps").replaceChildren(...steps.map(step => {
      const row = document.createElement("li"); row.className = "task-step";
      const heading = document.createElement("h4");
      heading.textContent = step.index + ". " + ({ search: "搜索", read: "读取", save_report: "保存报告", save_draft: "保存草案" }[step.tool] || "结束决定");
      row.append(heading, paragraph(step.reason));
      const result = step.result;
      if (result.status === "error") row.append(paragraph("已记录工具错误：" + errorText(result.error)));
      else if (step.tool === "read") {
        row.append(paragraph(result.file_id + " · 已读取 " + result.read_bytes + " 字节 · SHA256 " + result.sha256));
        const preview = document.createElement("pre"); preview.textContent = result.text;
        row.append(paragraph("保存的有限预览，可能不是全文："), preview);
      } else if (step.tool === "search") {
        row.append(paragraph("已读取 " + result.read_bytes + " 字节；以下仅为保存的匹配预览。"));
        result.matches.forEach(match => row.append(paragraph(match.file_id + " : " + match.line + " · " + match.text)));
      } else if (["save_report", "save_draft"].includes(step.tool)) row.append(paragraph(result.name + " · " + result.bytes + " 字节 · SHA256 " + result.sha256
        + (step.tool === "save_draft" ? " · 实际重读 " + result.read_bytes + " 字节" : "")));
      else row.append(paragraph("已记录结束决定；事实与质量仍按实际证据判断。"));
      return row;
    }));
    if (!steps.length) el("task-history-steps").append(paragraph("没有已提交的步骤结果。"));
  }

  function renderCost(cost) {
    const usage = cost.usage;
    const prefix = cost.mode === "synthetic" ? "synthetic 决策，无模型派发" : cost.mode === "synthetic_protocol" ? "synthetic 协议模拟费用" : "已记录协议费用（不代表服务端账单）";
    text("task-history-cost", "进程累计快照 · " + prefix + " · 调用 " + cost.calls + " 次 · 已核算 " + money(cost.accounted_nano_usd)
      + window.PactTrailTaskUI.ledgerCostText(cost)
      + " · 预留 " + money(cost.reserved_nano_usd) + " · 客户端预算 " + money(cost.budget_nano_usd)
      + (cost.cost_known ? " · 记录费用已知" : " · 最终费用未知，预留保留")
      + "。这些金额不是单任务费用。最近派发输入估算：" + (cost.input_estimate_tokens === null ? "无" : cost.input_estimate_tokens + " tokens")
      + (usage ? "；最后一次已核实用量：输入 " + usage.prompt_tokens + " / 输出 " + usage.completion_tokens + " / 合计 " + usage.total_tokens
        + " tokens（缓存命中 " + usage.prompt_cache_hit_tokens + " / 未命中 " + usage.prompt_cache_miss_tokens + "），不是累计 token。" : "；没有已核实 token 用量。")
      + "计量依据：" + cost.token_basis + "；价格依据：" + cost.pricing_basis + "。"
      + (cost.stopped ? "已停止：" + (cost.stop_reason ? errorText(cost.stop_reason) : "不再派发。") : ""));
  }

  async function verifyReport(report, token, record) {
    text("task-history-report-status", "正在核对成果正文与保存身份…");
    try {
      const bytes = new TextEncoder().encode(report.text);
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      if (token !== generation) return;
      const actual = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("");
      if (actual !== report.sha256) throw new Error("record_artifact_changed");
      const draft = draftVersion(record.version) ? await window.PactTrailDraftUI.verify(report, record.run_id, record.task.source) : null;
      if (token !== generation) return;
      if (draftVersion(record.version) && !draft) throw new Error("record_artifact_changed");
      artifact = report;
      artifactUrl = URL.createObjectURL(new Blob([bytes], { type: "text/plain;charset=utf-8" }));
      text("task-history-report-text", report.text);
      show("task-history-report-text", !draft);
      if (draft) window.PactTrailDraftUI.render(draft, "task-history-draft");
      if (draft && current.selected.application) text("task-history-draft-status", "草案生成时：未应用 · 未执行测试。实际应用结果见文件应用记录。");
      text("task-history-report-status", "正文 SHA256 与保存身份一致，可打开或下载这份内容。");
      controls();
    } catch (_) {
      if (token !== generation) return;
      clearReport();
      text("task-history-report-status", "成果正文未通过校验，未提供打开或下载。");
    }
  }

  function resetFilters() {
    filterQuery = ""; filterStatus = "all";
    el("task-history-query").value = ""; el("task-history-filter-status").value = "all";
    text("task-history-filter-count", "");
    show("task-history-filter-empty", false); show("task-history-filter-selection", false);
  }

  function paintList(data) {
    const readable = data.records.filter(item => !item.error);
    const matches = item => (!filterQuery || item.goal.toLowerCase().includes(filterQuery))
      && (filterStatus === "all" || item.status === filterStatus);
    const matched = filterActive ? readable.filter(matches) : readable;
    const visible = filterActive ? data.records.filter(item => item.error || matches(item)) : data.records;
    el("task-history-list").replaceChildren(...visible.map(item => {
      const button = document.createElement("button"); button.type = "button"; button.dataset.taskRunId = item.run_id;
      button.setAttribute("aria-pressed", String(Boolean(data.selected && data.selected.record.run_id === item.run_id)));
      const title = document.createElement("strong"); title.textContent = item.error ? "无法读取的任务记录" : item.goal;
      const summary = document.createElement("span"); summary.textContent = item.error ? errorText(item.error)
        : statuses[item.status] + " · " + recoveries[item.recovery] + " · " + date(item.updated_at);
      button.append(title, summary);
      button.addEventListener("click", () => act("open", item.run_id));
      return button;
    }));
    if (filterActive) {
      const errors = data.records.length - readable.length;
      text("task-history-filter-count", "匹配 " + matched.length + " / " + readable.length + " 份可读记录"
        + (errors ? " · 另有 " + errors + " 份异常记录（始终显示）" : ""));
      text("task-history-filter-empty", data.records.length ? "没有符合筛选条件的任务记录。" : "本组暂无任务记录。");
      show("task-history-filter-empty", matched.length === 0);
      show("task-history-filter-selection", Boolean(data.selected
        && !visible.some(item => item.run_id === data.selected.record.run_id)));
    }
  }

  function filterList() {
    if (!filterActive || !request || !current) return;
    filterQuery = el("task-history-query").value.slice(0, 128).trim().toLowerCase();
    const selected = el("task-history-filter-status").value;
    filterStatus = selected === "all" || Object.hasOwn(statuses, selected) ? selected : "all";
    paintList(current); controls();
  }

  function paint(data) {
    clearReport(); clearContext();
    window.PactTrailDraftUI?.renderApplication(null, "task-history-application");
    paintList(data);
    show("task-history-detail", data.selected !== null);
    text("task-history-status", data.error ? errorText(data.error) : data.records.length
      ? data.records.length + " 份任务记录 · 选择一项查看当时的目标、步骤和成果。" : "暂无任务记录。");
    if (!data.selected) return;
    const detail = data.selected;
    const record = detail.record;
    const draft = draftVersion(record.version);
    text("task-history-report-heading", draft ? "草案成果" : "保存的报告");
    text("task-history-report-load", draft ? "读取草案成果" : "读取报告正文");
    text("task-history-report-open", draft ? "打开草案 JSON" : "打开报告");
    text("task-history-report-download", draft ? "下载草案 JSON" : "下载报告");
    if (window.PactTrailFollowupUI) window.PactTrailFollowupUI.renderParent(record.parent, "task-history-parent");
    if (record.version === 3) renderContext(record.followup_context, generation);
    text("task-history-target", record.goal);
    text("task-history-time", "任务 " + record.run_id + " · 版本 " + record.revision + " · 创建 " + date(record.created_at) + " · 更新 " + date(record.updated_at));
    const notice = draftNotice(detail);
    text("task-history-record-state", statuses[record.status] + "（" + record.status + "）· " + recoveries[detail.recovery] + " · " + integrities[detail.integrity]
      + (record.error ? " · " + errorText(record.error) : "")
      + (notice ? " · " + notice : "")
      + (record.pending ? " · 当时尚待确认：" + (record.pending.kind === "model" ? "模型响应" : record.pending.kind === "search" ? "联网检索响应" : record.pending.tool) + "，不会重新派发。" : ""));
    renderSource(record.task.source); renderSteps(record.task.steps); renderCost(record.cost);
    const saved = record.task.artifact;
    window.PactTrailDraftUI?.renderApplication(detail.application, "task-history-application");
    text("task-history-report-identity", saved ? saved.name + " · " + saved.bytes + " 字节 · SHA256 " + saved.sha256 : "记录中没有已确认的成果身份。");
    text("task-history-report-status", saved ? "正文尚未读取；点击后只核对已保存文件。" : "成果文件的存在不能补写或推断任务成功。");
    if (["artifact_changed", "both_changed"].includes(detail.integrity)) text("task-history-report-status", errors.record_artifact_changed);
    if (detail.recovery === "write_unknown") text("task-history-report-status", "保存结果未知；请先核对任务记录与成果文件，不读取或下载成果，也不重发保存动作。");
    if (detail.report !== null) verifyReport(detail.report, generation, record);
  }

  function accept(data) {
    generation += 1;
    clearContext();
    if (!valid(data)) {
      current = null; clearReport(); el("task-history-list").replaceChildren(); show("task-history-detail", false);
      text("task-history-status", errors.invalid_response); controls(); return false;
    }
    current = data; paint(data); controls(); return true;
  }

  async function act(action, runId) {
    if (busy || !request) return;
    const token = ++requestEpoch;
    generation += 1;
    busy = true; clearReport(); clearContext(); controls();
    text("task-history-status", "正在读取已保存记录…");
    try {
      const data = await request("/task-history", { action, ...(runId ? { run_id: runId } : {}) });
      if (token !== requestEpoch) return;
      accept(data);
    } catch (error) {
      if (token !== requestEpoch) return;
      current = null; show("task-history-detail", false); clearReport();
      text("task-history-status", errorText(error.message));
    } finally {
      if (token === requestEpoch) { busy = false; controls(); }
    }
  }

  function open(download) {
    if (busy || !artifactUrl || !artifact) return;
    const link = document.createElement("a"); link.href = artifactUrl;
    if (download) link.download = artifact.name;
    else { link.target = "_blank"; link.rel = "noopener noreferrer"; }
    try { document.body.appendChild(link); link.click(); } finally { link.remove(); }
  }

  function mount(data, send, agent = false, prepareContinuation = null, prepareReference = null, prepareVersions = null, prepareSources = null) {
    requestEpoch += 1;
    request = send; busy = false; continueRecord = agent ? prepareContinuation : null;
    referenceRecord = agent ? prepareReference : null;
    versionsRecord = agent ? prepareVersions : null;
    sourcesRecord = agent ? prepareSources : null;
    filterActive = agent && Boolean(send); resetFilters();
    show("agent-history-controls", agent);
    show("workspace", false); show("task-history-view", true); document.title = "PactTrail · 只读任务记录";
    const skip = el("skip-link"); skip.href = "#task-history-list"; skip.textContent = "跳到任务记录";
    if (!initialized) {
      el("task-history-refresh").addEventListener("click", () => act("list"));
      el("task-history-report-load").addEventListener("click", () => current && current.selected && act("report", current.selected.record.run_id));
      el("task-history-report-open").addEventListener("click", () => open(false));
      el("task-history-report-download").addEventListener("click", () => open(true));
      el("task-history-continue").addEventListener("click", () => {
        if (!busy && continueRecord && continuable(current?.selected)) continueRecord(current.selected.record);
      });
      el("task-history-reference").addEventListener("click", () => {
        if (!busy && referenceRecord && artifact && artifactUrl && current?.selected)
          referenceRecord(current.selected.record);
      });
      el("task-history-versions").addEventListener("click", () => {
        if (!busy && versionsRecord && current?.selected?.record.task.artifact)
          versionsRecord(current.selected.record);
      });
      el("task-history-sources").addEventListener("click", () => {
        if (!busy && sourcesRecord && current?.selected) sourcesRecord(current.selected.record);
      });
      el("task-history-query").addEventListener("input", filterList);
      el("task-history-filter-status").addEventListener("change", filterList);
      el("task-history-filter-clear").addEventListener("click", () => {
        if (el("task-history-filter-clear").disabled) return;
        resetFilters(); paintList(current); controls(); el("task-history-query").focus();
      });
      initialized = true;
    }
    accept(data);
  }
  function closeHistory() {
    requestEpoch += 1; generation += 1; current = null; request = null; busy = false; continueRecord = null; referenceRecord = null; versionsRecord = null; sourcesRecord = null; clearReport(); clearContext();
    filterActive = false; resetFilters(); show("task-history-filters", false);
  }

  const available = data => Boolean(data && ["agent_demo", "agent_model"].includes(data.mode) && data.agent_history_available === true);
  const validAvailability = data => Boolean(data && (!Object.hasOwn(data, "agent_history_available") || available(data)));
  const groupOrder = (left, right) => left.created_at_ms === right.created_at_ms
    ? (left.task_id < right.task_id ? -1 : 1)
    : left.created_at_ms === null ? 1 : right.created_at_ms === null ? -1 : right.created_at_ms - left.created_at_ms;
  function validGroups(value) {
    const groups = value.groups;
    return Array.isArray(groups) && groups.length === value.tasks.length
      && groups.every(group => fields(group, ["task_id", "created_at_ms"]) && identity(group.task_id)
        && value.tasks.includes(group.task_id) && (group.created_at_ms === null || integer(group.created_at_ms, 253402300799999)))
      && new Set(groups.map(group => group.task_id)).size === groups.length && groups[0].task_id === value.current_task
      && groups.slice(2).every((group, index) => groupOrder(groups[index + 1], group) < 0);
  }
  function validAgent(value) {
    const names = ["version", "mode", "read_only", "tasks", "current_task", "selected_task", "history", "error"];
    if (value && value.version === 2) names.push("groups");
    if (!fields(value, names)
      || ![1, 2].includes(value.version) || value.mode !== "agent_history" || value.read_only !== true
      || !Array.isArray(value.tasks) || value.tasks.length > 64 || !value.tasks.every(identity)
      || new Set(value.tasks).size !== value.tasks.length || !identity(value.current_task) || !value.tasks.includes(value.current_task)
      || !(value.selected_task === null || identity(value.selected_task) && value.tasks.includes(value.selected_task))
      || !(value.error === null || value.error === "history_unavailable") || value.version === 2 && !validGroups(value)) return false;
    if (value.error !== null) return value.selected_task === null && value.history === null;
    return value.selected_task === null ? value.history === null : value.history !== null && valid(value.history);
  }

  function createAgent({ state, request: send, updateControls, continueTask, referenceArtifact, versionsArtifact, sourcesArtifact }) {
    let active = false, blocked = true, epoch = 0, selectedTask = null, currentTask = null, saved = null;
    const empty = () => ({ version: 1, mode: "task_history", read_only: true, records: [], selected: null, error: null });
    function restore() {
      epoch += 1; closeHistory(); selectedTask = null;
      if (!active) return;
      active = false;
      show("task-history-view", false); show("agent-history-controls", false); show("workspace", true);
      document.title = saved.title;
      el("skip-link").href = saved.href; text("skip-link", saved.skip);
      updateControls(); el("agent-history-open").focus();
    }
    function clear() {
      closeHistory(); mount(empty(), null, true);
      show("task-history-detail", false); text("task-history-status", "选择一个启动组，只读查看保存的任务。");
    }
    function prepareSelected(record) {
      if (!active || !selectedTask || !continueTask || current?.selected?.record !== record) return;
      const target = { task_id: selectedTask, run_id: record.run_id };
      restore(); continueTask(target);
    }
    function referenceSelected(record) {
      if (!active || !selectedTask || !referenceArtifact || current?.selected?.record !== record || !artifact) return;
      const target = { task_id: selectedTask, run_id: record.run_id };
      restore(); referenceArtifact(target);
    }
    function versionsSelected(record) {
      if (!active || !selectedTask || !versionsArtifact || current?.selected?.record !== record || !record.task.artifact) return;
      const target = { task_id: selectedTask, run_id: record.run_id };
      restore(); versionsArtifact(target);
    }
    function sourcesSelected(record) {
      if (!active || !selectedTask || !sourcesArtifact || current?.selected?.record !== record) return;
      const target = { task_id: selectedTask, run_id: record.run_id };
      restore(); sourcesArtifact(target);
    }
    function paintTasks(value) {
      const groups = value.version === 2 ? value.groups : [value.current_task, ...value.tasks.filter(taskId => taskId !== value.current_task)].map(task_id => ({ task_id }));
      el("agent-history-tasks").replaceChildren(...groups.map((group, index) => {
        const taskId = group.task_id;
        const button = document.createElement("button"); button.type = "button";
        button.dataset.agentTaskId = taskId; button.title = "任务组 " + taskId;
        button.textContent = (taskId === value.current_task ? "本次启动" : "此前启动" + (value.version === 2 ? " #" + index : ""))
          + " · " + taskId.slice(0, 8) + (value.version === 2 ? group.created_at_ms === null
            ? " · 时间未知" : " · 目录创建 " + date(Math.floor(group.created_at_ms / 1000)) : "");
        button.setAttribute("aria-pressed", String(taskId === value.selected_task));
        button.addEventListener("click", () => select(taskId));
        return button;
      }));
      text("agent-history-status", value.error ? errorText(value.error) : (value.selected_task === null
        ? value.tasks.length + " 个已登记启动组；请选择一组。" : "只读查看所选启动组；旧执行不会恢复，可准备新问题继续。")
        + (value.version === 2 ? "序号为列表位次；目录创建时间不代表任务结果或活动。" : ""));
    }
    async function read(body, token) {
      const value = await send("/agent-history", body);
      if (!active || token !== epoch) return null;
      if (!validAgent(value) || currentTask !== null && value.current_task !== currentTask
        || value.error === null && (body.action === "list" ? value.selected_task !== null : value.selected_task !== body.task_id)
        || value.error === null && ["open", "report"].includes(body.action) && value.history.error === null
          && (!value.history.selected || value.history.selected.record.run_id !== body.run_id)) {
        throw new Error("invalid_response");
      }
      currentTask = value.current_task;
      paintTasks(value);
      if (value.error !== null) throw new Error(value.error);
      return value;
    }
    function wrap(taskId, token) {
      return async (path, body) => {
        if (!active || token !== epoch || selectedTask !== taskId || path !== "/task-history"
          || !fields(body, body && body.action === "list" ? ["action"] : ["action", "run_id"])
          || !["list", "open", "report"].includes(body.action) || body.action !== "list" && !identity(body.run_id)) throw new Error("invalid_request");
        const value = await read({ action: body.action === "list" ? "select" : body.action, task_id: taskId,
          ...(body.action === "list" ? {} : { run_id: body.run_id }) }, token);
        if (!value) throw new Error("closed");
        return value.history;
      };
    }
    async function select(taskId) {
      if (!active || !identity(taskId)) return;
      const token = ++epoch; selectedTask = null; clear();
      text("agent-history-status", "正在读取所选启动组…");
      try {
        const value = await read({ action: "select", task_id: taskId }, token);
        if (!value) return;
        selectedTask = taskId; mount(value.history, wrap(taskId, token), true, continueTask ? prepareSelected : null,
          referenceArtifact ? referenceSelected : null, versionsArtifact ? versionsSelected : null,
          sourcesArtifact ? sourcesSelected : null);
      } catch (error) {
        if (active && token === epoch) text("agent-history-status", errorText(error.message));
      }
    }
    el("agent-history-open").addEventListener("click", async () => {
      if (active || blocked || !available(state())) return;
      const token = ++epoch;
      saved = { title: document.title, href: el("skip-link").href, skip: el("skip-link").textContent };
      active = true; selectedTask = null; currentTask = null; clear();
      el("agent-history-tasks").replaceChildren(); text("agent-history-status", "正在读取已登记的启动组…");
      updateControls(); el("agent-history-back").focus();
      try { await read({ action: "list" }, token); }
      catch (error) { if (active && token === epoch) text("agent-history-status", errorText(error.message)); }
    });
    el("agent-history-back").addEventListener("click", restore);
    return { active: () => active, close: restore,
      controls: unavailable => { blocked = unavailable; show("agent-history-open", available(state())); el("agent-history-open").disabled = blocked || !available(state()); } };
  }
  window.PactTrailTaskHistory = { mount, valid, validCost, validSummary, createAgent, validAgent, validAvailability,
    error: code => Object.hasOwn(errors, code) ? errors[code] : Object.hasOwn(agentErrors, code) ? agentErrors[code] : null, close: closeHistory };
})();
