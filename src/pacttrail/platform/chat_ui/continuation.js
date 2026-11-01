(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const fields = (value, names) => value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
  const bounded = (value, max) => typeof value === "string" && Array.from(value).length <= max
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\ud800-\udfff]/u.test(value);
  const id = value => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const target = value => fields(value, ["task_id", "run_id"]) && id(value.task_id) && id(value.run_id);
  const stringList = (value, limit = 12) => Array.isArray(value) && value.length <= limit
    && value.every(item => bounded(item, 256) && item.trim().length > 0);
  const reasons = {
    busy: "当前任务或资源尚未退出，请核对状态后再准备。",
    already_complete: "这份记录已结束，可直接查看已有成果。",
  };
  function validPlan(value) {
    if (!fields(value, ["version", "kind", "conversation_id", "revision", "task_id", "run_id", "record_sha256",
      "status", "recovery", "integrity", "goal", "facts", "unfinished", "artifacts", "source", "suggestion", "can_prepare", "reason"])
      || value.version !== 1 || value.kind !== "task_continuation" || !id(value.conversation_id)
      || !integer(value.revision) || !id(value.task_id) || !id(value.run_id) || !hash(value.record_sha256)
      || !["running", "complete", "stopped", "error"].includes(value.status)
      || !["terminal", "interrupted", "write_unknown"].includes(value.recovery)
      || !["verified", "source_changed", "artifact_changed", "both_changed"].includes(value.integrity)
      || !bounded(value.goal, 4096) || !value.goal.trim() || !stringList(value.facts) || !stringList(value.unfinished, 16)
      || !Array.isArray(value.artifacts) || value.artifacts.length > 1
      || !value.artifacts.every(item => fields(item, ["kind", "name", "sha256", "bytes", "available", "application_state"])
        && ["report", "change_draft"].includes(item.kind) && bounded(item.name, 64) && item.name.length > 0
        && hash(item.sha256) && integer(item.bytes) && item.bytes > 0 && item.bytes <= 8192
        && typeof item.available === "boolean" && ["none", "applied", "partial", "unknown"].includes(item.application_state))
      || !fields(value.source, ["state", "name", "sha256"])
      || !(value.source.state === "none" ? value.source.name === null && value.source.sha256 === null
        : value.source.state === "recheck_required" && bounded(value.source.name, 80)
          && value.source.name.trim().length > 0 && hash(value.source.sha256))
      || !(fields(value.suggestion, ["message", "task_intent", "creation_path"])
        && ["answer", "report", "change_draft"].includes(value.suggestion.task_intent)
        && bounded(value.suggestion.message, 8192)
        && value.suggestion.message.trim().length > 0
        && (value.suggestion.creation_path === null || value.suggestion.task_intent === "change_draft"
          && bounded(value.suggestion.creation_path, 160) && !/[\r\n\t]/.test(value.suggestion.creation_path)))
      || typeof value.can_prepare !== "boolean"
      || !(value.can_prepare ? value.reason === null && value.suggestion !== null
        : Object.hasOwn(reasons, value.reason))) return false;
    return true;
  }
  const errorText = code => ({ record_conflict: "记录已变化，请重新查看续接计划。",
    stale_request: "当前状态已变化，请重新查看续接计划。", busy: reasons.busy,
    record_missing: "任务记录已不存在，请重新选择。", closed: "服务已关闭。",
    scope_required: "请先重新核对当前来源。" }[code] || "续接计划暂不可用，请核对状态后重试；当前输入保留。");

  function create({ state, request, action, composer, updateControls }) {
    const panel = el("continuation-panel");
    let owner = "", run = "", selected = null, selectionKey = "", generation = 0;
    let plan = null, pending = null, status = "", painted = null, closed = false;
    const key = value => value ? value.task_id + ":" + value.run_id : "";
    const currentTarget = data => target(data?.continuation_target) ? data.continuation_target : null;
    const available = data => !closed && data?.agent_history_available === true && id(data.conversation_id);
    const blocked = current => current.locked || current.mutating || current.snapshot?.busy
      || current.snapshot?.verification?.status === "running"
      || current.snapshot?.draft_precheck?.status === "running" || current.snapshot?.draft_precheck?.reaped === false;
    function clear() {
      generation += 1; plan = null; pending = null; painted = null; status = "";
      el("continuation-plan").hidden = true; el("continuation-question").open = false;
      for (const name of ["facts", "artifacts", "unfinished"]) el("continuation-" + name).replaceChildren();
      for (const name of ["goal", "source-note", "message"]) el("continuation-" + name).textContent = "";
    }
    function rows(name, values, empty) {
      el("continuation-" + name).replaceChildren(...(values.length ? values : [empty]).map(value => {
        const item = document.createElement("li"); item.textContent = value; return item;
      }));
    }
    function paint() {
      const next = plan ? plan.record_sha256 : "";
      if (next === painted) return;
      painted = next; el("continuation-question").open = false;
      el("continuation-plan").hidden = !plan;
      if (!plan) return;
      el("continuation-goal").textContent = plan.goal;
      rows("facts", plan.facts, "没有已确认完成的步骤。");
      rows("unfinished", plan.unfinished, "没有可确认的未完成项，请结合原目标继续准备。");
      rows("artifacts", plan.artifacts.map(item => (item.kind === "change_draft" ? "草案" : "报告")
        + " · " + item.name + (item.available ? " · 可在任务记录查看" : " · 正文待核对")
        + ({ none: "", applied: " · 已应用", partial: " · 部分应用", unknown: " · 应用结果待核对" }[item.application_state])),
        "没有已确认保存的成果。");
      el("continuation-source-note").textContent = plan.source.state === "recheck_required"
        ? "原任务关联「" + plan.source.name + "」。准备后请重新核对当前来源。"
        : "准备只填入新问题；本次参考与授权需重新选择。";
      el("continuation-message").textContent = plan.suggestion?.message || "";
      el("continuation-question").hidden = !plan.suggestion;
    }
    function render() {
      const current = state(), data = current.snapshot;
      const nextOwner = available(data) ? data.conversation_id : "", nextRun = data?.task?.run_id || "";
      if (nextOwner !== owner || nextRun !== run) {
        owner = nextOwner; run = nextRun; selected = null; selectionKey = ""; clear(); panel.open = false;
      }
      const candidate = selected || currentTarget(data), nextKey = key(candidate);
      if (nextKey !== selectionKey) { selectionKey = nextKey; clear(); panel.open = false; }
      panel.hidden = !nextOwner || !candidate;
      const disabled = !nextOwner || !candidate || blocked(current) || Boolean(pending);
      el("continuation-load").disabled = disabled;
      el("continuation-load").textContent = plan ? "重新核对计划" : "查看续接计划";
      el("continuation-prepare").hidden = !plan;
      el("continuation-prepare").disabled = disabled || !plan?.can_prepare || !composer.blank(composer.capture());
      el("continuation-source").hidden = !plan || plan.source.state !== "recheck_required";
      el("continuation-source").disabled = disabled || el("composer-files").disabled;
      el("continuation-status").textContent = status || (plan && !plan.can_prepare ? reasons[plan.reason]
        : plan && !composer.blank(composer.capture()) ? "当前输入和任务选项会保留；请先保存，或清空内容、路径并切回普通对话后再准备。"
          : "查看后可准备为新问题，由你编辑并发送。");
      paint();
    }
    const owns = binding => !closed && binding.generation === generation && binding.owner === owner
      && state().snapshot?.conversation_id === binding.owner && (state().snapshot?.task?.run_id || "") === binding.run
      && selectionKey === binding.selectionKey;
    async function perform(kind) {
      render();
      const current = state(), data = current.snapshot, chosen = selected || currentTarget(data);
      if (!available(data) || !chosen || blocked(current) || pending || kind === "prepare"
        && (!plan?.can_prepare || !composer.blank(composer.capture()))) return;
      const before = composer.capture(), previous = plan;
      const binding = { owner, run, generation, selectionKey, kind };
      const body = { action: kind, conversation_id: owner, revision: data.revision, ...chosen,
        ...(kind === "prepare" ? { record_sha256: previous.record_sha256 } : {}) };
      pending = binding; status = kind === "plan" ? "正在读取续接计划…" : "正在准备新问题…"; updateControls();
      try {
        const result = await (kind === "prepare" ? action("/continuation", {
          action: kind, ...chosen, record_sha256: previous.record_sha256,
        }) : request("/continuation", body));
        if (!owns(binding)) return;
        if (kind === "plan") {
          if (!validPlan(result) || result.conversation_id !== body.conversation_id || result.revision !== body.revision
            || result.task_id !== body.task_id || result.run_id !== body.run_id) throw Error("invalid_response");
          plan = result; painted = null; status = "";
        } else {
          if (!result || result.conversation_id !== body.conversation_id || !integer(result.revision)
              || result.revision <= body.revision) throw Error("stale_request");
          status = composer.install(previous.suggestion, result, before)
            ? "已准备到输入框；请核对本次参考，编辑后发送。"
            : "已保留当前新写的内容；续接问题仍可查看，清空后可重新准备。";
        }
      } catch (error) {
        if (owns(binding)) { status = errorText(error.message); if (kind === "plan") { plan = null; painted = null; } }
      } finally {
        if (pending === binding) { pending = null; updateControls(); }
      }
    }
    el("continuation-load").addEventListener("click", () => perform("plan"));
    el("continuation-prepare").addEventListener("click", () => perform("prepare"));
    el("continuation-source").addEventListener("click", () => {
      render(); if (!el("continuation-source").disabled && !el("continuation-source").hidden) el("composer-files").click();
    });
    return { render,
      open(value) {
        if (!target(value) || !available(state().snapshot) || blocked(state())) return false;
        render(); selected = { ...value }; selectionKey = key(selected); clear(); render();
        panel.open = true; el("continuation-load").focus(); void perform("plan"); return true;
      },
      close() { closed = true; selected = null; clear(); panel.open = false; render(); },
    };
  }
  window.PactTrailContinuationUI = { validPlan, validTarget: target, create };
})();
