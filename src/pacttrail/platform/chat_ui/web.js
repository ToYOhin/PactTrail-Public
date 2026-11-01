(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const text = (id, value) => { el(id).textContent = value; };
  const show = (id, visible) => { el(id).hidden = !visible; };
  const fields = (value, names) => value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
  const identity = value => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const errors = {
    invalid_url: "网页地址未被接受；仅支持 1–3 个公开 HTTP(S) 地址，每个最多 512 个 ASCII 字符。",
    forbidden_address: "网页地址或重定向不属于允许的公开范围，未生成可关联快照。",
    invalid_response: "网页响应的类型、编码或结构未通过校验，未生成可关联快照。",
    response_limit: "网页内容超过有界获取或文本快照限制；没有截断或关联部分内容。",
    redirect_limit: "网页重定向未通过限制校验，没有继续获取。",
    timeout: "网页获取超时，未生成可关联快照。",
    cancelled: "网页获取已取消，没有关联部分内容。",
    unavailable: "网页暂时无法获取；没有自动重试。",
    busy: "上一批网页获取仍在结束中，请等待后再获取。",
  };
  function valid(data) {
    if (!data || !Object.hasOwn(data, "web")) return true;
    const value = data.web;
    if (!["agent_demo", "agent_model"].includes(data.mode)
      || !fields(value, ["version", "transport", "operation", "preview", "busy", "remaining_fetches"])
      || value.version !== 1 || !["http", "synthetic"].includes(value.transport) || typeof value.busy !== "boolean"
      || !Number.isInteger(value.remaining_fetches) || value.remaining_fetches < 0 || value.remaining_fetches > 6) return false;
    if ((value.busy || value.preview) && (data.busy || data.material || data.project || data.followup && data.followup.selected)) return false;
    const operation = value.operation;
    if (operation === null) return value.preview === null;
    if (!fields(operation, ["id", "status", "error"]) || !identity(operation.id)
      || !["running", "ready", "cancelled", "error"].includes(operation.status)) return false;
    if (operation.status === "error") return value.preview === null && typeof operation.error === "string"
      && Object.hasOwn(errors, operation.error) && !["cancelled", "busy"].includes(operation.error);
    if (operation.error !== null) return false;
    if (operation.status === "running") return value.busy && value.preview === null;
    if (operation.status === "cancelled") return value.preview === null;
    return !value.busy && window.PactTrailTaskUI.validProject(value.preview, true) && value.preview.version === 2
      && value.preview.files.every(file => file.web.transport === value.transport);
  }
  const canonical = value => Array.isArray(value) ? value.map(canonical)
    : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
  async function digest(value) {
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, "0")).join("");
  }
  async function verify(project) {
    const manifest = { ...project, files: project.files.map(({ text, ...entry }) => entry) };
    delete manifest.sha256;
    const hashes = await Promise.all(project.files.map(async file => await digest(file.text) === file.sha256));
    return hashes.every(Boolean) && await digest(JSON.stringify(canonical(manifest))) === project.sha256;
  }
  function create({ state, action, updateControls, inputsBlocked }) {
    let key = "";
    let generation = 0;
    let verified = false;
    let failed = false;
    const current = () => state().snapshot && state().snapshot.web;
    const blocksTask = () => Boolean(current() && (current().busy || current().preview));
    const urls = () => el("web-urls").value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    function validInput() {
      const values = urls();
      return values.length >= 1 && values.length <= 3 && new Set(values).size === values.length
        && values.every(window.PactTrailTaskUI.validWebURL);
    }
    function invalidate() { generation += 1; key = ""; verified = false; failed = false; }
    function controls(unavailable) {
      const { snapshot, locked, mutating } = state();
      const value = current();
      const blocked = unavailable || locked || mutating || !value;
      const attached = snapshot && (snapshot.material || snapshot.project || snapshot.followup && snapshot.followup.selected);
      const running = value && value.operation && value.operation.status === "running";
      const scoped = blocksTask();
      el("web-urls").disabled = blocked || scoped || Boolean(attached) || Boolean(snapshot && snapshot.busy);
      el("web-fetch").disabled = blocked || scoped || Boolean(attached) || Boolean(snapshot && snapshot.busy)
        || inputsBlocked() || value.remaining_fetches === 0 || !validInput();
      el("web-cancel").disabled = blocked || !running;
      el("web-clear").disabled = blocked || !(value.operation || value.preview || value.busy);
      el("web-attach").disabled = blocked || !value.preview || value.busy || !verified || Boolean(attached)
        || Boolean(snapshot && snapshot.busy) || inputsBlocked();
      if (scoped) {
        for (const id of ["material-file", "material-attach", "material-remove", "project-files", "project-preview", "project-attach",
          "project-remove", "followup-parent", "followup-round-count", "followup-select", "followup-refresh", "followup-clear", "task-use-material", "task-use-followup"]) el(id).disabled = true;
      }
    }
    function renderPreview(project) {
      el("web-preview").replaceChildren(...project.files.map(file => {
        const section = document.createElement("section"); section.dataset.webFileId = file.file_id;
        const title = document.createElement("h4"); title.textContent = file.file_id + " · " + file.path;
        const provenance = document.createElement("p"); provenance.className = "web-provenance";
        provenance.textContent = window.PactTrailTaskUI.webProvenance(file.web);
        const identity = document.createElement("p");
        identity.textContent = file.byte_count + " 字节 · " + file.line_count + " 行 · 正文 SHA256 " + file.sha256;
        const body = document.createElement("pre"); body.className = "web-body"; body.textContent = file.text;
        section.append(title, provenance, identity, body);
        return section;
      }));
    }
    function status(value) {
      const prefix = (value.transport === "synthetic" ? "synthetic 合成获取" : "公开 HTTP 获取")
        + " · 剩余 " + value.remaining_fetches + " / 6 批 · ";
      if (value.busy && (!value.operation || value.operation.status !== "running")) return prefix + "旧获取正在结束，迟到内容不会加入预览；结束前不能发起新获取或任务。";
      const operation = value.operation;
      if (!operation) return prefix + "填写网页地址并明确点击获取；不会自动连接网页或调用模型。";
      if (operation.status === "running") return prefix + "正在获取网页；尚未关联材料，也没有调用模型。";
      if (operation.status === "cancelled") return prefix + errors.cancelled;
      if (operation.status === "error") return prefix + errors[operation.error];
      return prefix + (failed ? "快照正文或来源清单摘要校验失败，不能关联。"
        : verified ? "正文与来源清单摘要已核对。请阅读预览，再明确关联到任务。" : "正在核对正文和可信来源清单摘要…");
    }
    function render(data) {
      const value = data && data.web;
      show("web-panel", Boolean(value));
      if (!value || !valid(data)) {
        invalidate(); el("web-preview").replaceChildren(); show("web-preview", false);
        text("web-status", value ? "网页获取状态未通过校验，不能关联或发送。" : ""); return;
      }
      const nextKey = JSON.stringify([data.conversation_id, data.revision, value.operation, value.preview]);
      if (nextKey !== key) {
        key = nextKey; generation += 1; verified = false; failed = false;
        el("web-preview").replaceChildren();
        if (value.preview) {
          const token = generation;
          void verify(value.preview).then(result => {
            if (token !== generation) return;
            verified = result; failed = !result;
            if (result) renderPreview(value.preview);
            text("web-status", status(value)); controls(false); updateControls();
          }).catch(() => {
            if (token !== generation) return;
            failed = true; verified = false;
            text("web-status", status(value)); controls(false); updateControls();
          });
        }
      }
      show("web-preview", Boolean(value.preview));
      show("web-attach", Boolean(value.preview));
      show("web-cancel", Boolean(value.operation && value.operation.status === "running"));
      text("web-status", status(value));
    }
    el("web-urls").addEventListener("input", () => { controls(false); updateControls(); });
    el("web-fetch").addEventListener("click", () => {
      controls(false);
      if (el("web-fetch").disabled) return;
      const fetchId = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
      return action("/web", { action: "fetch", fetch_id: fetchId, urls: urls() });
    });
    el("web-cancel").addEventListener("click", () => {
      controls(false);
      if (el("web-cancel").disabled) return;
      const fetchId = current().operation.id;
      invalidate(); return action("/web", { action: "cancel", fetch_id: fetchId });
    });
    el("web-clear").addEventListener("click", () => {
      controls(false);
      if (el("web-clear").disabled) return;
      invalidate(); return action("/web", { action: "clear" });
    });
    el("web-attach").addEventListener("click", () => {
      controls(false);
      if (el("web-attach").disabled) return;
      return action("/web", { action: "attach", fetch_id: current().operation.id, project_sha256: current().preview.sha256 });
    });
    return { render, controls, blocksTask, invalidate };
  }
  window.PactTrailWebUI = { create, valid, error: code => Object.hasOwn(errors, code) ? errors[code] : null };
})();
