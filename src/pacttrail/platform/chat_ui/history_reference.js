(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const fields = (value, names) => value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
  const id = value => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const text = (value, max) => typeof value === "string" && Array.from(value).length <= max
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\ud800-\udfff]/u.test(value);
  const kinds = { answer: "问答", report: "报告", change_draft: "草案" };
  const bytes = value => new TextEncoder().encode(value).byteLength;
  const bindingFields = ["version", "conversation_id", "run_id", "request_revision", "turn_sha256", "source_sha256", "artifact_sha256", "artifact_kind", "context_sha256"];
  const recordFields = [...bindingFields, "id", "summary_version", "text", "text_sha256"];
  function validSummaryBinding(value) {
    return Boolean(fields(value, bindingFields) && value.version === 1 && id(value.conversation_id) && id(value.run_id)
      && integer(value.request_revision) && hash(value.turn_sha256) && hash(value.context_sha256)
      && (value.source_sha256 === null || hash(value.source_sha256))
      && (value.artifact_kind === null ? value.artifact_sha256 === null
        : ["report", "change_draft"].includes(value.artifact_kind) && hash(value.artifact_sha256)));
  }
  function validSummaryText(value) { return text(value, 2048) && value.trim().length > 0 && bytes(value) <= 2048; }
  function validSummaryRecord(value, projection = false) {
    return Boolean(fields(value, [...recordFields, ...(projection ? ["adopted", "state"] : [])])
      && validSummaryBinding(Object.fromEntries(bindingFields.map(name => [name, value[name]])))
      && id(value.id) && integer(value.summary_version) && value.summary_version > 0 && value.summary_version <= 16
      && validSummaryText(value.text) && hash(value.text_sha256)
      && (!projection || typeof value.adopted === "boolean" && ["current", "stale"].includes(value.state)));
  }
  function validSummaryPlan(value, conversationId = null) {
    if (!fields(value, ["version", "state", "binding", "items", "max_records", "max_text_bytes"])
      || value.version !== 1 || value.max_records !== 16 || value.max_text_bytes !== 2048
      || !["none", "current", "stale"].includes(value.state) || !Array.isArray(value.items) || value.items.length > 16
      || !value.items.every(item => validSummaryRecord(item, true) && (!conversationId || item.conversation_id === conversationId))
      || new Set(value.items.map(item => item.id)).size !== value.items.length) return false;
    const active = value.items.filter(item => item.adopted), stale = active.some(item => item.state === "stale");
    if (new Set(active.map(item => item.run_id)).size !== active.length) return false;
    return value.state === "none" ? !active.length && value.binding === null
      : active.length > 0 && hash(value.binding) && (value.state === "stale") === stale;
  }
  function validSummaryCandidate(value, candidate) {
    return Boolean(candidate && fields(value, ["version", "mode", "conversation_id", "revision", "candidate"])
      && value.version === 1 && value.mode === "context_summary" && id(value.conversation_id) && integer(value.revision)
      && fields(value.candidate, ["version", "binding", "text", "original_bytes", "truncated"])
      && value.candidate.version === 1 && validSummaryBinding(value.candidate.binding)
      && value.candidate.binding.conversation_id === value.conversation_id && value.candidate.binding.run_id === candidate.run_id
      && value.candidate.binding.artifact_sha256 === candidate.artifact_sha256
      && value.candidate.binding.artifact_kind === (candidate.has_artifact ? candidate.intent : null)
      && validSummaryText(value.candidate.text) && integer(value.candidate.original_bytes)
      && typeof value.candidate.truncated === "boolean");
  }
  function validMetadata(value) {
    if (Object.hasOwn(value || {}, "summary_plan") && !validSummaryPlan(value.summary_plan, value.selected)) return false;
    const present = value && ["history_selection", "history_candidates"].filter(name => Object.hasOwn(value, name));
    if (!present || !present.length) return true;
    if (present.length !== 2) return false;
    const selection = value.history_selection, items = value.history_candidates;
    return Boolean(fields(selection, ["version", "mode", "run_ids"]) && selection.version === 1
      && ["all", "selected"].includes(selection.mode) && Array.isArray(selection.run_ids)
      && selection.run_ids.length <= 32 && selection.run_ids.every(id)
      && new Set(selection.run_ids).size === selection.run_ids.length
      && Array.isArray(items) && items.length <= 32
      && items.every(item => fields(item, ["run_id", "title", "intent", "has_artifact", "artifact_sha256"])
        && id(item.run_id) && text(item.title, 80) && item.title.length > 0
        && Object.hasOwn(kinds, item.intent) && typeof item.has_artifact === "boolean"
        && (item.has_artifact ? hash(item.artifact_sha256) : item.artifact_sha256 === null)
        && (item.intent === "answer" || item.has_artifact))
      && new Set(items.map(item => item.run_id)).size === items.length
      && JSON.stringify(items.filter(item => selection.run_ids.includes(item.run_id)).map(item => item.run_id))
        === JSON.stringify(selection.run_ids)
      && (selection.mode !== "all" || selection.run_ids.length === items.length)
      && value.context_turns === selection.run_ids.length);
  }
  function validOriginal(value, candidate) {
    if (!value || !candidate || !fields(value, ["version", "mode", "conversation_id", "revision", "turn"])
      || value.version !== 1 || value.mode !== "history_reference" || !id(value.conversation_id)
      || !integer(value.revision)) return false;
    const turn = value.turn;
    if (!fields(turn, ["run_id", "request_revision", "goal", "intent", "status", "error", "answer", "source", "artifact", "application",
      ...["web_search", "artifact_reference", "history_selection", "derived_summaries"].filter(name => Object.hasOwn(turn || {}, name))])
      || turn.run_id !== candidate.run_id || turn.intent !== candidate.intent || turn.status !== "complete"
      || turn.error !== null || !integer(turn.request_revision) || !text(turn.goal, 4096) || !turn.goal.trim()
      || !text(turn.answer, 4096) || !turn.answer.trim()) return false;
    if (Object.hasOwn(turn, "history_selection") && (!fields(turn.history_selection, ["version", "run_ids"])
      || turn.history_selection.version !== 1 || !Array.isArray(turn.history_selection.run_ids)
      || turn.history_selection.run_ids.length > 32 || !turn.history_selection.run_ids.every(id)
      || new Set(turn.history_selection.run_ids).size !== turn.history_selection.run_ids.length)) return false;
    if (Object.hasOwn(turn, "derived_summaries") && (!Array.isArray(turn.derived_summaries) || turn.derived_summaries.length > 16
      || !turn.derived_summaries.every(item => validSummaryRecord(item) && item.conversation_id === value.conversation_id)
      || new Set(turn.derived_summaries.map(item => item.id)).size !== turn.derived_summaries.length)) return false;
    const artifact = turn.artifact;
    if (candidate.has_artifact !== Boolean(artifact)) return false;
    return !artifact || fields(artifact, ["name", "sha256", "bytes", "text"])
      && artifact.name === (turn.intent === "change_draft" ? "draft-" : "report-") + turn.run_id
        + (turn.intent === "change_draft" ? ".json" : ".md")
      && artifact.sha256 === candidate.artifact_sha256 && hash(artifact.sha256)
      && integer(artifact.bytes) && artifact.bytes > 0 && artifact.bytes <= 8192
      && text(artifact.text, 8192) && new TextEncoder().encode(artifact.text).byteLength === artifact.bytes;
  }
  function create({ state, request, action, updateControls }) {
    const panel = el("history-reference-panel"), original = el("history-reference-original");
    let owner = "", generation = 0, stamp = "", selection = [], rows = new Map(), pending = null;
    let closed = false, status = "", preview = null, previewRevision = null;
    let summaryCandidate = null, summaryRows = new Map();
    const inputStamp = () => JSON.stringify([el("message").value, el("task-intent").value, el("task-creation-path").value]);
    const available = data => !closed && id(data?.conversation_id) && data?.conversation?.persistent === true
      && Array.isArray(data.conversation.history_candidates) && validMetadata(data.conversation);
    const blocked = current => current.locked || current.mutating || current.snapshot?.busy
      || current.snapshot?.verification?.status === "running" || current.snapshot?.draft_precheck?.status === "running"
      || current.snapshot?.draft_precheck?.reaped === false;
    function clearOriginal() {
      preview = null; previewRevision = null; original.open = false; original.hidden = true;
      for (const name of ["goal", "answer", "artifact", "identity"]) el("history-reference-" + name).textContent = "";
      el("history-reference-artifact-section").hidden = true;
    }
    function clearSummary() {
      summaryCandidate = null;
      el("context-summary-editor").hidden = true; el("context-summary-editor").open = false;
      el("context-summary-text").value = "";
      for (const name of ["origin", "size", "status"]) el("context-summary-" + name).textContent = "";
    }
    function renderSummaries(current) {
      const data = current.snapshot, plan = data.conversation.summary_plan, next = new Map();
      for (const item of plan?.items || []) {
        if (!item.adopted) continue;
        let node = summaryRows.get(item.id);
        if (!node) {
          const row = document.createElement("li"), label = document.createElement("span"), button = document.createElement("button");
          button.type = "button"; button.className = "text-button"; button.textContent = "撤回摘要";
          const details = document.createElement("details"), heading = document.createElement("summary"), origin = document.createElement("p"), body = document.createElement("pre");
          heading.textContent = "来源与摘要"; details.appendChild(heading); details.appendChild(origin); details.appendChild(body);
          row.appendChild(label); row.appendChild(button); row.appendChild(details); node = { row, label, button, origin, body, item };
          button.addEventListener("click", () => {
            if (summaryRows.get(item.id) === node) void withdraw(item.id);
          });
        }
        node.item = item;
        const title = rows.get(item.run_id)?.candidate.title || item.run_id;
        node.label.textContent = title + " · 摘要 v" + item.summary_version + " · " + bytes(item.text) + " B"
          + (item.state === "stale" ? " · 待重新核对" : "");
        const origin = "原运行 " + item.run_id + " · 摘要 v" + item.summary_version
          + " · 原记录 SHA-256 " + item.turn_sha256 + (item.artifact_sha256 ? " · 原成果 SHA-256 " + item.artifact_sha256 : "");
        if (node.origin.textContent !== origin) node.origin.textContent = origin;
        if (node.body.textContent !== item.text) node.body.textContent = item.text;
        node.button.disabled = blocked(current) || Boolean(pending);
        next.set(item.id, node);
      }
      if (summaryRows.size !== next.size || [...summaryRows.keys()].some((key, index) => key !== [...next.keys()][index]))
        el("context-summary-list").replaceChildren(...[...next.values()].map(node => node.row));
      summaryRows = next;
      el("context-summary-active").hidden = !next.size;
      el("context-summary-warning").textContent = plan?.state === "stale" ? "摘要来源已变化，请重核或撤回后发送。" : "";
      el("context-summary-warning").hidden = plan?.state !== "stale";
      const editor = el("context-summary-text"), size = bytes(editor.value);
      editor.disabled = !summaryCandidate || blocked(current) || Boolean(pending);
      el("context-summary-adopt").disabled = editor.disabled || !validSummaryText(editor.value) || (plan?.items.length || 0) >= 16;
      el("context-summary-cancel").disabled = Boolean(pending);
      if (summaryCandidate) el("context-summary-size").textContent = size + " / 2048 B"
        + (summaryCandidate.value.truncated ? " · 候选已截取，请核对原文" : "")
        + ((plan?.items.length || 0) >= 16 ? " · 摘要版本记录已满" : "");
    }
    function render() {
      const current = state(), data = current.snapshot, nextOwner = available(data) ? data.conversation_id : "";
      if (nextOwner !== owner) {
        owner = nextOwner; generation += 1; stamp = ""; rows = new Map(); pending = null; status = "";
        clearOriginal(); el("history-reference-list").replaceChildren(); panel.open = false;
        clearSummary(); summaryRows = new Map(); el("context-summary-list").replaceChildren(); el("context-summary-active").hidden = true;
      }
      panel.hidden = !owner;
      if (!owner) return;
      if (pending?.kind === "read" && pending.revision !== data.revision) {
        generation += 1; pending = null; status = "状态已变化，请重新读取历史原文。"; clearOriginal();
      }
      if (pending?.kind === "summary_candidate" && (pending.revision !== data.revision || pending.input !== inputStamp() || pending.epoch !== current.epoch)) {
        generation += 1; pending = null; status = "内容已变化，请重新提取摘要候选。"; clearSummary();
      }
      if (summaryCandidate && (summaryCandidate.revision !== data.revision || summaryCandidate.input !== inputStamp() || summaryCandidate.epoch !== current.epoch)) clearSummary();
      const value = data.conversation, nextStamp = JSON.stringify([value.history_candidates, value.history_selection]);
      if (nextStamp !== stamp) {
        stamp = nextStamp; selection = [...value.history_selection.run_ids];
        rows = new Map(); el("history-reference-list").replaceChildren();
        for (const candidate of value.history_candidates) {
          const row = document.createElement("li"), label = document.createElement("label"), checkbox = document.createElement("input");
          checkbox.type = "checkbox"; checkbox.checked = selection.includes(candidate.run_id);
          const caption = document.createElement("span"); caption.textContent = kinds[candidate.intent] + " · " + candidate.title;
          label.appendChild(checkbox); label.appendChild(caption); row.appendChild(label);
          const button = document.createElement("button"); button.type = "button"; button.className = "text-button";
          button.textContent = "查看完整原文"; row.appendChild(button); el("history-reference-list").appendChild(row);
          const summarize = document.createElement("button"); summarize.type = "button"; summarize.className = "text-button";
          summarize.textContent = "摘要候选"; row.appendChild(summarize);
          const entry = { candidate, checkbox, button, summarize }; rows.set(candidate.run_id, entry);
          checkbox.addEventListener("change", () => {
            if (rows.get(candidate.run_id) !== entry || blocked(state()) || pending) return;
            selection = [...rows.values()].filter(item => item.checkbox.checked).map(item => item.candidate.run_id);
            status = "勾选尚未生效；点击“只参考勾选历史”确认本次范围。"; updateControls();
          });
          button.addEventListener("click", () => read(candidate.run_id));
          summarize.addEventListener("click", () => propose(candidate.run_id));
        }
      }
      if (preview && previewRevision !== data.revision) clearOriginal();
      const disabled = blocked(current) || Boolean(pending);
      for (const entry of rows.values()) {
        entry.checkbox.disabled = disabled; entry.button.disabled = disabled;
        entry.summarize.disabled = disabled || !value.history_selection.run_ids.includes(entry.candidate.run_id);
        entry.summarize.title = entry.summarize.disabled && !disabled ? "先确认此历史为本次参考" : "提取本地原文，编辑后明确采用";
      }
      el("history-reference-apply").disabled = disabled;
      el("history-reference-all").disabled = disabled || value.history_selection.mode === "all";
      el("history-reference-summary").textContent = value.history_selection.mode === "selected"
        ? "历史参考 · 已选择 " + value.history_selection.run_ids.length + "/" + value.history_candidates.length + " 轮"
        : "历史参考 · 默认全部成功历史";
      el("history-reference-scope").textContent = value.history_selection.mode === "selected"
        ? "本次发送仅参考已确认的 " + value.history_selection.run_ids.length + " 轮成功历史。"
        : "本次发送参考全部 " + value.history_candidates.length + " 轮成功历史。";
      el("history-reference-status").textContent = status || "可勾选本次参考；完整历史、旧成果和当前输入均保留。";
      el("history-reference-empty").hidden = value.history_candidates.length > 0;
      renderSummaries(current);
    }
    const owns = binding => !closed && owner === binding.owner && generation === binding.generation
      && state().snapshot?.conversation_id === binding.owner && (!Object.hasOwn(binding, "epoch") || binding.epoch === state().epoch);
    async function propose(runId) {
      render(); const current = state(), data = current.snapshot, entry = rows.get(runId);
      if (!owner || !entry || blocked(current) || pending || !data.conversation.history_selection.run_ids.includes(runId)) return false;
      const binding = { owner, generation, revision: data.revision, input: inputStamp(), epoch: current.epoch, kind: "summary_candidate" };
      pending = binding; clearSummary(); status = "正在提取本地摘要候选…"; updateControls();
      try {
        const result = await request("/conversation", { action: "summary_candidate", run_id: runId,
          conversation_id: binding.owner, revision: binding.revision });
        if (!owns(binding) || state().snapshot.revision !== binding.revision || inputStamp() !== binding.input) return false;
        if (!validSummaryCandidate(result, entry.candidate) || result.conversation_id !== binding.owner
          || result.revision !== binding.revision) throw Error("invalid_response");
        summaryCandidate = { value: result.candidate, revision: binding.revision, input: binding.input, epoch: binding.epoch };
        el("context-summary-text").value = result.candidate.text;
        const source = result.candidate.binding;
        el("context-summary-origin").textContent = "原运行 " + source.run_id + " · 原版本 " + source.request_revision + " · 原记录 SHA-256 " + source.turn_sha256
          + (source.artifact_sha256 ? " · 原成果 SHA-256 " + source.artifact_sha256 : "")
          + " · 原文 " + result.candidate.original_bytes + " B";
        el("context-summary-editor").hidden = false; el("context-summary-editor").open = true;
        el("context-summary-status").textContent = result.candidate.truncated
          ? "本地原文摘录（最多2KiB），尚未采用，可编辑。" : "本地原文摘录，尚未采用，可编辑。";
        status = "摘要候选已就绪；核对、编辑后可明确采用。"; return true;
      } catch (_) { if (owns(binding)) { clearSummary(); status = "候选暂不可用，请重新核对原文。"; } }
      finally { if (pending === binding) { pending = null; updateControls(); } }
      return false;
    }
    async function summaryAction(kind, payload) {
      render(); const current = state(), data = current.snapshot;
      if (!owner || blocked(current) || pending) return;
      const binding = { owner, generation, revision: data.revision, kind }, input = inputStamp();
      if (kind === "adopt_summary" && (!summaryCandidate || summaryCandidate.revision !== data.revision
        || summaryCandidate.input !== input || !validSummaryText(payload.text) || (data.conversation.summary_plan?.items.length || 0) >= 16)) return;
      pending = binding; status = kind === "adopt_summary" ? "正在保存摘要选择…" : "正在撤回摘要…"; updateControls();
      try {
        const flight = action("/conversation", { action: kind, ...payload });
        binding.epoch = state().epoch;
        const result = await flight;
        if (!owns(binding)) return;
        if (!result || result.conversation_id !== binding.owner || !integer(result.revision)
          || result.revision <= binding.revision || !validMetadata(result.conversation)) throw Error("stale_request");
        const items = result.conversation.summary_plan?.items || [];
        if (kind === "adopt_summary" ? !items.some(item => item.adopted && item.run_id === payload.binding.run_id && item.text === payload.text)
          : items.some(item => item.id === payload.summary_id && item.adopted)) throw Error("invalid_response");
        clearSummary();
        if (inputStamp() === input) status = kind === "adopt_summary" ? "摘要已采用；沿原入口发送。" : "摘要已撤回；原文参考保持。";
      } catch (_) { if (owns(binding)) status = "结果尚未确认，请刷新核对；不会重发或改动当前输入。"; }
      finally { if (pending === binding) { pending = null; updateControls(); } }
    }
    function withdraw(summaryId) {
      const record = state().snapshot?.conversation?.summary_plan?.items.find(item => item.id === summaryId && item.adopted);
      if (record) return summaryAction("withdraw_summary", { summary_id: summaryId });
    }
    async function read(runId, reveal = false) {
      render(); const current = state(), data = current.snapshot, entry = rows.get(runId);
      if (!owner || !entry || blocked(current) || pending) return false;
      const binding = { owner, generation, revision: data.revision, kind: "read" }; pending = binding;
      clearOriginal(); status = "正在读取完整历史原文…"; updateControls();
      try {
        const result = await request("/conversation", { action: "read_history", run_id: runId,
          conversation_id: binding.owner, revision: binding.revision });
        if (!owns(binding) || state().snapshot.revision !== binding.revision) return false;
        if (!validOriginal(result, entry.candidate) || result.conversation_id !== binding.owner
          || result.revision !== binding.revision) throw Error("invalid_response");
        if (result.turn.artifact) {
          const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(result.turn.artifact.text));
          if (!owns(binding) || state().snapshot.revision !== binding.revision) return false;
          if (Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("") !== entry.candidate.artifact_sha256)
            throw Error("invalid_response");
        }
        preview = result.turn; previewRevision = binding.revision;
        el("history-reference-goal").textContent = preview.goal; el("history-reference-answer").textContent = preview.answer;
        el("history-reference-artifact").textContent = preview.artifact?.text || "";
        el("history-reference-artifact-section").hidden = !preview.artifact;
        el("history-reference-identity").textContent = "原运行 " + preview.run_id
          + (preview.artifact ? " · " + preview.artifact.name + " · SHA-256 " + preview.artifact.sha256 : "");
        original.hidden = false; original.open = reveal;
        status = "已读取完整原文；确认历史选择后，编辑要求并沿原发送入口生成独立成果。";
        return true;
      } catch (_) { if (owns(binding)) { clearOriginal(); status = "原文暂不可用或状态已变化，请重新读取；当前输入保留。"; } }
      finally { if (pending === binding) { pending = null; updateControls(); } }
      return false;
    }
    async function choose(all) {
      render(); const current = state(), data = current.snapshot;
      if (!owner || blocked(current) || pending) return;
      const binding = { owner, generation, revision: data.revision, kind: "select" }, chosen = all ? null : [...selection];
      pending = binding; status = "正在保存历史参考选择…"; updateControls();
      try {
        const result = await action("/conversation", { action: "select_history", run_ids: chosen });
        if (!owns(binding)) return;
        if (!result || result.conversation_id !== binding.owner || !integer(result.revision)
          || result.revision <= binding.revision || !validMetadata(result.conversation)
          || result.conversation.history_selection.mode !== (all ? "all" : "selected")
          || chosen && JSON.stringify(result.conversation.history_selection.run_ids) !== JSON.stringify(chosen)) throw Error("stale_request");
        status = all ? "已恢复默认全部成功历史；当前输入保留。" : "历史参考选择已保存；当前输入保留。";
      } catch (_) { if (owns(binding)) status = "选择未能确认，请核对当前范围后重试；当前输入保留。"; }
      finally { if (pending === binding) { pending = null; updateControls(); } }
    }
    el("history-reference-apply").addEventListener("click", () => choose(false));
    el("history-reference-all").addEventListener("click", () => choose(true));
    el("context-summary-adopt").addEventListener("click", () => summaryCandidate && summaryAction("adopt_summary", {
      binding: summaryCandidate.value.binding, text: el("context-summary-text").value }));
    el("context-summary-cancel").addEventListener("click", () => { if (!pending) { clearSummary(); updateControls(); } });
    el("context-summary-text").addEventListener("input", updateControls);
    for (const name of ["message", "task-intent", "task-creation-path"])
      el(name).addEventListener(name === "task-intent" ? "change" : "input", () => { if (summaryCandidate || pending?.kind === "summary_candidate") updateControls(); });
    return { render,
      fields() { const data = state().snapshot; if (!available(data)) return {};
        return { ...(data.conversation.history_selection.mode === "selected" ? { history_run_ids: [...data.conversation.history_selection.run_ids] } : {}),
          ...(data.conversation.summary_plan?.binding ? { context_plan_sha256: data.conversation.summary_plan.binding } : {}) }; },
      ready: () => state().snapshot?.conversation?.summary_plan?.state !== "stale",
      locate(ref) {
        render(); const data = state().snapshot;
        if (!available(data) || ref.kind !== "history" || ref.conversation_id !== owner || ref.revision !== data.revision
          || !rows.has(ref.run_id) || blocked(state()) || pending) return false;
        panel.open = true; panel.scrollIntoView?.({ block: "nearest" }); void read(ref.run_id, true); return true;
      },
      close() { closed = true; generation += 1; pending = null; clearOriginal();
        clearSummary(); summaryRows = new Map(); el("context-summary-list").replaceChildren(); el("context-summary-active").hidden = true;
        el("history-reference-list").replaceChildren(); panel.open = false; panel.hidden = true; },
    };
  }
  window.PactTrailHistoryReferenceUI = { validMetadata, validOriginal, validSummaryBinding, validSummaryRecord, validSummaryPlan, validSummaryCandidate, create,
    handles: (path, body) => path === "/conversation" && ["select_history", "adopt_summary", "withdraw_summary"].includes(body?.action) };
})();
