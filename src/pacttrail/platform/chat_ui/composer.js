(() => {
  "use strict";
  const el = (id) => document.getElementById(id);
  const count = (value) => Array.from(value).length;

  function create({ state, inputLimit, validText, ready, readTaskDraft, openFiles, focusComposer }) {
    const input = el("message");
    const workbench = createWorkbench({ state, focusComposer });
    let version = 0, observed = "";
    const values = () => ({ text: input.value, ...readTaskDraft() });
    const keyFor = (value) => {
      const data = state().snapshot;
      return JSON.stringify([data?.mode, data?.conversation_id, value]);
    };
    function capture() {
      const value = values(), key = keyFor(value);
      if (key !== observed) { observed = key; version += 1; }
      return { ...value, version };
    }
    function edited() { version += 1; observed = keyFor(values()); }
    input.addEventListener("input", edited);
    el("task-intent").addEventListener("change", edited);
    el("task-creation-path").addEventListener("input", edited);
    el("composer-files").addEventListener("click", () => {
      const { snapshot, locked, historyActive } = state();
      if (snapshot && !locked && !historyActive) openFiles();
    });

    function controls({ unavailable, modelBlocked, historyLimit }) {
      const { snapshot: data, locked, mutating, historyActive } = state();
      const draft = capture(), length = count(draft.text);
      const max = inputLimit(data, draft.intent === "create_file" ? "change_draft" : draft.intent);
      // Page-only preparation has no dispatch authority. A late acknowledgement
      // may clear only the exact version that was submitted.
      input.disabled = Boolean(locked || !data || historyActive);
      const checking = data?.verification?.status === "running"
        || data?.draft_precheck?.status === "running" || data?.draft_precheck?.reaped === false;
      const liveBlocked = data?.provider_id && data.provider_id !== "offline" && !data.connection?.can_send;
      el("send").disabled = Boolean(unavailable || locked || !data || mutating || historyActive || checking
        || modelBlocked || liveBlocked || data?.busy || historyLimit || length === 0 || length > max || !ready()
        || (data?.answer_mode === "ordinary" && !validText(draft.text, max))
        || (data && data.provider_id !== "offline" && data.connection && data.answer_mode !== "ordinary"
          && draft.text !== data.prompts[data.connection.calls]));
      el("char-count").textContent = length + " / " + max;
      const bar = el("composer-taskbar"), files = el("composer-files");
      bar.hidden = !data || data.answer_mode !== "ordinary";
      const selected = data?.project?.files || (data?.material ? [{ path: data.material.name }] : []);
      files.textContent = selected.length ? "已关联 " + selected.length + " 文件" : "关联文件";
      files.title = selected.length ? selected.map(file => file.path).join("\n") : "选择本次使用的文件";
      files.disabled = Boolean(locked || !data || historyActive);
      const explanation = el("task-intent-explanation");
      if (explanation) explanation.hidden = !data || !["agent_demo", "agent_model"].includes(data.mode);
    }

    return { capture, controls, renderWorkbench: workbench.render,
      clearIfUnchanged(submitted) {
        const current = capture();
        if (submitted && current.version === submitted.version && current.text === submitted.text
            && current.intent === submitted.intent && current.creationPath === submitted.creationPath) {
          input.value = ""; capture();
        }
      },
      close() { observed = ""; version += 1; workbench.close(); },
    };
  }

  function createWorkbench({ state, focusComposer }) {
    const input = el("message"), dialog = el("command-palette"), query = el("command-query");
    let previousFocus = null, active = 0, visible = [], generation = 0, binding = null, painted = "";
    const identity = () => JSON.stringify([state().snapshot?.conversation_id, state().snapshot?.revision]);
    const usable = id => { const item = el(id); return item && !item.disabled && !item.hidden; };
    const focusInput = () => {
      if (input.disabled) return;
      focusComposer?.();
      el("composer-fold").open = true; input.focus(); resize();
    };
    function resize() {
      if (!input.style || !el("composer-fold").open) return;
      input.style.height = "auto";
      input.style.height = Math.min(200, Math.max(64, input.scrollHeight)) + "px";
    }
    function prepare(value) {
      const current = state(), option = el("task-intent-" + (value === "change_draft" ? "draft" : value));
      if (!current.snapshot || current.locked || current.historyActive || input.disabled || !option || option.disabled || option.hidden) return;
      const select = el("task-intent");
      if (select.disabled) return;
      select.value = value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      focusInput();
    }
    const commands = [
      { name: "输入消息", search: "提问 chat message", key: "", ready: () => !input.disabled, run: focusInput },
      { name: "文件与改动", search: "材料 files changes", key: "Ctrl J", ready: () => usable("composer-files"), run: () => el("composer-files").click() },
      { name: "切换侧边栏", search: "会话 sidebar", key: "Ctrl B", ready: () => true, run: () => el("sidebar-toggle").click() },
      { name: "生成报告", search: "report", key: "", ready: () => usable("quick-report"), run: () => prepare("report") },
      { name: "修改代码", search: "草案 draft edit", key: "", ready: () => usable("quick-draft"), run: () => prepare("change_draft") },
      { name: "历史参考", search: "history context", key: "", ready: () => !el("history-reference-panel").hidden,
        run: () => { focusInput(); el("history-reference-panel").open = true; el("history-reference-summary").focus(); } },
      { name: "模型设置", search: "model connection", key: "", ready: () => usable("model-setup-open"), run: () => el("model-setup-open").click() },
    ];
    function close(restore = true) {
      if (!dialog?.open) return;
      generation += 1; binding = null;
      dialog.close();
      if (restore && previousFocus?.isConnected !== false && !previousFocus?.disabled) previousFocus?.focus();
      previousFocus = null;
    }
    function choose(command, stamp = generation) {
      if (!dialog.open || stamp !== generation || binding !== identity() || !command || !command.ready()
          || state().locked || state().historyActive) return;
      close(false); command.run();
    }
    function paint() {
      const words = query.value.trim().toLocaleLowerCase();
      visible = commands.filter(command => command.ready() && (command.name + " " + command.search).toLocaleLowerCase().includes(words));
      active = Math.min(active, Math.max(0, visible.length - 1));
      const stamp = ++generation;
      painted = JSON.stringify([words, active, visible.map(command => command.name)]);
      const buttons = visible.map((command, index) => {
        const button = document.createElement("button"); button.type = "button";
        button.dataset.active = String(index === active); button.setAttribute("aria-current", String(index === active));
        const name = document.createElement("span"); name.className = "command-name"; name.textContent = command.name;
        const key = document.createElement("kbd"); key.className = "command-key"; key.textContent = command.key;
        button.appendChild(name); button.appendChild(key);
        button.addEventListener("click", () => choose(command, stamp)); return button;
      });
      el("command-list").replaceChildren(...buttons); el("command-empty").hidden = visible.length > 0;
    }
    function open() {
      if (!dialog || dialog.open || el("draft-review-dialog")?.open || state().locked || state().historyActive) return;
      previousFocus = document.activeElement; query.value = ""; active = 0; binding = identity(); paint(); dialog.showModal(); query.focus();
    }
    el("command-open")?.addEventListener("click", open);
    el("command-close")?.addEventListener("click", () => close());
    query?.addEventListener("input", () => { active = 0; paint(); });
    dialog?.addEventListener("cancel", event => { event.preventDefault(); close(); });
    dialog?.addEventListener("keydown", event => {
      if (event.isComposing) return;
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); }
      if (event.target !== query) return;
      if (["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault(); active = visible.length ? (active + (event.key === "ArrowDown" ? 1 : -1) + visible.length) % visible.length : 0; paint();
      } else if (event.key === "Enter") { event.preventDefault(); choose(visible[active]); }
    });
    window.addEventListener("keydown", event => {
      if (event.defaultPrevented || event.isComposing || event.altKey || !(event.ctrlKey || event.metaKey)
          || el("draft-review-dialog")?.open) return;
      const key = event.key.toLowerCase();
      if (key === "k") { event.preventDefault(); dialog?.open ? close() : open(); }
      else if (!dialog?.open && !state().historyActive && ["b", "j"].includes(key)) {
        event.preventDefault(); el(key === "b" ? "sidebar-toggle" : "sources-toggle").click();
      }
    });
    for (const [name, value] of [["answer", "answer"], ["report", "report"], ["draft", "change_draft"]]) {
      el("quick-" + name)?.addEventListener("click", () => prepare(value));
    }
    input.addEventListener("input", resize);
    el("composer-fold").addEventListener("toggle", resize);
    window.addEventListener("resize", resize);
    return { close: () => close(false), render() {
      const current = state(), data = current.snapshot;
      const supported = data && ["agent_demo", "agent_model"].includes(data.mode);
      const blocked = current.locked || current.historyActive || !data;
      for (const name of ["answer", "report", "draft"]) {
        const button = el("quick-" + name), option = el("task-intent-" + name);
        if (button) { button.hidden = !supported; button.disabled = Boolean(blocked || input.disabled || el("task-intent").disabled || !option || option.disabled || option.hidden); }
      }
      if (el("command-open")) el("command-open").disabled = Boolean(blocked);
      if (blocked || dialog?.open && binding !== identity()) close(false);
      else if (dialog?.open) {
        const key = JSON.stringify([query.value.trim().toLocaleLowerCase(), active,
          commands.filter(command => command.ready() && (command.name + " " + command.search).toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())).map(command => command.name)]);
        if (key !== painted) paint();
      }
      resize();
    } };
  }
  function createInputs(options) {
    const composer = window.PactTrailSavedInputUI?.createComposer(options);
    const saved = window.PactTrailSavedInputUI?.create({ ...options, composer });
    const continuation = window.PactTrailContinuationUI?.create({ ...options, composer });
    const artifact = window.PactTrailArtifactReferenceUI?.create(options);
    const versions = window.PactTrailArtifactVersionsUI?.create(options);
    const sources = window.PactTrailSourceCitationsUI?.create(options);
    const history = window.PactTrailHistoryReferenceUI?.create(options);
    const fields = () => ({ ...artifact?.fields(), ...history?.fields() });
    return { render() { saved?.render(); continuation?.render(); artifact?.render(); versions?.render(); sources?.render(); history?.render(); },
      openContinuation: binding => continuation?.open(binding),
      openArtifact: binding => artifact?.open(binding),
      openVersions: binding => versions?.open(binding),
      openSources: binding => sources?.open(binding),
      locateArtifact: ref => artifact?.locate(ref),
      locateHistory: (ref, fallback) => history?.locate(ref) || fallback?.(ref),
      createPreview({ searchFields, locateHistory, locateSource }) {
        return window.PactTrailSendPreviewUI?.create({ state: options.state, request: options.request,
          locate: ref => ref.kind === "artifact" ? artifact?.locate(ref)
            : ref.kind === "history" ? history?.locate(ref) || locateHistory?.(ref) : locateSource?.(ref),
          fields: () => { const data = options.state().snapshot; return { message: el("message").value,
            ...options.taskDraft.fields(), ...searchFields(), ...fields(),
            ...(data?.project ? { project_sha256: data.project.sha256 } : {}) }; },
        }) || { render() {}, close() {} };
      },
      ready: () => artifact?.ready() !== false && history?.ready() !== false, fields,
      close() { saved?.close(); continuation?.close(); artifact?.close(); versions?.close(); sources?.close(); history?.close(); composer?.close(); } };
  }
  function createRepair({ state, ready, prepareChange, updateControls }) {
    return { prepareRepair(goal) {
      const current = state();
      if (el("message").value || current.locked || current.mutating || current.snapshot?.busy
          || !ready() || !prepareChange()) return false;
      el("composer-fold").open = true; el("composer-options").open = true;
      el("message").value = goal; el("message").focus(); updateControls(); return true;
    }, clearRepair() { el("message").value = ""; el("message").focus(); updateControls(); return true; } };
  }
  window.PactTrailComposerUI = { create, createInputs, createRepair };
})();
