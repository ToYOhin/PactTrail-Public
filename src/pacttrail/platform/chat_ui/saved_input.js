(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const fields = (value, names) => value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
  const bounded = (value, max) => typeof value === "string" && Array.from(value).length <= max
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\ud800-\udfff]/u.test(value);
  const id = value => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const intents = { answer: "普通对话", report: "回答与报告", change_draft: "变更草案", create_file: "新建文件" };
  function valid(value) {
    return Boolean(fields(value, ["id", "message", "task_intent", "creation_path", "source"])
      && id(value.id) && bounded(value.message, 4096) && value.message.trim().length > 0
      && ["answer", "report", "change_draft"].includes(value.task_intent)
      && (value.creation_path === null || value.task_intent === "change_draft"
        && bounded(value.creation_path, 160) && !/[\r\n\t]/.test(value.creation_path))
      && (value.source === null || fields(value.source, ["kind", "name", "sha256"])
        && value.source.kind === "selected_project" && bounded(value.source.name, 80)
        && value.source.name.trim().length > 0 && !/[\r\n\t]/.test(value.source.name)
        && typeof value.source.sha256 === "string" && /^[a-f0-9]{64}$/.test(value.source.sha256)));
  }
  const handles = (path, value) => path === "/conversation"
    && ["save_input", "restore_input", "delete_input"].includes(value?.action);
  function createComposer({ taskDraft }) {
    const input = el("message"), intent = el("task-intent"), path = el("task-creation-path");
    let version = 0, observed = "", closed = false;
    const read = () => ({ message: input.value, ...taskDraft.readTaskDraft() });
    function capture() {
      const value = read(), key = JSON.stringify(value);
      if (key !== observed) { observed = key; version += 1; }
      return { ...value, version };
    }
    const edited = () => { version += 1; observed = JSON.stringify(read()); };
    const blank = (value = capture()) => value.message === "" && value.intent === "answer" && value.creationPath === "";
    for (const [target, event] of [[input, "input"], [intent, "change"], [path, "input"]]) target.addEventListener(event, edited);
    return { capture, blank,
      install(item, data, before) {
        if (closed || !before || capture().version !== before.version || !blank()) return false;
        taskDraft.restoreTaskDraft({ intent: item.creation_path === null ? item.task_intent : "create_file",
          creationPath: item.creation_path ?? "" }, data);
        el("composer-fold").open = true;
        input.value = item.message;
        for (const name of ["task-use-material", "task-use-followup", "task-apply-confirm", "web-search-toggle"])
          el(name).checked = false;
        input.dispatchEvent(new Event("input", { bubbles: true })); input.focus();
        return true;
      },
      close() {
        closed = true; version += 1;
        for (const [target, event] of [[input, "input"], [intent, "change"], [path, "input"]]) target.removeEventListener?.(event, edited);
      },
    };
  }
  function create({ state, action, taskDraft, updateControls, composer }) {
    const input = el("message"), panel = el("saved-input-panel");
    const editor = composer || createComposer({ taskDraft }), { capture, blank } = editor;
    let owner = "", itemKey = "", generation = 0;
    let pending = null, confirmation = null, status = "", closed = false;
    const edited = () => { status = "";
      if (confirmation?.action === "save_input") confirmation = null; };
    input.addEventListener("input", edited);
    el("task-intent").addEventListener("change", edited);
    el("task-creation-path").addEventListener("input", edited);
    const available = data => Boolean(!closed && data?.conversation?.persistent
      && data.conversation_id === data.conversation.selected && id(data.conversation_id)
      && (!Object.hasOwn(data.conversation, "saved_input") || valid(data.conversation.saved_input)));
    const checking = data => data?.verification?.status === "running"
      || data?.draft_precheck?.status === "running" || data?.draft_precheck?.reaped === false;
    function fieldsFor(value) {
      const intent = value.intent === "create_file" ? "change_draft" : value.intent;
      const path = value.intent === "create_file" ? value.creationPath : null;
      return valid({ id: "0".repeat(32), message: value.message, task_intent: intent, creation_path: path, source: null })
        ? { message: value.message, task_intent: intent, creation_path: path } : null;
    }
    function render() {
      const current = state(), data = current.snapshot;
      const active = available(data), nextOwner = active ? data.conversation_id : "";
      if (nextOwner !== owner) {
        owner = nextOwner; generation += 1; itemKey = null; confirmation = null; pending = null; status = "";
        panel.open = false; el("saved-input-preview").open = false;
      }
      panel.hidden = !active;
      const item = active ? data.conversation.saved_input : null, nextKey = item?.id || "";
      if (nextKey !== itemKey) {
        itemKey = nextKey; confirmation = null; status = "";
        el("saved-input-preview").open = false;
        el("saved-input-message").textContent = item?.message || "";
        el("saved-input-meta").textContent = item ? intents[item.creation_path === null ? item.task_intent : "create_file"]
          + (item.creation_path === null ? "" : " · " + item.creation_path) : "";
        el("saved-input-source").textContent = item?.source
          ? "保存时关联「" + item.source.name + "」；恢复后需重新核对来源。"
          : "恢复只填入文字，来源请重新关联或核对。";
      }
      const value = capture();
      const disabled = !active || current.locked || current.mutating || Boolean(pending) || checking(data)
        || ![null, "context_limit"].includes(data?.conversation?.error);
      const writable = Boolean(fieldsFor(value));
      if (confirmation && (confirmation.owner !== owner || confirmation.id !== nextKey
          || confirmation.action === "save_input" && confirmation.version !== value.version)) confirmation = null;
      el("saved-input-summary").textContent = item ? "待发草稿 · 已保存" : "待发草稿";
      el("saved-input-preview").hidden = !item;
      el("saved-input-save").disabled = disabled || !writable;
      el("saved-input-restore").disabled = disabled || !item || data.busy || !blank(value);
      el("saved-input-delete").disabled = disabled || !item;
      el("saved-input-confirmation").hidden = !confirmation;
      el("saved-input-confirm").disabled = disabled || !confirmation;
      el("saved-input-cancel").disabled = disabled;
      el("saved-input-confirm-note").textContent = confirmation?.action === "save_input"
        ? "替换这段会话已保存的草稿？" : "删除这段会话已保存的草稿？";
      el("saved-input-confirm").textContent = confirmation?.action === "save_input" ? "确认替换" : "确认删除";
      el("saved-input-status").textContent = status || (item && !blank(value)
        ? "当前输入会保留；请先保存或清空后再恢复。" : !writable && value.message.length
          ? "完整问题最多 4096 字，新建路径最多 160 字且不能换行。" : "每段会话可显式保存一份；恢复后由你发送。");
    }
    function owns(binding) {
      return !closed && binding.generation === generation && state().snapshot?.conversation_id === binding.owner;
    }
    async function perform(kind, expectedId, value = null) {
      const current = state(), data = current.snapshot;
      if (!available(data) || current.locked || current.mutating || pending || checking(data)
          || (data.conversation.saved_input?.id || "") !== (expectedId || "")) return;
      const before = capture();
      if (kind === "restore_input" && (data.busy || !blank(before))) return;
      const payload = kind === "save_input" ? fieldsFor(value || before) : null;
      if (kind === "save_input" && !payload) return;
      const binding = { owner, generation, version: before.version, kind };
      pending = binding; confirmation = null; status = kind === "save_input" ? "正在保存…"
        : kind === "restore_input" ? "正在恢复…" : "正在删除…"; updateControls();
      try {
        const result = await action("/conversation", { action: kind, saved_input_id: expectedId, ...payload });
        if (!owns(binding)) return;
        if (!result) { status = "尚未确认完成，请刷新状态核对；当前输入已保留。"; return; }
        const item = result.conversation?.saved_input;
        if (kind === "save_input") status = !valid(item) ? "保存结果需刷新核对。"
          : capture().version !== binding.version ? "已保存提交时的草稿；当前新改动尚未保存。" : "已保存。";
        else if (kind === "delete_input") status = item ? "删除结果需刷新核对。" : "已删除。";
        else if (!valid(item) || item.id !== expectedId) status = "恢复结果需刷新核对，当前输入已保留。";
        else if (!editor.install(item, result, before))
          status = "已保留当前新写的内容；已存草稿仍可稍后恢复。";
        else {
          status = "已填入输入框；请重新核对来源后发送。";
        }
      } catch (_) {
        if (owns(binding)) status = "尚未确认完成，请刷新状态核对；当前输入已保留。";
      } finally {
        if (pending === binding) { pending = null; updateControls(); }
      }
    }
    el("saved-input-save").addEventListener("click", () => {
      render(); if (el("saved-input-save").disabled) return;
      const value = capture(), item = state().snapshot.conversation.saved_input;
      if (!item) return perform("save_input", null, value);
      confirmation = { action: "save_input", owner, id: item.id, version: value.version, value }; render();
    });
    el("saved-input-restore").addEventListener("click", () => {
      render(); if (!el("saved-input-restore").disabled)
        return perform("restore_input", state().snapshot.conversation.saved_input.id);
    });
    el("saved-input-delete").addEventListener("click", () => {
      render(); if (el("saved-input-delete").disabled) return;
      confirmation = { action: "delete_input", owner, id: state().snapshot.conversation.saved_input.id }; render();
    });
    el("saved-input-confirm").addEventListener("click", () => {
      render(); const chosen = confirmation;
      if (!el("saved-input-confirm").disabled && chosen) return perform(chosen.action, chosen.id, chosen.value);
    });
    el("saved-input-cancel").addEventListener("click", () => { confirmation = null; render(); });
    return { render, close() { closed = true; generation += 1; pending = null; confirmation = null; panel.open = false;
      if (!composer) editor.close();
      el("saved-input-preview").open = false; el("saved-input-message").textContent = ""; render(); } };
  }
  window.PactTrailSavedInputUI = { valid, handles, createComposer, create };
})();
