(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  function create({ state, updateControls }) {
    const dialog = el("draft-review-dialog"), opener = el("draft-review-open"), card = el("draft-review-card");
    const draft = el("task-draft"), checks = el("task-draft-precheck"), apply = el("task-apply-actions");
    let value = null, artifact = null, identity = "", unavailable = true, mounted = false, returning = null;
    let selected = "", view = "diff", fileStamp = "", homes = [], scrolls = new Map(), requested = null;
    const notify = () => { if (typeof updateControls === "function") updateControls(); };
    const rows = () => Array.from(el("task-draft-files").children);
    const scrollKey = () => selected + ":" + view;
    function saveScroll() {
      if (mounted) scrolls.set(scrollKey(), { top: draft.scrollTop, left: draft.scrollLeft });
    }
    function clearConsent() {
      el("task-apply-confirm").checked = false;
      el("task-apply").disabled = true;
    }
    function contextBinding(runId, sourceSha, sha) {
      const data = state().snapshot, task = window.PactTrailDraftUI?.effectiveDraft
        ? window.PactTrailDraftUI.effectiveDraft(data) : data?.task;
      if (!hash(sha) || !hash(sourceSha) || !task
        || task.intent !== "change_draft" || task.run_id !== runId || task.artifact?.sha256 !== sha
        || task.source?.sha256 !== sourceSha || data.project?.sha256 !== sourceSha
        || typeof data.conversation_id !== "string") return "";
      return JSON.stringify([data.conversation_id, task.recovered ? "recovered" : "live", runId, sourceSha, sha]);
    }
    function binding(candidate = value, sha = artifact) {
      if (!candidate || !Array.isArray(candidate.changes) || !candidate.changes.length
        || candidate.changes.length > 4) return "";
      return contextBinding(candidate.run_id, candidate.source_sha256, sha);
    }
    const uncertain = data => ["unknown", "partial", "unavailable"].includes(data?.editing?.state)
      || data?.task?.error === "state_unavailable";
    function restoreRows() {
      for (const row of rows()) {
        row.hidden = false;
        for (const node of Array.from(row.children)) if (node.tagName.toLowerCase() === "h5"
          || ["draft-diff", "draft-new-text"].includes(node.className)) node.hidden = false;
      }
    }
    function close(tell = true) {
      if (!mounted && !dialog.open) return;
      saveScroll(); clearConsent(); mounted = false;
      if (dialog.open) dialog.close();
      for (const home of homes.slice().reverse()) {
        if (home.next?.parentNode === home.parent) home.parent.insertBefore(home.node, home.next);
        else home.parent.appendChild(home.node);
      }
      homes = []; mounted = false; delete draft.dataset.reviewOpen; restoreRows();
      const target = returning && returning.isConnected !== false && !returning.disabled && !returning.hidden
        ? returning : !opener.hidden && !opener.disabled && !card.hidden ? opener : el("message");
      returning = null;
      if (!target.disabled && !target.hidden) target.focus();
      if (tell) notify();
    }
    function paintFiles() {
      if (!value) return;
      const nextStamp = JSON.stringify(value.changes.map(change => [change.file_id, change.path]));
      if (nextStamp !== fileStamp) {
        fileStamp = nextStamp;
        const buttons = value.changes.map(change => {
          const button = document.createElement("button"); button.type = "button";
          button.textContent = change.path; button.dataset.reviewFile = change.file_id;
          button.addEventListener("click", () => {
            if (!mounted || binding() !== identity) return;
            saveScroll(); selected = change.file_id; paintFiles();
          });
          return button;
        });
        el("draft-review-files").replaceChildren(...buttons);
      }
      for (const button of Array.from(el("draft-review-files").children))
        button.setAttribute("aria-pressed", String(button.dataset.reviewFile === selected));
      el("draft-review-diff").setAttribute("aria-pressed", String(view === "diff"));
      el("draft-review-text").setAttribute("aria-pressed", String(view === "text"));
      if (!mounted) return;
      for (const row of rows()) {
        row.hidden = row.dataset.draftFileId !== selected;
        for (const node of Array.from(row.children)) {
          if (node.tagName.toLowerCase() === "h5") node.hidden = true;
          if (node.className === "draft-diff") node.hidden = view !== "diff";
          if (node.className === "draft-new-text") node.hidden = view !== "text";
        }
      }
      const position = scrolls.get(scrollKey());
      draft.scrollTop = position?.top ?? 0; draft.scrollLeft = position?.left ?? 0;
    }
    function controls(blocked = false) {
      unavailable = blocked;
      const current = state(), data = current.snapshot, valid = Boolean(value && binding() === identity);
      const transient = Boolean(current.mutating || data?.busy || data?.draft_precheck?.status === "running"
        || data?.verification?.status === "running");
      const unsafe = current.locked || uncertain(data) && !data?.recovered_draft || unavailable && !transient;
      const sourceTarget = data?.source_target, liveSources = valid && !data?.recovered_draft
        && sourceTarget?.run_id === value?.run_id && /^[a-f0-9]{32}$/.test(sourceTarget?.task_id || "");
      el("draft-review-sources").hidden = !liveSources;
      el("draft-review-sources").disabled = !liveSources || unsafe || unavailable || transient;
      if (requested && (current.locked || uncertain(data) && !data?.recovered_draft
        || contextBinding(requested.runId, requested.sourceSha, requested.sha) !== requested.identity)) requested = null;
      if (mounted && (!valid || unsafe)) close(false);
      if (!valid && value) {
        const pending = requested; clear(); requested = pending; return;
      }
      card.hidden = !valid;
      opener.disabled = !valid || unsafe || unavailable || transient || typeof dialog.showModal !== "function";
      if (valid) {
        const status = uncertain(data) ? "应用结果待核对，请先刷新状态。"
          : data.editing?.state === "applied" ? "已应用" : "等待审阅";
        el("draft-review-summary").textContent = value.changes.length + " 个文件 · " + status;
      }
      if (unsafe || unavailable || transient || uncertain(data)) clearConsent();
      if (requested && valid && identity === requested.identity && !opener.disabled && !mounted) {
        requested = null; open();
      }
    }
    function clear() {
      close(false); clearConsent(); value = null; artifact = null; identity = "";
      selected = ""; view = "diff"; fileStamp = ""; scrolls.clear(); requested = null;
      delete draft.dataset.reviewManaged; card.hidden = true; opener.disabled = true;
      el("draft-review-summary").textContent = ""; el("draft-review-files").replaceChildren();
    }
    function render(next, sha) {
      const key = binding(next, sha);
      if (!key) { clear(); return; }
      if (key !== identity) {
        close(false); clearConsent(); identity = key; selected = next.changes[0].file_id;
        view = "diff"; scrolls.clear(); fileStamp = "";
      } else if (mounted) saveScroll();
      value = next; artifact = sha; draft.dataset.reviewManaged = "true";
      el("draft-review-checks").hidden = !next.changes.some(change => change.path.toLowerCase().endsWith(".py"));
      paintFiles(); controls(unavailable);
    }
    function open() {
      if (opener.disabled || mounted || binding() !== identity) return;
      returning = document.activeElement || opener; clearConsent();
      homes = [draft, checks, apply].map(node => ({ node, parent: node.parentNode, next: node.nextSibling }));
      if (homes.some(home => !home.parent)) { homes = []; returning = null; return; }
      el("draft-review-content").appendChild(draft);
      el("draft-review-checks").appendChild(checks); el("draft-review-apply").appendChild(apply);
      mounted = true; draft.dataset.reviewOpen = "true";
      try { dialog.showModal(); paintFiles(); el("draft-review-close").focus(); }
      catch (_) { close(false); return; }
      notify();
    }
    opener.addEventListener("click", () => { controls(unavailable); open(); });
    for (const mode of ["diff", "text"]) el("draft-review-" + mode).addEventListener("click", () => {
      if (!mounted || binding() !== identity) return;
      saveScroll(); view = mode; paintFiles();
    });
    el("draft-review-close").addEventListener("click", () => close());
    el("draft-review-sources").addEventListener("click", () => {
      if (mounted && !el("draft-review-sources").disabled) close();
    });
    dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
    dialog.addEventListener("keydown", event => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); }
    });
    dialog.addEventListener("close", () => { if (mounted && !dialog.open) close(); });
    return { render, controls, clear, close, requestOpen(runId, sha) {
      const data = state().snapshot, task = window.PactTrailDraftUI?.effectiveDraft
        ? window.PactTrailDraftUI.effectiveDraft(data) : data?.task;
      const sourceSha = task?.source?.sha256, key = contextBinding(runId, sourceSha, sha);
      if (typeof runId !== "string" || !key) return false;
      requested = { runId, sourceSha, sha, identity: key }; controls(unavailable);
      return Boolean(requested || mounted && identity === key);
    } };
  }
  window.PactTrailDraftReviewUI = { create };
})();
