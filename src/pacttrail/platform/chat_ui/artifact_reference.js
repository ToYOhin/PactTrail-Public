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
  const bindingFor = value => value ? { task_id: value.task_id, run_id: value.run_id } : null;
  const key = value => value ? value.task_id + ":" + value.run_id : "";
  function validReference(value) {
    return Boolean(fields(value, ["version", "task_id", "run_id", "record_sha256", "goal", "kind", "artifact", "source", "state"])
      && value.version === 1 && id(value.task_id) && id(value.run_id) && hash(value.record_sha256)
      && bounded(value.goal, 4096) && value.goal.trim().length > 0
      && ["report", "change_draft"].includes(value.kind) && ["confirmed", "review_required"].includes(value.state)
      && fields(value.artifact, ["name", "sha256", "bytes", "text"])
      && value.artifact.name === (value.kind === "change_draft" ? "draft-" : "report-") + value.run_id
        + (value.kind === "change_draft" ? ".json" : ".md")
      && hash(value.artifact.sha256) && integer(value.artifact.bytes) && value.artifact.bytes > 0
      && value.artifact.bytes <= 8192 && bounded(value.artifact.text, 8192)
      && new TextEncoder().encode(value.artifact.text).byteLength === value.artifact.bytes
      && (value.source === null || fields(value.source, ["kind", "name", "sha256"])
        && ["selected_text", "selected_project"].includes(value.source.kind)
        && bounded(value.source.name, 80) && value.source.name.trim().length > 0
        && !/[\r\n\t]/.test(value.source.name) && hash(value.source.sha256)));
  }
  function valid(data) {
    return Boolean(data && (!Object.hasOwn(data, "artifact_target") || data.artifact_target === null
      || target(data.artifact_target) && data.agent_history_available === true)
      && (!data.conversation || !Object.hasOwn(data.conversation, "artifact_reference")
        || data.conversation.artifact_reference === null || validReference(data.conversation.artifact_reference)));
  }
  function validPreview(value) {
    return Boolean(fields(value, ["version", "kind", "conversation_id", "revision", "reference"])
      && value.version === 1 && value.kind === "artifact_reference_preview" && id(value.conversation_id)
      && integer(value.revision) && validReference(value.reference) && value.reference.state === "review_required");
  }
  const errors = { record_conflict: "原文或任务记录已变化，请重新读取。",
    record_artifact_changed: "成果原文已变化，未关联这份内容。", artifact_reference_changed: "成果原文已变化，请重新核对。",
    artifact_reference_unavailable: "这份成果暂不可用，请重新选择。", record_report_unavailable: "记录中没有可核对的成果。",
    stale_request: "当前状态已变化，请重新读取原文。", busy: "当前任务或资源尚未退出，请稍后核对。",
    record_missing: "任务记录已不存在，请重新选择。", closed: "服务已关闭。" };
  const error = code => Object.hasOwn(errors, code) ? errors[code] : null;
  const errorText = code => error(code) || "暂时无法核对这份成果，当前输入保留。";

  function create({ state, request, action, updateControls }) {
    const panel = el("artifact-reference-panel"), original = el("artifact-reference-original");
    let owner = "", run = "", selected = null, generation = 0, preview = null;
    let pending = null, status = "", painted = "", viewed = false, closed = false;
    const reference = data => validReference(data?.conversation?.artifact_reference) ? data.conversation.artifact_reference : null;
    const currentTarget = data => target(data?.artifact_target) ? data.artifact_target : null;
    const available = data => !closed && data?.agent_history_available === true && id(data.conversation_id) && valid(data);
    const blocked = current => current.locked || current.mutating || current.snapshot?.busy
      || current.snapshot?.verification?.status === "running" || current.snapshot?.draft_precheck?.status === "running"
      || current.snapshot?.draft_precheck?.reaped === false;
    const chosen = data => selected || bindingFor(reference(data)) || currentTarget(data);
    function clear() {
      generation += 1; preview = null; pending = null; status = ""; viewed = false; painted = "";
      original.open = false; el("artifact-reference-preview").hidden = true;
      for (const name of ["goal", "source", "text"]) el("artifact-reference-" + name).textContent = "";
    }
    function render() {
      const current = state(), data = current.snapshot, nextOwner = available(data) ? data.conversation_id : "";
      const nextRun = data?.task?.run_id || "";
      if (nextOwner !== owner || nextRun !== run) {
        owner = nextOwner; run = nextRun; selected = null; clear(); panel.open = false;
      }
      const attached = reference(data), candidate = chosen(data), currentArtifact = currentTarget(data);
      panel.hidden = !nextOwner || !candidate;
      const disabled = !nextOwner || blocked(current) || Boolean(pending);
      el("artifact-reference-summary").textContent = attached ? "本次参考 · " + (attached.kind === "change_draft" ? "已有草案" : "已有报告") : "基于已有成果修改";
      el("artifact-reference-attached").hidden = !attached;
      el("artifact-reference-attached").textContent = attached ? (attached.state === "confirmed" ? "已关联：" : "待重新核对：") + attached.artifact.name : "";
      el("artifact-reference-load").disabled = disabled || !candidate;
      el("artifact-reference-load").textContent = attached && key(candidate) === key(attached) ? "核对已关联原文" : "读取成果原文";
      el("artifact-reference-current").hidden = !currentArtifact || key(candidate) === key(currentArtifact);
      el("artifact-reference-current").disabled = disabled || !currentArtifact;
      el("artifact-reference-attach").hidden = !preview;
      el("artifact-reference-attach").disabled = disabled || !preview || !viewed;
      el("artifact-reference-attach").textContent = attached && attached.artifact.sha256 !== preview?.artifact.sha256 ? "将此成果替换为本次参考" : "关联为本次参考";
      el("artifact-reference-remove").hidden = !attached;
      el("artifact-reference-remove").disabled = disabled || !attached;
      el("artifact-reference-status").textContent = status || (preview ? viewed
        ? "可明确关联这份原文，再编辑修改要求并发送。" : "请展开原文，再决定是否关联。"
        : attached?.state === "review_required" ? "请重新读取并确认原文，再发送新问题。"
          : attached ? "选择保存报告或修改文件，输入修改要求后发送。" : "先查看成果原文，再决定是否作为本次参考。");
      const nextPaint = preview ? preview.record_sha256 + ":" + preview.artifact.sha256 : "";
      if (painted !== nextPaint) {
        painted = nextPaint; original.open = false; viewed = false;
        el("artifact-reference-preview").hidden = !preview;
        if (preview) {
          el("artifact-reference-goal").textContent = preview.goal;
          el("artifact-reference-source").textContent = preview.source
            ? "原成果使用「" + preview.source.name + "」。本次来源需重新选择和核对。"
            : "这份成果作为参考声明；本次来源与文件应用需另行确认。";
          el("artifact-reference-text").textContent = preview.artifact.text;
        }
      }
    }
    const owns = binding => !closed && binding.owner === owner && binding.run === run
      && binding.generation === generation && state().snapshot?.conversation_id === binding.owner
      && (state().snapshot?.task?.run_id || "") === binding.run;
    async function perform(kind) {
      render();
      const current = state(), data = current.snapshot, targetValue = chosen(data), attached = reference(data);
      if (!available(data) || blocked(current) || pending || kind === "read" && !targetValue
        || kind === "attach" && (!preview || !viewed) || kind === "remove" && !attached) return;
      const binding = { owner, run, generation, revision: data.revision }, previous = preview;
      if (kind === "read") selected = { ...targetValue };
      const body = kind === "remove" ? { action: kind, artifact_sha256: attached.artifact.sha256 }
        : { action: kind, ...targetValue, ...(kind === "attach"
          ? { record_sha256: previous.record_sha256, artifact_sha256: previous.artifact.sha256 } : {}) };
      pending = binding; status = kind === "read" ? "正在核对成果原文…" : kind === "attach" ? "正在关联参考…" : "正在移除参考…"; updateControls();
      try {
        const result = await (kind === "read" ? request("/artifact-reference", {
          ...body, conversation_id: owner, revision: data.revision,
        }) : action("/artifact-reference", body));
        if (!owns(binding)) return;
        if (kind === "read") {
          if (state().snapshot?.revision !== binding.revision) throw Error("stale_request");
          if (!validPreview(result) || result.conversation_id !== binding.owner || result.revision !== binding.revision
            || key(result.reference) !== key(targetValue)) throw Error("invalid_response");
          const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(result.reference.artifact.text));
          if (!owns(binding)) return;
          if (state().snapshot?.revision !== binding.revision) throw Error("stale_request");
          if (Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("") !== result.reference.artifact.sha256)
            throw Error("record_artifact_changed");
          preview = result.reference; painted = ""; viewed = false; status = "";
        } else {
          if (!result || result.conversation_id !== binding.owner || !integer(result.revision)
            || result.revision <= binding.revision || !valid(result)) throw Error("stale_request");
          if (kind === "attach") {
            const saved = reference(result);
            if (!saved || saved.state !== "confirmed" || key(saved) !== key(previous)
              || saved.artifact.sha256 !== previous.artifact.sha256) throw Error("invalid_response");
            status = "已关联；当前输入保留。选择保存报告或修改文件，编辑要求后发送。";
          } else {
            if (reference(result)) throw Error("invalid_response");
            status = "已移除参考，当前输入保留。";
          }
        }
      } catch (error) {
        if (owns(binding)) {
          status = errorText(error.message);
          if (kind !== "remove") { preview = null; painted = ""; viewed = false; original.open = false;
            el("artifact-reference-preview").hidden = true; el("artifact-reference-text").textContent = ""; }
        }
      } finally { if (pending === binding) { pending = null; updateControls(); } }
    }
    function open(value) {
      if (!target(value) || !available(state().snapshot) || blocked(state())) return false;
      render(); selected = { ...value }; clear(); render(); panel.open = true;
      el("artifact-reference-load").focus(); void perform("read"); return true;
    }
    original.addEventListener("toggle", () => {
      if (original.open && preview) { viewed = true; updateControls(); }
    });
    el("artifact-reference-load").addEventListener("click", () => perform("read"));
    el("artifact-reference-current").addEventListener("click", () => open(currentTarget(state().snapshot)));
    el("artifact-reference-attach").addEventListener("click", () => perform("attach"));
    el("artifact-reference-remove").addEventListener("click", () => perform("remove"));
    return { render, open,
      ready: () => reference(state().snapshot)?.state !== "review_required",
      fields: () => { const value = reference(state().snapshot); return value ? { artifact_reference_sha256: value.artifact.sha256 } : {}; },
      locate(value) { const attached = reference(state().snapshot), requested = bindingFor(value);
        const chosenValue = target(requested) ? requested : bindingFor(attached);
        return open(chosenValue); },
      close() { closed = true; selected = null; clear(); panel.open = false; render(); },
    };
  }
  window.PactTrailArtifactReferenceUI = { valid, validReference, validPreview, validTarget: target, error, create };
})();
