(() => {
  "use strict";
  const el = (id) => document.getElementById(id);
  const text = (id, value) => { if (el(id).textContent !== value) el(id).textContent = value; };
  const show = (id, visible) => { el(id).hidden = !visible; };
  const validKey = (value) => typeof value === "string" && /^[\x21-\x7e]{1,512}$/.test(value);
  const money = (value) => "$" + (value / 1000000000).toFixed(8);
  const toolMoney = (value) => {
    const digits = String(value).padStart(10, "0");
    return "$" + digits.slice(0, -9) + "." + digits.slice(-9);
  };

  function label(connection) {
    if (connection.can_reactivate === true) return "任务已结束 · 可重新启用";
    if (connection.state === "ready") {
      const state = connection.stopped ? "已停止" : connection.calls >= connection.max_calls ? "调用额度已用尽"
        : connection.can_send ? "可发送" : "正在处理";
      return state + (connection.verified ? connection.transport_mode === "synthetic"
        ? " · synthetic 路径已完成" : " · 已验证" : " · 尚未验证")
        + (connection.cost_known === false ? " · 费用待核对" : "");
    }
    return { unconfigured: "未配置", configured_unverified: "已配置 · 尚未启用",
      failed: "连接失败 · 已停止", cleared: "已清除" }[connection.state];
  }

  function setup(data, locked) {
    const connection = data && data.connection;
    const exhausted = connection && (connection.calls >= connection.max_calls
      || connection.accounted_nano_usd >= connection.budget_nano_usd);
    const quietReady = data?.agent_history_available === true && data.answer_mode === "ordinary"
      && connection?.state === "ready" && !locked && !connection.stopped && !exhausted
      && connection.cost_known && (connection.can_send || data.busy);
    show("model-setup", Boolean(connection) && !quietReady);
    if (!connection) return;
    let title, note, button;
    if (locked) {
      title = "连接状态待核对"; note = "刷新核对 · 不自动重发"; button = "查看配置";
    } else if (connection.can_reactivate === true) {
      title = "等待重新启用"; note = "配置保留；重新启用后发送。旧任务不会重放。"; button = "查看连接";
    } else if (connection.can_configure === true && connection.stopped) {
      title = "可以重新配置"; note = "累计费用保留 · 配置不发送问题"; button = "配置模型";
    } else if (connection.stopped || connection.state === "failed") {
      const retained = data.conversation && !["unconfigured", "cleared"].includes(connection.state);
      title = retained ? "本次请求已停止" : "本次配置已结束";
      note = retained ? data.busy ? "后台退出中 · 输入保留，稍后核对"
        : "配置保留；核对结果与费用后继续。"
        : Object.hasOwn(connection, "can_configure") ? "核对资源与费用，再重新配置。"
          : "查看状态与费用；本次不能重新配置。";
      button = "查看详情";
    } else if (connection.state === "configured_unverified") {
      title = "已配置，等待启用"; note = "启用连接后发送"; button = "查看配置";
    } else if (exhausted) {
      title = "调用额度已用尽"; note = "查看调用与费用 · 发送已停用"; button = "查看详情";
    } else if (connection.cost_known === false) {
      title = "用量待核对"; note = "核对连接与费用 · 不自动重发"; button = "查看详情";
    } else if (connection.state === "ready") {
      title = connection.can_send ? "已就绪，可发送" : "模型正在处理";
      note = "发送时才调用模型"; button = "连接设置";
    } else {
      title = "尚未配置模型"; note = "配置 DeepSeek 后启用"; button = "配置模型";
    }
    text("model-setup-state", title);
    text("model-setup-note", note);
    text("model-setup-open", button);
  }

  function create({ state, action, updateControls, notice, openPanel, leaveRequest }) {
    const clearInput = () => { el("connection-key").value = ""; };
    let leaveCleanupIdentity = "";

    function leave() {
      clearInput();
      const data = state().snapshot, identity = data && data.conversation_id + ":" + data.revision;
      if (!data?.connection || !["configured_unverified", "ready"].includes(data.connection.state)
        || leaveCleanupIdentity === identity || typeof leaveRequest !== "function") return;
      leaveCleanupIdentity = identity;
      const disconnect = data.mode === "agent_model" && data.conversation?.persistent === true;
      const body = { conversation_id: data.conversation_id, revision: data.revision,
        action: disconnect ? "disconnect" : "clear" };
      try { void Promise.resolve(leaveRequest(body)).catch(() => {}); }
      catch (_) { /* Server lifecycle remains authoritative after a lost leave request. */ }
    }

    function controls(unavailable, busy) {
      const current = state(), snapshot = current.snapshot;
      const connection = snapshot && snapshot.connection;
      const configurable = connection && !busy && (Object.hasOwn(connection, "can_configure")
        ? connection.can_configure : !connection.stopped && ["unconfigured", "cleared"].includes(connection.state));
      el("connection-key").disabled = unavailable || !configurable;
      el("connection-configure").disabled = unavailable || !configurable || !validKey(el("connection-key").value);
      el("connection-activate").disabled = unavailable || !connection || busy || !(connection.can_reactivate === true
        || !connection.stopped && connection.state === "configured_unverified");
      text("connection-activate", connection?.can_reactivate === true ? "重新启用" : "启用本次配置");
      el("connection-clear").disabled = unavailable || !connection
        || !["configured_unverified", "ready", "failed"].includes(connection.state);
      el("model-setup-open").disabled = unavailable || busy || !connection;
      setup(snapshot, current.locked);
    }

    function render(data) {
      const connection = data.connection, tool = data.mode === "agent_model";
      const displayMoney = tool ? toolMoney : money;
      show("connection-panel", Boolean(connection));
      setup(data, state().locked);
      if (!connection) { clearInput(); return; }
      text("connection-state", label(connection));
      text("connection-model", "deepseek-flash · " + (tool ? "每任务最多 8 次决策，共用 60 秒期限" : "每轮最多等待 60 秒")
        + " · 已调用 " + connection.calls + " / " + connection.max_calls + " 次");
      text("connection-cost", (tool && connection.transport_mode === "synthetic" ? "synthetic 模拟费用 · " : "")
        + "客户端预算 " + displayMoney(connection.budget_nano_usd) + " · 已核算 "
        + displayMoney(connection.accounted_nano_usd) + window.PactTrailTaskUI.ledgerCostText(connection, displayMoney)
        + " · 预留 " + displayMoney(connection.reserved_nano_usd)
        + (connection.cost_known ? "" : " · 最终费用未知"));
      const usage = connection.usage;
      text("connection-usage", "本轮输入估算：" + (connection.input_estimate_tokens === null ? "尚无" : connection.input_estimate_tokens + " tokens")
        + (usage ? " · 实际输入 " + usage.prompt_tokens + " / 输出 " + usage.completion_tokens
          + " / 总计 " + usage.total_tokens + " tokens（缓存命中 " + usage.prompt_cache_hit_tokens
          + " / 未命中 " + usage.prompt_cache_miss_tokens + "）" : " · 尚无已核实用量"));
      text("connection-note", connection.can_reactivate === true
        ? "现有配置保留。重新启用仅为新问题开放发送，旧任务不会重放；累计调用与费用保留。"
        : connection.stopped ? connection.can_configure === true
          ? "可重新配置；累计调用与费用保留，配置和启用均不发送问题。"
          : Object.hasOwn(connection, "can_reactivate") ? "调用次数与费用保留。请核对资源、费用和剩余额度；当前不能重新启用。"
            : "本次配置已结束。调用次数与费用保留，本次服务不能重新配置。"
        : (tool && data.conversation?.persistent
          ? "离开页面只断开本页发送状态；如已安全保存，保存项保留。继续前需明确重新启用。配置和启用不会发送请求；"
          : "仅保留在本次服务内存中。配置和启用不会发送请求；")
          + (tool ? "启用后发送目标才执行工具任务；每任务最多 8 次、本次服务最多 48 次模型派发。"
            : data.answer_mode === "ordinary" ? "启用后可自由输入，最多派发 3 次。" : "只接受三轮固定问题。")
          + (tool && data.conversation?.persistent
            ? "清除配置按钮会同时删除已有的工作区保存项；关闭服务清理内存连接。"
            : "刷新或离开页面会尝试清除配置；关闭服务完成清理。"));
    }

    el("model-setup-open").addEventListener("click", () => {
      const current = state(), connection = current.snapshot && current.snapshot.connection;
      if (el("model-setup-open").disabled || current.locked || current.mutating || !connection) return;
      openPanel();
      el("model-config-details").open = true;
      el("connection-panel").scrollIntoView({ block: "nearest" });
      const target = !el("connection-key").disabled ? "connection-key"
        : !el("connection-activate").disabled ? "connection-activate" : "connection-heading";
      el(target).focus({ preventScroll: true });
    });
    el("connection-key").addEventListener("input", updateControls);
    el("connection-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const key = el("connection-key").value;
      clearInput();
      const snapshot = state().snapshot;
      if (el("connection-configure").disabled || !snapshot || !snapshot.connection) { updateControls(); return; }
      if (!validKey(key)) { notice("invalid_request"); updateControls(); return; }
      return action("/connection", { action: "configure", key });
    });
    el("connection-activate").addEventListener("click", () => {
      if (el("connection-activate").disabled) return;
      return action("/connection", { action: "activate" });
    });
    el("connection-clear").addEventListener("click", () => {
      clearInput();
      if (el("connection-clear").disabled) return;
      return action("/connection", { action: "clear" });
    });
    return { controls, render, clearInput, leave };
  }
  window.PactTrailModelConnectionUI = { create, label };
})();
