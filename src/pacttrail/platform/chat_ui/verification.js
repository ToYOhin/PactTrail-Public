(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const fields = (value, names) => value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
  const id = value => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  const text = (value, limit) => typeof value === "string" && value.length <= limit
    && !/[\u0000-\u001f\u007f\ud800-\udfff]/u.test(value);
  const root = value => text(value, 4096) && (/^[A-Za-z]:[\\/]/.test(value) || value.startsWith("/"));
  const filePath = value => text(value, 320) && value.toLowerCase().endsWith(".py")
    && !/[\\:]/.test(value) && value.split("/").every(part => part && part !== "." && part !== "..");
  const statuses = ["idle", "running", "passed", "failed", "cancelled", "timed_out", "error", "unknown", "stale"];
  const errors = {
    verification_unavailable: "当前文件范围不可验证，请重新核对并确认。",
    verification_error: "验证未完成，请检查本地服务状态。",
    verification_timeout: "验证等待超时，请查看退出状态。",
    verification_unknown: "无法确认验证结果，请刷新状态。",
    verification_stale: "文件范围已变化，旧结果不能代表当前范围。",
  };
  const enabled = data => Boolean(data && Object.hasOwn(data, "verification"));
  function valid(data) {
    if (!enabled(data)) return true;
    const value = data.verification;
    if (!fields(value, ["version", "command", "target_root", "available", "next_id", "check_id",
      "project_sha256", "status", "exit_code", "files", "error", "reaped"])
      || value.version !== 1 || value.command !== "python_syntax" || !root(value.target_root)
      || typeof value.available !== "boolean" || !id(value.next_id)
      || !(value.check_id === null || id(value.check_id))
      || !(value.project_sha256 === null || hash(value.project_sha256))
      || !statuses.includes(value.status) || typeof value.reaped !== "boolean"
      || !(value.exit_code === null || Number.isSafeInteger(value.exit_code))
      || !(value.error === null || typeof value.error === "string" && Object.hasOwn(errors, value.error))
      || !Array.isArray(value.files) || value.files.length > 4
      || new Set(value.files.map(file => file && file.path)).size !== value.files.length
      || !value.files.every(file => fields(file, ["path", "status", "line"]) && filePath(file.path)
        && ["pending", "passed", "failed"].includes(file.status)
        && (file.status === "failed" ? file.line === null || Number.isSafeInteger(file.line) && file.line >= 1 && file.line <= 257 : file.line === null))) return false;
    const project = data.project, scope = data.conversation && data.conversation.scope;
    if (value.available && (data.busy || !project || !scope || scope.state !== "confirmed"
      || scope.sha256 !== project.sha256 || !Array.isArray(project.files)
      || !project.files.some(file => file && typeof file.path === "string" && file.path.toLowerCase().endsWith(".py")))) return false;
    if (value.status === "idle") return value.check_id === null && value.exit_code === null
      && value.files.every(file => file.status === "pending");
    if (!id(value.check_id) || !hash(value.project_sha256)) return false;
    if (value.status === "running") return data.busy === true && !value.available
      && value.exit_code === null && !value.reaped;
    if (value.status === "passed" || value.status === "failed") {
      if (!project || value.project_sha256 !== project.sha256
        || !value.reaped || !value.files.length) return false;
      return value.status === "passed" ? value.exit_code === 0 && value.files.every(file => file.status === "passed")
        : value.exit_code === 1 && value.files.some(file => file.status === "failed");
    }
    return true;
  }
  function create({ state, action, updateControls, prepareRepair, clearRepair }) {
    let unavailable = false, closed = false, submitting = false, copying = false;
    let summaryKey = "", summaryGeneration = 0;
    let captured = null, selected = null, lineButtons = [];
    let prepared = null;
    const missingSnapshot = "受测快照未保留，无法定位；需要重新验证。";
    const binding = data => [data.conversation_id, data.verification.check_id, data.verification.project_sha256].join(":");
    function summary(data) {
      if (closed || !enabled(data) || !valid(data)) return null;
      const value = data.verification;
      if (["idle", "running"].includes(value.status) || !id(value.check_id)) return null;
      const status = { passed: "通过", failed: "未通过", cancelled: "已取消", timed_out: "超时",
        error: "未完成", unknown: "结果未知", stale: "旧范围结果（不能代表当前范围）" }[value.status];
      const failed = value.files.filter(file => file.status === "failed");
      return ["Python 语法验证", "状态：" + status, "范围摘要：" + value.project_sha256,
        "退出码：" + (value.exit_code === null ? "尚未确认" : value.exit_code),
        failed.length ? "失败文件：\n" + failed.map(file => "- " + file.path + "："
          + (file.line === null ? "行号未知" : "第 " + file.line + " 行")).join("\n")
          : "失败文件：" + (value.status === "passed" ? "无" : "尚未确认")].join("\n");
    }
    const summaryIdentity = (data, content) => binding(data) + ":" + content;
    function renderSummary(data) {
      const content = summary(data), key = content === null ? "" : summaryIdentity(data, content);
      if (key !== summaryKey || content === null) {
        summaryKey = key; summaryGeneration += 1;
        el("project-verify-copy-feedback").textContent = "";
      }
      el("project-verify-summary").value = content || "";
      el("project-verify-summary").readOnly = true;
      el("project-verify-summary-region").hidden = content === null;
    }
    function clearLocation() {
      selected = null;
      el("project-verify-location").hidden = true;
      el("project-verify-location-title").textContent = "";
      el("project-verify-location-rows").replaceChildren();
    }
    function capture(data) {
      const key = binding(data);
      if (captured && captured.key !== key) { captured = null; clearLocation(); }
      if (captured || !id(data.conversation_id)
          || !["running", "failed"].includes(data.verification.status)
          || !window.PactTrailTaskUI || !window.PactTrailTaskUI.validProject(data.project, true)
          || data.project.sha256 !== data.verification.project_sha256) return;
      captured = { key, files: data.project.files.map(file => ({ path: file.path, text: file.text })) };
    }
    function sourceFor(data, file) {
      const value = data.verification;
      if (!captured || captured.key !== binding(data) || !["failed", "stale"].includes(value.status)
          || !value.reaped || value.exit_code !== 1 || file.status !== "failed" || !Number.isSafeInteger(file.line)) return null;
      const source = captured.files.find(source => source.path === file.path);
      if (!source) return null;
      const lines = source.text.replace(/\r\n|\r/g, "\n").split("\n");
      if (file.line > lines.length + 1) return null;
      if (file.line === lines.length + 1) lines.push("");
      return lines;
    }
    function repairGoal(data) {
      if (closed || !enabled(data) || !valid(data)) return null;
      const value = data.verification, project = data.project, conversation = data.conversation;
      if (!value.available || value.status !== "failed" || value.exit_code !== 1 || !value.reaped || value.error !== null
          || !project || !window.PactTrailTaskUI || !window.PactTrailTaskUI.validProject(project, true) || project.sha256 !== value.project_sha256
          || !conversation || conversation.error !== null || !conversation.scope || conversation.scope.state !== "confirmed"
          || conversation.scope.sha256 !== project.sha256 || !data.editing || data.editing.state === "unavailable"
          || data.editing.target_root !== value.target_root || !captured || captured.key !== binding(data)) return null;
      const failed = value.files.filter(file => file.status === "failed");
      if (!failed.length || failed.some(file => !sourceFor(data, file))) return null;
      const positions = failed.map(file => ({ id: project.files.find(entry => entry.path === file.path)?.file_id, line: file.line }));
      if (positions.some(file => !/^file-[1-4]$/.test(file.id))) return null;
      positions.sort((left, right) => left.id.localeCompare(right.id));
      const goal = "修复 Python 语法验证失败；check=" + value.check_id + "；sha=" + project.sha256
        + "；failed=" + positions.map(file => file.id + ":" + file.line).join(",");
      return Array.from(goal).length <= 256 ? goal : null;
    }
    function ready() {
      if (prepared && el("message").value === "") {
        prepared = null; el("message").readOnly = false;
      }
      if (closed) return false;
      if (!prepared) return true;
      const data = state().snapshot;
      return repairGoal(data) === prepared.goal && binding(data) === prepared.key
        && el("message").value === prepared.goal && el("task-intent").value === "change_draft";
    }
    function renderRepair(data) {
      const active = !closed && enabled(data) && valid(data), goal = active ? repairGoal(data) : null;
      const context = state() || {}, blocked = unavailable || closed || context.locked || context.mutating || data?.busy;
      const currentPreparation = ready();
      el("project-verify-repair-region").hidden = !active || !prepared && !["failed", "stale"].includes(data.verification.status);
      el("project-verify-repair").disabled = blocked || prepared !== null || !goal
        || typeof prepareRepair !== "function" || Boolean(el("message").value);
      el("project-verify-repair-clear").hidden = prepared === null;
      el("project-verify-repair-clear").disabled = blocked || prepared === null || typeof clearRepair !== "function";
      el("project-verify-repair-feedback").textContent = prepared
        ? currentPreparation ? "已预填当前失败的修复请求。请单独发送，再审阅并确认应用；不会自动执行。"
          : "预填反馈已过期或输入已变化。请先刷新状态，清除预填请求，再刷新并确认项目范围、重新验证；如仍失败，再采用当前失败反馈。不会自动重发。"
        : goal ? el("message").value ? "请先保留或清空已有输入，不会覆盖未发送的文字。"
            : "采用后仅预填修复草案请求；发送、应用和再验证均需单独操作。"
          : "仅当前已确认范围、已定位失败行和完整受测快照可用于修复；请重新核对或验证。";
    }
    function showLocation(data, file, focus = false) {
      const lines = sourceFor(data, file);
      if (!lines) { clearLocation(); return; }
      selected = { key: binding(data), path: file.path, line: file.line };
      const title = el("project-verify-location-title");
      title.textContent = (data.verification.status === "stale" ? "旧范围快照" : "受测快照")
        + " · " + file.path + " · 第 " + file.line + " 行";
      const rows = [];
      for (let index = Math.max(0, file.line - 3); index < Math.min(lines.length, file.line + 2); index += 1) {
        const row = document.createElement("div");
        row.className = "material-line" + (index + 1 === file.line ? " material-line-current" : "");
        const number = document.createElement("span"), content = document.createElement("span");
        number.className = "material-line-number"; number.textContent = String(index + 1);
        content.className = "material-line-text"; content.textContent = lines[index];
        row.appendChild(number); row.appendChild(content); rows.push(row);
      }
      el("project-verify-location-rows").replaceChildren(...rows);
      el("project-verify-location").hidden = false;
      if (focus) title.focus();
    }
    const current = () => {
      const context = state();
      return !closed && enabled(context.snapshot) && valid(context.snapshot) ? context : null;
    };
    function controls(value) {
      unavailable = value;
      const context = current(), verification = context && context.snapshot.verification;
      const blocked = !context || unavailable || context.locked || context.mutating || submitting;
      el("project-verify-start").disabled = blocked || !verification.available || context.snapshot.busy;
      el("project-verify-cancel").disabled = blocked || verification.status !== "running";
      el("project-verify-cancel").hidden = !verification || verification.status !== "running";
      el("project-verify-copy").disabled = blocked || copying || summary(context.snapshot) === null;
      renderRepair(context && context.snapshot);
      for (const { button, file } of lineButtons) button.disabled = blocked || !sourceFor(context.snapshot, file);
    }
    function render(data) {
      renderSummary(data);
      const active = enabled(data);
      el("project-verification").hidden = !active;
      if (closed || !active || !valid(data)) {
        captured = null; lineButtons = []; clearLocation();
        renderRepair(null);
        el("project-verify-location-notice").hidden = true;
        if (closed || !active) return;
        el("project-verify-target").textContent = "";
        el("project-verify-status").textContent = "无法核对验证状态，请刷新状态。";
        el("project-verify-exit").textContent = "";
        el("project-verify-files").replaceChildren();
        return;
      }
      const value = data.verification;
      capture(data);
      renderRepair(data);
      const labels = {
        idle: value.available ? "已确认以下文件，点击按钮开始语法验证。" : "先确认含 Python 文件的项目范围。",
        running: "正在验证 Python 语法…", passed: "Python 语法验证通过。", failed: "Python 语法验证未通过。",
        cancelled: "验证已取消。", timed_out: "验证超时。", error: "验证未完成。",
        unknown: "无法确认验证结果，请刷新状态。", stale: "文件范围已变化；以下结果属于旧范围。",
      };
      el("project-verify-target").textContent = value.target_root;
      el("project-verify-status").textContent = labels[value.status];
      el("project-verify-exit").textContent = value.exit_code === null ? "退出码：尚未确认" : "退出码：" + value.exit_code;
      lineButtons = [];
      el("project-verify-files").replaceChildren(...value.files.map(file => {
        const item = document.createElement("li");
        item.textContent = file.path + " · " + ({pending: "待验证", passed: "语法通过", failed: "语法失败"})[file.status]
          + (file.line === null ? "" : " · ");
        if (file.line !== null) {
          const button = document.createElement("button"), key = binding(data);
          button.type = "button"; button.className = "verification-line-link";
          button.dataset.verificationPath = file.path; button.textContent = "第 " + file.line + " 行";
          button.title = sourceFor(data, file) ? "查看受测快照中的这一行" : missingSnapshot;
          button.setAttribute("aria-controls", "project-verify-location");
          button.addEventListener("click", () => {
            const context = current();
            if (!context || unavailable || context.locked || context.mutating || key !== binding(context.snapshot)) return;
            const latest = context.snapshot.verification.files.find(latest => latest.path === file.path
              && latest.status === "failed" && latest.line === file.line);
            if (latest) showLocation(context.snapshot, latest, true);
          });
          item.appendChild(button); lineButtons.push({ button, file });
        }
        return item;
      }));
      const missing = lineButtons.some(({ file }) => !sourceFor(data, file));
      el("project-verify-location-notice").textContent = missing ? missingSnapshot : "";
      el("project-verify-location-notice").hidden = !missing;
      if (selected) {
        const file = value.files.find(file => selected.key === binding(data) && selected.path === file.path && selected.line === file.line);
        if (file) showLocation(data, file); else clearLocation();
      }
      controls(unavailable);
    }
    async function submit(cancel) {
      const context = current();
      if (!context || unavailable || context.locked || context.mutating || submitting) return;
      const value = context.snapshot.verification;
      if (cancel ? value.status !== "running" : !value.available || context.snapshot.busy) return;
      submitting = true; updateControls();
      try {
        await action("/verify", cancel ? { action: "cancel", check_id: value.check_id }
          : { action: "start", project_sha256: context.snapshot.project.sha256, check_id: value.next_id, confirmed: true });
      } finally { submitting = false; updateControls(); }
    }
    async function copySummary() {
      const context = current();
      if (!context || unavailable || context.locked || context.mutating || copying) return;
      const content = summary(context.snapshot);
      if (content === null) return;
      const key = summaryIdentity(context.snapshot, content), generation = summaryGeneration;
      const stillCurrent = () => {
        const latest = current();
        return latest && !unavailable && !latest.locked && !latest.mutating && generation === summaryGeneration
          && key === summaryIdentity(latest.snapshot, summary(latest.snapshot));
      };
      copying = true; el("project-verify-copy-feedback").textContent = ""; updateControls();
      try {
        if (typeof navigator === "undefined" || !navigator.clipboard
            || typeof navigator.clipboard.writeText !== "function") throw new Error("clipboard_unavailable");
        await navigator.clipboard.writeText(content);
        if (stillCurrent()) el("project-verify-copy-feedback").textContent = "验证摘要已复制。";
      } catch (_) {
        if (stillCurrent()) {
          el("project-verify-copy-feedback").textContent = "无法自动复制，请手动复制下方摘要。";
          el("project-verify-summary").focus(); el("project-verify-summary").select();
        }
      } finally { copying = false; updateControls(); }
    }
    el("project-verify-start").addEventListener("click", () => submit(false));
    el("project-verify-cancel").addEventListener("click", () => submit(true));
    el("project-verify-copy").addEventListener("click", copySummary);
    el("project-verify-repair").addEventListener("click", () => {
      controls(unavailable);
      if (el("project-verify-repair").disabled) return;
      const data = state().snapshot, goal = repairGoal(data);
      if (goal && prepareRepair(goal) === true) {
        prepared = { key: binding(data), goal }; el("message").readOnly = true; updateControls();
      }
    });
    el("project-verify-repair-clear").addEventListener("click", () => {
      controls(unavailable);
      if (el("project-verify-repair-clear").disabled) return;
      if (clearRepair() === true) { prepared = null; el("message").readOnly = false; updateControls(); }
    });
    return { render, controls, ready, close() { closed = true; prepared = null; captured = null;
      el("message").readOnly = false; clearLocation(); renderSummary(null); controls(true); } };
  }
  function repairFailureText(code) {
    if (code === "stale_request") return "修复请求已失效，本次请求在执行前被拒绝。请先点击“刷新状态”，清除预填请求，再刷新并确认项目范围、重新验证；如仍失败，再采用当前反馈并单独发送。不会自动重发。";
    if (["state_unavailable", "request_conflict"].includes(code)) return "修复请求的结果未确认。请先点击“刷新状态”，核对本轮任务记录和草案成果；结果确认前不要重复发送或应用。不会自动重试。";
    return null;
  }
  window.PactTrailVerificationUI = { valid, create, repairFailureText, error: code => Object.hasOwn(errors, code) ? errors[code] : null };
})();
