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
  const kinds = ["report", "change_draft"];
  const target = value => fields(value, ["task_id", "run_id"]) && id(value.task_id) && id(value.run_id);
  const key = value => value ? value.task_id + ":" + value.run_id : "";
  function validIdentity(value) {
    return Boolean(fields(value, ["task_id", "run_id", "kind", "record_sha256", "artifact_sha256"])
      && id(value.task_id) && id(value.run_id) && kinds.includes(value.kind)
      && hash(value.record_sha256) && hash(value.artifact_sha256));
  }
  function validSide(value) {
    if (!fields(value, ["task_id", "run_id", "kind", "goal", "state", "source_state", "artifact"])
      || !id(value.task_id) || !id(value.run_id) || !(value.kind === null || kinds.includes(value.kind))
      || !(value.goal === null || bounded(value.goal, 4096) && value.goal.trim())
      || !["verified", "missing", "changed", "unknown", "unavailable"].includes(value.state)
      || ![null, "verified", "source_changed"].includes(value.source_state)
      || value.state !== "verified" && value.source_state !== null) return false;
    if (value.artifact === null) return value.state !== "verified";
    const artifact = value.artifact;
    return value.state === "verified" && kinds.includes(value.kind)
      && fields(artifact, ["name", "sha256", "bytes", "text"])
      && artifact.name === (value.kind === "change_draft" ? "draft-" : "report-") + value.run_id
        + (value.kind === "change_draft" ? ".json" : ".md")
      && hash(artifact.sha256) && integer(artifact.bytes) && artifact.bytes > 0 && artifact.bytes <= 8192
      && bounded(artifact.text, 8192) && new TextEncoder().encode(artifact.text).byteLength === artifact.bytes;
  }
  function valid(value) {
    if (!fields(value, ["version", "kind", "conversation_id", "revision", "task_id", "run_id", "relation", "current", "previous", "comparison"])
      || value.version !== 1 || value.kind !== "artifact_versions" || !id(value.conversation_id)
      || !integer(value.revision) || !id(value.task_id) || !id(value.run_id)
      || !validSide(value.current) || key(value.current) !== key(value)
      || !fields(value.relation, ["state", "reference"])
      || !["recorded", "legacy", "none", "unrecorded", "conflict"].includes(value.relation.state)
      || !(value.relation.reference === null || validIdentity(value.relation.reference))
      || !(value.previous === null || validSide(value.previous))) return false;
    const reference = value.relation.reference, previous = value.previous, comparison = value.comparison;
    if (["recorded", "legacy"].includes(value.relation.state) !== Boolean(reference)
      || (reference ? !previous || key(reference) !== key(previous) || reference.kind !== previous.kind
      || previous.artifact && reference.artifact_sha256 !== previous.artifact.sha256 : previous !== null)) return false;
    if (!fields(comparison, ["state", "kind", "files", "truncated"])
      || !["ready", "different_kind", "unavailable", "none", "unrecorded", "conflict"].includes(comparison.state)
      || !(comparison.kind === null || kinds.includes(comparison.kind)) || typeof comparison.truncated !== "boolean"
      || !Array.isArray(comparison.files) || comparison.files.length > 8
      || !comparison.files.every(file => fields(file, ["path", "change", "diff"])
        && bounded(file.path, 160) && file.path.trim() && !/[\r\n\t]/.test(file.path)
        && ["added", "removed", "changed", "unchanged"].includes(file.change) && bounded(file.diff, 16000))
      || new Set(comparison.files.map(file => file.path)).size !== comparison.files.length
      || comparison.files.reduce((sum, file) => sum + Array.from(file.diff).length, 0) > 16000
      || comparison.files.reduce((sum, file) => sum + (file.diff ? file.diff.split("\n").length - (file.diff.endsWith("\n") ? 1 : 0) : 0), 0) > 256) return false;
    return ["ready", "different_kind"].includes(comparison.state) ? Boolean(previous && reference && value.current.state === "verified"
      && previous.state === "verified" && comparison.files.length > 0 && (comparison.state === "ready"
        ? value.current.kind === previous.kind && comparison.kind === value.current.kind
        : value.current.kind !== previous.kind && comparison.kind === null))
      : comparison.files.length === 0 && comparison.truncated === false;
  }
  const stateText = { verified: "原文与保存身份一致", missing: "记录或原文已缺失", changed: "记录或原文已变化",
    unknown: "原保存结果未确认", unavailable: "原文暂不可核对" };
  const relationText = { recorded: "此新成果明确引用了以下旧成果。", legacy: "已从保存的会话记录核对这次引用关系。",
    none: "这份成果未记录对旧成果的引用。", unrecorded: "旧记录未保存可核对的引用关系。",
    conflict: "保存记录中的引用关系有冲突，未推断版本关系。" };
  const comparisonText = { ready: "以下比较两份保存原文的实际内容；差异不作为文件应用补丁。", different_kind: "两份成果类型不同，展示成果原文文本差异；差异不作为文件应用补丁。",
    unavailable: "原文尚不可完整核对，暂不生成差异。", none: "没有已记录的旧成果可比较。",
    unrecorded: "没有可核对的引用关系，未推断差异。", conflict: "引用关系有冲突，未生成差异。" };
  const errorText = code => ({ stale_request: "当前会话状态已变化，请重新读取。", record_missing: "任务记录已缺失，请刷新历史。",
    record_artifact_changed: "成果原文已变化，请重新核对。", record_conflict: "任务记录已变化，请重新读取。",
    closed: "服务已关闭，请重新打开当前页面。" })[code] || "暂时无法核对成果版本，请稍后重新读取。";
  function create({ state, request, updateControls }) {
    const panel = el("artifact-versions-panel");
    let owner = "", run = "", selected = null, generation = 0, pending = null, value = null, painted = null, status = "", closed = false;
    const currentTarget = data => target(data?.artifact_target) ? data.artifact_target : null;
    const available = data => !closed && data?.agent_history_available === true && id(data.conversation_id);
    const blocked = current => current.locked || current.mutating;
    const chosen = data => selected || currentTarget(data);
    function clear() {
      generation += 1; pending = null; value = null; painted = null; status = "";
      el("artifact-versions-content").hidden = true;
      for (const name of ["identity", "new", "old", "diff"]) el("artifact-versions-" + name).open = false;
      for (const name of ["relation", "identity-text", "new-goal", "old-goal", "new-state", "old-state", "new-text", "old-text", "comparison-note"])
        el("artifact-versions-" + name).textContent = "";
      el("artifact-versions-files").replaceChildren();
    }
    function paintSide(side, prefix) {
      el("artifact-versions-" + prefix + "-goal").textContent = side?.goal || "原目标未能核对。";
      el("artifact-versions-" + prefix + "-state").textContent = side
        ? stateText[side.state] + (side.source_state === "source_changed" ? "；当时来源快照另有变化，不影响已核对的成果正文身份。" : "")
        : "没有已记录的旧成果。";
      el("artifact-versions-" + prefix + "-text").textContent = side?.artifact?.text || "";
    }
    function paint() {
      if (painted === value) return;
      painted = value; el("artifact-versions-content").hidden = !value;
      if (!value) return;
      el("artifact-versions-relation").textContent = relationText[value.relation.state]
        + (value.current.state === "verified" ? "" : " 新成果：" + stateText[value.current.state] + "。")
        + (!value.previous || value.previous.state === "verified" ? "" : " 旧成果：" + stateText[value.previous.state] + "。");
      const identity = side => side ? "任务组 " + side.task_id + " · 运行 " + side.run_id
        + (side.artifact ? " · " + side.artifact.name + " · SHA256 " + side.artifact.sha256 : "") : "无";
      el("artifact-versions-identity-text").textContent = "新成果：" + identity(value.current) + "\n旧成果：" + identity(value.previous)
        + (value.relation.reference ? "\n本次引用保存的旧成果 SHA256 " + value.relation.reference.artifact_sha256 : "")
        + "\n引用关系只说明本次参考，不构成当前来源或文件应用授权。";
      paintSide(value.current, "new"); paintSide(value.previous, "old");
      el("artifact-versions-old").hidden = !value.previous;
      el("artifact-versions-diff").hidden = !["ready", "different_kind"].includes(value.comparison.state);
      el("artifact-versions-comparison-note").textContent = comparisonText[value.comparison.state]
        + (value.comparison.truncated ? " 差异预览已截断；两份完整原文仍可分别展开查看。" : "");
      el("artifact-versions-files").replaceChildren(...value.comparison.files.map(file => {
        const row = document.createElement("section"), heading = document.createElement("h3"), body = document.createElement("pre");
        heading.textContent = file.path + " · " + ({ added: "新增", removed: "移除", changed: "已修改", unchanged: "内容相同" })[file.change];
        body.className = "artifact-version-diff"; body.tabIndex = 0; body.textContent = file.diff || "内容相同。";
        row.append(heading, body); return row;
      }));
    }
    function render() {
      const current = state(), data = current.snapshot, nextOwner = available(data) ? data.conversation_id : "", nextRun = data?.task?.run_id || "";
      if (owner !== nextOwner || run !== nextRun) { owner = nextOwner; run = nextRun; selected = null; clear(); panel.open = false; }
      const candidate = chosen(data), active = currentTarget(data);
      panel.hidden = !nextOwner || !candidate;
      el("artifact-versions-load").disabled = !nextOwner || !candidate || blocked(current) || Boolean(pending);
      el("artifact-versions-load").textContent = value ? "重新核对关系与原文" : "读取关系与原文";
      el("artifact-versions-current").hidden = !active || key(candidate) === key(active);
      el("artifact-versions-current").disabled = !active || blocked(current) || Boolean(pending);
      el("artifact-versions-status").textContent = status || "只读核对保存的引用关系、原文和差异。";
      paint();
    }
    const owns = binding => !closed && owner === binding.owner && run === binding.run && generation === binding.generation
      && state().snapshot?.conversation_id === binding.owner && (state().snapshot?.task?.run_id || "") === binding.run;
    async function read() {
      render();
      const current = state(), data = current.snapshot, candidate = chosen(data);
      if (!available(data) || !candidate || blocked(current) || pending) return;
      const binding = { owner, run, generation, revision: data.revision }, requested = { ...candidate };
      selected = requested; pending = binding; status = "正在核对成果关系与两份原文…"; updateControls();
      try {
        const response = await request("/artifact-versions", { action: "read", conversation_id: owner, revision: data.revision, ...requested });
        if (!owns(binding)) return;
        if (state().snapshot?.revision !== binding.revision) throw Error("stale_request");
        if (!valid(response) || response.conversation_id !== binding.owner || response.revision !== binding.revision || key(response) !== key(requested))
          throw Error("invalid_response");
        for (const side of [response.current, response.previous]) if (side?.artifact) {
          const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(side.artifact.text));
          if (!owns(binding)) return;
          if (state().snapshot?.revision !== binding.revision) throw Error("stale_request");
          if (Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("") !== side.artifact.sha256)
            throw Error("record_artifact_changed");
        }
        value = response; painted = null; status = "已核对保存内容；可按需展开原文和差异。";
      } catch (error) {
        if (owns(binding)) { value = null; painted = null; clear(); status = errorText(error.message); }
      } finally { if (pending === binding) pending = null; updateControls(); }
    }
    function open(binding) {
      if (!target(binding) || !available(state().snapshot) || blocked(state())) return false;
      render(); selected = { ...binding }; clear(); render(); panel.open = true;
      el("artifact-versions-load").focus(); void read(); return true;
    }
    el("artifact-versions-load").addEventListener("click", read);
    el("artifact-versions-current").addEventListener("click", () => open(currentTarget(state().snapshot)));
    return { render, open, close() { closed = true; selected = null; clear(); panel.open = false; render(); } };
  }
  window.PactTrailArtifactVersionsUI = { valid, validIdentity, validSide, validTarget: target, create };
})();
