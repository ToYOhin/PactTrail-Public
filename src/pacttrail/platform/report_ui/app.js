(() => {
  "use strict";

  const sessionElement = document.querySelector('meta[name="pacttrail-session"]');
  const session = sessionElement ? sessionElement.content : "";
  if (sessionElement) sessionElement.remove();

  const element = (id) => document.getElementById(id);
  const text = (id, value) => { element(id).textContent = value; };
  const visible = (id, shown) => { element(id).hidden = !shown; };
  const actionIds = ["review", "approve", "deny", "publish", "resume"];
  const stateNames = {
    new: "待生成", reviewed: "待审批", approved: "已批准", denied: "已否决",
    intent: "等待恢复", staging: "等待恢复", complete: "已写入", unresolved: "状态待核实"
  };
  const stateNotes = {
    new: "尚未创建审阅记录。请先生成并检查报告。",
    reviewed: "报告内容已冻结。批准或否决前，请核对右侧的写入范围。",
    approved: "当前报告已获批准。需要点击“写入报告”才会开始写入。",
    denied: "这份报告已被否决，不会继续写入。",
    intent: "写入意图已记录。请确认当前内容后，显式恢复本次写入。",
    staging: "写入尚未完成。请显式恢复，让服务检查并继续本次操作。",
    complete: "报告已写入固定目标，当前内容与回执可只读查看。",
    unresolved: "状态或文件归属需要核实。请保留现场，不要重新生成或覆盖。"
  };
  const safeErrors = {
    invalid_request: "请求未被接受。请刷新状态并检查当前操作。",
    access_denied: "本地会话验证失败。请从当前本地服务地址重新打开页面。",
    approval_required: "当前报告尚未获批准。请刷新后检查审批状态。",
    denied: "当前报告已被否决，不能写入。",
    expired: "当前审批已过期，不能继续新的批准或写入。",
    source_changed: "样例来源与审阅记录不一致，写入已停止。",
    state_unavailable: "无法读取本地状态。请保留状态目录并核实服务。",
    unresolved: "无法确认状态或文件归属。请保留现场，停止写入。",
    output_unavailable: "服务未能完整返回结果。操作可能已经生效，请刷新状态。"
  };
  let snapshot = null;
  let busy = false;
  let locked = true;
  let reviewAttempted = false;
  let recoveryCommand = "";
  let copyBusy = false;

  const utc = (seconds) => new Date(seconds * 1000).toISOString().replace("T", " ").replace(".000Z", " UTC");
  const expired = () => snapshot && snapshot.record && snapshot.server_time >= snapshot.record.expires_at;

  function updateControls() {
    const state = snapshot ? snapshot.state : "unresolved";
    const disabled = busy || locked;
    element("refresh").disabled = busy;
    element("query").readOnly = state !== "new" || reviewAttempted;
    element("query").disabled = busy;
    visible("review", state === "new");
    visible("consent-row", state === "reviewed");
    visible("approve", state === "reviewed");
    visible("deny", state === "reviewed");
    visible("publish", state === "approved");
    visible("resume", state === "intent" || state === "staging");
    for (const id of actionIds) element(id).disabled = true;
    element("review").disabled = disabled || state !== "new" || reviewAttempted;
    element("consent").disabled = disabled || expired();
    element("approve").disabled = disabled || state !== "reviewed" || expired() || !element("consent").checked;
    element("deny").disabled = disabled || state !== "reviewed" || expired();
    element("publish").disabled = disabled || state !== "approved" || expired();
    element("resume").disabled = disabled || !["intent", "staging"].includes(state);
    element("refresh").textContent = busy ? "正在处理…" : "刷新状态";
    element("copy-recovery").disabled = busy || copyBusy || !recoveryCommand;
  }

  function showFailure(code, disconnected = false) {
    locked = true;
    element("consent").checked = false;
    text("notice", Object.hasOwn(safeErrors, code) ? safeErrors[code] : "未能确认操作结果。请先刷新状态，不要重复提交。");
    visible("notice", true);
    if (!disconnected) {
      recoveryCommand = "";
      text("recovery-command", "尚无可用恢复命令。");
      text("recovery-note", "状态未确认。保留现场，停止写入；请先核实状态，不能拼接或猜测恢复参数。");
    } else {
      text("recovery-note", recoveryCommand ? "连接中断，状态未刷新。此处保留上次核对的命令，仅用于重新打开并检查同一操作；不要重复提交写入。" : "连接中断且尚无已核对的恢复命令。保留现场，不要猜测参数或覆盖状态目录。");
    }
    text("copy-note", "");
  }

  function render(data) {
    if (!data || !Object.hasOwn(stateNames, data.state) || typeof data.server_time !== "number") {
      throw new Error("invalid_response");
    }
    const record = data.record;
    if (record && (typeof record.proposal_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(record.proposal_sha256) || typeof record.report_utf8 !== "string" || typeof record.proposal_json !== "string")) {
      throw new Error("invalid_response");
    }
    const command = data.recovery_command;
    if (command !== null && (typeof command !== "string" || command.length > 4096 || !command.startsWith("pacttrail evidence report serve --state-dir ") || /[\u0000-\u001f\u007f\u2028\u2029]/.test(command) || !record || ["new", "unresolved"].includes(data.state))) {
      throw new Error("invalid_response");
    }
    snapshot = data;
    recoveryCommand = command || "";
    text("recovery-command", recoveryCommand || "尚无可用恢复命令。");
    text("copy-note", "");
    locked = data.state === "unresolved";
    element("consent").checked = false;
    visible("notice", false);
    text("state", stateNames[data.state]);
    element("state").dataset.state = data.state;
    text("state-note", stateNotes[data.state]);
    text("target", data.target);
    text("root", data.root);
    text("operator", data.operator);
    text("checked-at", "服务端状态时间：" + utc(data.server_time));
    const stepIndex = { new: 0, reviewed: 1, approved: 2, intent: 2, staging: 2, complete: 3, denied: 1, unresolved: -1 }[data.state];
    ["step-review", "step-approve", "step-publish"].forEach((id, index) => {
      element(id).dataset.active = String(index === stepIndex);
      element(id).dataset.done = String(index < stepIndex);
      if (index === stepIndex) element(id).setAttribute("aria-current", "step");
      else element(id).removeAttribute("aria-current");
    });
    visible("empty-report", !record);
    visible("report", Boolean(record));
    visible("proposal-details", Boolean(record));
    visible("record-binding", Boolean(record));
    if (record) {
      reviewAttempted = true;
      element("query").value = record.query;
      text("report", record.report_utf8);
      text("proposal", record.proposal_json);
      text("report-size", record.report_bytes + " bytes · 已冻结");
      text("report-hash", record.report_sha256);
      text("proposal-hash", record.proposal_sha256);
      text("run-id", record.run_id);
      text("expires-at", utc(record.expires_at));
      text("expiry-note", data.state === "complete" ? "写入已完成；保留原审批有效期供核对。" : expired() ? "按当前服务端时间，此审批已过期。" : "最终有效性由服务端在操作时核验。");
    } else {
      text("report-size", "尚未生成");
      text("report", "");
      text("proposal", "");
    }
    const actionNotes = {
      new: "审阅内容生成后，可在此批准或否决。",
      reviewed: expired() ? "审批已过期。保留当前记录，不会自动续期。" : "确认完整报告与固定目标后，再作出本次决定。",
      approved: expired() ? "审批已过期，不能开始新的写入。" : "已批准当前摘要。点击下方按钮，将报告写入所示目标。",
      denied: "本次审批已结束。报告不会写入。",
      intent: "恢复仅针对当前审批和目标，不会创建新的报告请求。",
      staging: "恢复会核实已有文件。归属不明时会停止，不覆盖文件。",
      complete: "本次写入完成。刷新只核对结果，不会再次写入。",
      unresolved: "请保留现场并核实本地服务与状态目录。"
    };
    text("action-note", actionNotes[data.state]);
    const recoveryNotes = {
      new: "创建并核对审阅记录后，才会提供当前操作的命令。",
      denied: "本次已否决。重新打开只能核对记录，不会恢复写入。",
      complete: "报告已经完成。重新打开只核对原操作，不会再次写入。",
      unresolved: "状态或文件归属不明。保留现场并停止写入，不提供猜测的恢复命令。"
    };
    text("recovery-note", !recoveryCommand && record ? "当前状态或路径无法安全表示为恢复命令。保留现场，使用原 CLI 核实；不要猜测参数。" : recoveryNotes[data.state] || (expired() ? "审批已经过期。重新打开不会续期；已存在的写入意图仍由原恢复协议核验。" : "此命令只对应当前状态目录、操作者、运行标识和审批摘要。复制不会改变报告或审批。"));
  }

  async function request(path, body) {
    if (busy || !session || (path !== "/status" && locked)) return;
    busy = true;
    updateControls();
    try {
      const headers = { "X-PactTrail-Session": session };
      const options = { method: body === undefined ? "GET" : "POST", headers, cache: "no-store", credentials: "omit", mode: "same-origin", redirect: "error", referrerPolicy: "no-referrer" };
      if (body !== undefined) {
        headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
      }
      const response = await fetch(path, options);
      try {
        const data = await response.json();
        if (!response.ok) showFailure(data && data.error);
        else render(data);
      } catch {
        showFailure("invalid_request");
      }
    } catch {
      showFailure("output_unavailable", true);
    } finally {
      busy = false;
      updateControls();
    }
  }

  element("copy-recovery").addEventListener("click", async () => {
    if (element("copy-recovery").disabled || !recoveryCommand) return;
    copyBusy = true;
    updateControls();
    try {
      await navigator.clipboard.writeText(recoveryCommand);
      text("copy-note", "已复制。尚未运行命令；请在原安装环境的 PowerShell 中主动运行。");
    } catch {
      text("copy-note", "未能复制。可选中上方命令手动复制；本次未执行命令或改变报告状态。");
    } finally {
      copyBusy = false;
      updateControls();
    }
  });
  element("refresh").addEventListener("click", () => request("/status"));
  element("consent").addEventListener("change", updateControls);
  element("review-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (element("review").disabled || !element("query").reportValidity()) return;
    reviewAttempted = true;
    request("/review", { query: element("query").value });
  });
  for (const action of ["approve", "deny", "publish", "resume"]) {
    element(action).addEventListener("click", () => {
      if (element(action).disabled || !snapshot || !snapshot.record) return;
      request("/" + action, { proposal_sha256: snapshot.record.proposal_sha256 });
    });
  }
  if (session) request("/status");
  else { showFailure("access_denied"); updateControls(); }
})();
