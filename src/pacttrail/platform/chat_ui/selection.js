(() => {
  "use strict";
  const el = (id) => document.getElementById(id);
  const text = (id, value) => { el(id).textContent = value; };
  const show = (id, visible) => { el(id).hidden = !visible; };
  const count = (value) => Array.from(value).length;
  const integer = (value, low, high) => Number.isInteger(value) && value >= low && value <= high;
  const taskMode = (data) => data && ["agent_demo", "agent_model"].includes(data.mode);
  function node(tag, className, value) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (value !== undefined) result.textContent = value;
    return result;
  }
  function materialLines(value) { return value.replace(/^\ufeff/u, "").replace(/\r\n|\r/g, "\n").split("\n"); }

  function setPanel(name, open, moveFocus = false) {
    const panel = el(name), button = el(name === "sidebar" ? "sidebar-toggle" : "sources-toggle");
    el("workspace").dataset[name === "sidebar" ? "sidebarOpen" : "inspectorOpen"] = String(open);
    panel.inert = !open; button.setAttribute("aria-expanded", String(open));
    if (moveFocus) {
      if (open) el(name === "sidebar" ? "sidebar-close" : "sources-close").focus();
      else button.focus();
    } else if (!open && panel.contains(document.activeElement)) button.focus();
  }
  function selectTab(name, focus = false) {
    for (const tab of ["materials", "session"]) {
      const selected = tab === name;
      el("tab-" + tab).setAttribute("aria-selected", String(selected));
      el(tab + "-panel").hidden = !selected;
      el("tab-" + tab).tabIndex = selected ? 0 : -1;
    }
    if (focus) el("tab-" + name).focus();
  }

  function validMaterialName(value) {
    return typeof value === "string" && count(value) <= 80 && value.replace(/[\s\u0085]/gu, "").length > 0
      && /\.txt$/i.test(value) && !/[\\/:\u0000-\u001f\u007f\ud800-\udfff]/u.test(value);
  }

  function validMaterialText(value) {
    return typeof value === "string" && value.length <= 8192 && value.replace(/[\s\u0085]/gu, "").length > 0
      && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\ud800-\udfff]/u.test(value);
  }

  function validMaterial(material) {
    return material && Object.keys(material).length === 5
      && validMaterialName(material.name) && validMaterialText(material.text)
      && typeof material.sha256 === "string" && /^[a-f0-9]{64}$/.test(material.sha256) && integer(material.byte_count, 1, 8192)
      && integer(material.line_count, 1, 256)
      && new TextEncoder().encode(material.text).byteLength === material.byte_count
      && materialLines(material.text).length === material.line_count;
  }

  async function readText(file, isCurrent, includeHash = false) {
    if (!integer(file.size, 1, 8192)) throw new Error(file.size > 8192 ? "material_limit" : "material_invalid");
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (!isCurrent()) return null;
    if (bytes.byteLength !== file.size) throw new Error("material_invalid");
    const content = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
    if (!validMaterialText(content)) throw new Error("material_invalid");
    const lines = materialLines(content);
    if (lines.length > 256) throw new Error("material_limit");
    const result = { text: content, byte_count: bytes.byteLength, line_count: lines.length,
      data: btoa(Array.from(bytes, (value) => String.fromCharCode(value)).join("")) };
    if (includeHash) {
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      if (!isCurrent()) return null;
      result.sha256 = Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
    }
    return result;
  }

  function materialRows(lines, first, last, query, selectedLine) {
    return lines.slice(first, last).map((line, index) => {
      const current = first + index === selectedLine;
      const row = node("div", "material-line" + (current ? " material-line-current" : ""));
      row.dataset.materialLine = String(first + index + 1);
      if (current) row.setAttribute("aria-current", "true");
      row.appendChild(node("span", "material-line-number", String(first + index + 1)));
      const original = node("span", "material-line-text");
      let cursor = 0;
      let found = query ? line.indexOf(query) : -1;
      while (found >= 0) {
        if (found > cursor) original.appendChild(document.createTextNode(line.slice(cursor, found)));
        original.appendChild(node("mark", "material-match", line.slice(found, found + query.length)));
        cursor = found + query.length;
        found = line.indexOf(query, cursor);
      }
      if (cursor < line.length || !line.length) original.appendChild(document.createTextNode(line.slice(cursor)));
      row.appendChild(original);
      return row;
    });
  }

  function createLocator({ state, updateControls, request, action, inputsBlocked }) {
    let identity = "", serial = 0, version = 0, controller = null, pending = false;
    let result = null, selected = new Set(), feedback = "", controlsUnavailable = false;
    let candidateRows = new Map(), renderedCandidates = null;
    const key = () => {
      const data = state().snapshot;
      return JSON.stringify([data?.conversation_id, data?.revision, data?.editing?.target_root,
        data?.project?.sha256, data?.material?.sha256]);
    };
    const available = () => {
      const { snapshot: data, locked, mutating } = state();
      return Boolean(data && taskMode(data) && data.editing?.target_root && !locked && !mutating && !data.busy);
    };
    const queryValid = () => {
      const value = el("project-locate-query").value;
      return count(value) >= 1 && count(value) <= 128 && value.trim().length > 0
        && !/[\u0000-\u001f\u007f-\u009f\ud800-\udfff]/u.test(value);
    };
    const chosen = () => result ? result.files.filter(entry => selected.has(entry.path)) : [];
    const validSelection = () => chosen().length > 0 && chosen().length <= 4
      && chosen().reduce((sum, entry) => sum + entry.byte_count, 0) <= 8192;
    function invalidate(clearQuery = false) {
      serial += 1;
      if (controller) controller.abort();
      controller = null; pending = false; result = null; selected = new Set(); feedback = "";
      candidateRows.clear(); renderedCandidates = null;
      if (clearQuery) { el("project-locate-query").value = ""; version += 1; }
    }
    function sync() {
      const next = key();
      if (identity !== next || !available() || inputsBlocked()) { invalidate(); identity = next; }
    }
    const bound = () => ({ conversation_id: state().snapshot.conversation_id, revision: state().snapshot.revision });
    function validResult(data, expected, selectionPaths = null) {
      const fields = ["kind", "version", "conversation_id", "revision", "target_root", "search_id", "query", "mode",
        "files", "scanned_entries", "read_bytes", "truncated", "skipped", "selection"];
      return data && Object.keys(data).length === fields.length && fields.every(name => Object.hasOwn(data, name))
        && data.kind === "project_location" && data.version === 1
        && data.conversation_id === expected.conversation_id && data.revision === expected.revision
        && data.target_root === expected.target_root && data.query === expected.query && data.mode === expected.mode
        && typeof data.search_id === "string" && /^[a-f0-9]{32}$/.test(data.search_id)
        && (!expected.search_id || data.search_id === expected.search_id)
        && ["scanned_entries", "read_bytes", "skipped"].every(name => integer(data[name], 0, 2147483647))
        && typeof data.truncated === "boolean" && Array.isArray(data.files) && data.files.length <= 20
        && data.files.every(entry => entry && Object.keys(entry).length === (Object.hasOwn(entry, "line") ? 3 : 2)
          && window.PactTrailTaskUI.validProjectPath(entry.path) && integer(entry.byte_count, 0, Number.MAX_SAFE_INTEGER)
          && (!Object.hasOwn(entry, "line") || integer(entry.line, 1, 2147483647)))
        && new Set(data.files.map(entry => window.PactTrailTaskUI.foldPath(entry.path))).size === data.files.length
        && (selectionPaths === null ? data.selection === null : window.PactTrailTaskUI.validProject(data.selection, true)
          && data.selection.version === 1 && data.selection.files.length === selectionPaths.length
          && data.selection.files.every(entry => selectionPaths.includes(entry.path)));
    }
    function renderCandidates() {
      const searchId = result?.search_id, nextRows = new Map();
      for (const entry of result?.files || []) {
        const candidateKey = JSON.stringify([searchId, entry.path]);
        let candidate = candidateRows.get(candidateKey);
        if (!candidate) {
          const row = node("label", "project-file-row"), checkbox = node("input");
          checkbox.type = "checkbox"; checkbox.dataset.locatePath = entry.path;
          checkbox.addEventListener("change", () => {
            sync();
            if (checkbox.disabled || result?.search_id !== searchId || !available()
              || candidateRows.get(candidateKey)?.checkbox !== checkbox) return;
            if (checkbox.checked) selected.add(entry.path); else selected.delete(entry.path);
            version += 1; serial += 1; result.selection = null;
            feedback = chosen().length > 4 || chosen().reduce((sum, item) => sum + item.byte_count, 0) > 8192
              ? "最多选 4 份，合计不超过 8192 字节。" : "";
            render(); updateControls();
          });
          row.appendChild(checkbox); row.appendChild(node("span", "", entry.path + (entry.line ? ":" + entry.line : "")
            + " · " + entry.byte_count + " 字节" + (entry.byte_count < 1 || entry.byte_count > 8192 ? " · 超出关联范围" : "")));
          candidate = { row, checkbox };
        }
        const { checkbox } = candidate;
        checkbox.checked = selected.has(entry.path);
        checkbox.disabled = controlsUnavailable || pending || !available() || inputsBlocked()
          || entry.byte_count < 1 || entry.byte_count > 8192;
        nextRows.set(candidateKey, candidate);
      }
      const nextRendered = JSON.stringify([...nextRows.keys()]);
      candidateRows = nextRows;
      if (renderedCandidates !== nextRendered) {
        el("project-locate-results").replaceChildren(...Array.from(candidateRows.values(), candidate => candidate.row));
        renderedCandidates = nextRendered;
      }
    }
    function render() {
      sync();
      renderCandidates();
      const data = state().snapshot, present = Boolean(taskMode(data) && data.editing?.target_root);
      show("project-locator", present);
      if (!present) return;
      text("project-locate-root", data.editing.target_root);
      text("project-locate-feedback", feedback);
      show("project-locate-feedback", Boolean(feedback));
      const files = chosen(), bytes = files.reduce((sum, entry) => sum + entry.byte_count, 0);
      text("project-locate-summary", pending ? "正在定位…" : result
        ? "扫描 " + result.scanned_entries + " 项 · 读取 " + result.read_bytes + " 字节 · "
          + (result.truncated ? "已到定位上限" : "本次范围已查完") + " · 跳过 " + result.skipped + " 项\n"
          + "入选 " + files.length + " / 4 份 · " + bytes + " / 8192 字节"
        : "只在此目录定位。先选结果，再预览并确认关联。");
      const preview = result?.selection;
      text("project-locate-preview-text", preview ? preview.files.map(entry => entry.path + " · " + entry.byte_count
        + " 字节 · " + entry.line_count + " 行\n" + entry.text).join("\n\n") : "");
      show("project-locate-preview-text", Boolean(preview)); show("project-locate-attach", Boolean(preview));
      show("project-locate-cancel", pending || Boolean(result));
    }
    async function locate(kind) {
      sync();
      if (controlsUnavailable || !available() || inputsBlocked() || pending || !queryValid() || kind === "preview" && !validSelection()) return;
      const data = state().snapshot, current = ++serial, inputVersion = version, currentIdentity = identity;
      const expected = { ...bound(), target_root: data.editing.target_root, query: el("project-locate-query").value,
        mode: el("project-locate-mode").value, ...(kind === "preview" ? { search_id: result.search_id } : {}) };
      const paths = kind === "preview" ? chosen().map(entry => entry.path) : null;
      const body = kind === "preview" ? { ...bound(), action: kind, search_id: expected.search_id, paths }
        : { ...bound(), action: kind, query: expected.query, mode: expected.mode };
      if (controller) controller.abort(); controller = new AbortController();
      const signal = controller.signal;
      pending = true; feedback = ""; if (kind === "search") { result = null; selected = new Set(); }
      else result.selection = null;
      render(); updateControls();
      const isCurrent = () => { sync(); return current === serial && inputVersion === version && identity === currentIdentity
        && available() && !signal.aborted && !inputsBlocked(); };
      try {
        const response = await request("/project-locate", body, signal);
        if (!isCurrent()) return;
        if (!validResult(response, expected, paths)) throw new Error("project_location_invalid");
        result = response;
        if (!result.files.length) feedback = result.truncated ? "本次上限内没有匹配；请缩小路径或换一个关键词。" : "没有找到匹配文件。";
      } catch (error) {
        if (!isCurrent()) return;
        feedback = ({ project_location_invalid: "定位结果未通过校验，请重新定位。",
          project_location_unavailable: "文件暂不可读，未关联任何内容。",
          project_location_limit: "选定内容超过上限，请减少文件后重试。",
          project_location_stale: "目录或文件已变化，请重新定位。" })[error.message] || "本次定位未完成，请重新定位。";
        if (kind === "preview" && result) result.selection = null;
      } finally {
        if (isCurrent()) { pending = false; controller = null; render(); updateControls(); }
      }
    }
    el("project-locate-query").addEventListener("input", () => { version += 1; invalidate(); render(); updateControls(); });
    el("project-locate-mode").addEventListener("change", () => { version += 1; invalidate(); render(); updateControls(); });
    el("project-locate-search").addEventListener("click", () => locate("search"));
    el("project-locate-preview").addEventListener("click", () => locate("preview"));
    el("project-locate-cancel").addEventListener("click", async () => {
      sync(); const searchId = result?.search_id, body = searchId && available() ? { ...bound(), action: "cancel", search_id: searchId } : null;
      invalidate(); render(); updateControls();
      if (body) { try { await request("/project-locate", body); } catch (_) { /* Local cancellation already isolated the result. */ } }
    });
    el("project-locate-attach").addEventListener("click", () => {
      sync();
      if (controlsUnavailable || !available() || inputsBlocked() || pending || !result?.selection || el("project-locate-attach").disabled) return;
      const fields = { action: "attach", search_id: result.search_id, project_sha256: result.selection.sha256, confirmed: true };
      invalidate(); render(); updateControls(); return action("/project-locate", fields);
    });
    return { render, clear() { invalidate(true); }, hasDraft() { sync(); return pending || Boolean(result); },
      controls(unavailable) {
        controlsUnavailable = unavailable;
        if (unavailable) invalidate();
        sync(); render();
        const blocked = unavailable || !available() || inputsBlocked();
        el("project-locate-query").disabled = blocked;
        el("project-locate-mode").disabled = blocked;
        el("project-locate-search").disabled = blocked || pending || !queryValid();
        el("project-locate-preview").disabled = blocked || pending || !validSelection();
        el("project-locate-attach").disabled = blocked || pending || !result?.selection;
        el("project-locate-cancel").disabled = !pending && !result;
      } };
  }

  function createProject({ state, updateControls, action, request, materialBlocked }) {
    let snapshot = null;
    let locked = true;
    let mutating = false;
    let projectCandidates = null;
    let projectDraft = null;
    let projectReading = false;
    let projectRead = 0;
    let renderedProject = "";
    const locator = request && el("project-locator") ? createLocator({ state, updateControls, action, request,
      inputsBlocked: () => Boolean(projectCandidates) || projectReading || materialBlocked()
        || Boolean(state().snapshot?.material || state().snapshot?.project) }) : null;
    const sync = () => { ({ snapshot, locked, mutating } = state()); };
  function projectFeedback(value) {
    text("project-feedback", value);
    show("project-feedback", Boolean(value));
  }

  function clearProjectSelection() {
    locator?.clear();
    projectRead += 1;
    projectCandidates = null;
    projectDraft = null;
    projectReading = false;
    renderedProject = "";
    el("project-files").value = "";
    projectFeedback("");
  }

  function selectedProjectFiles() {
    return projectCandidates ? projectCandidates.files.filter((entry) => entry.selected) : [];
  }

  function validProjectSelection() {
    const selected = selectedProjectFiles();
    return selected.length > 0 && selected.length <= 4
      && selected.reduce((total, entry) => total + entry.file.size, 0) <= 8192;
  }

  function renderProject() {
    sync();
    locator?.render();
    const active = taskMode(snapshot);
    show("project-panel", Boolean(active));
    if (!active) return;
    const attached = snapshot.project;
    const selected = selectedProjectFiles();
    const entries = attached ? attached.files : projectCandidates ? projectCandidates.files : [];
    const key = JSON.stringify([attached ? attached.sha256 : projectCandidates && projectCandidates.name,
      Boolean(attached), entries.map((entry) => [entry.path, attached ? entry.byte_count : entry.file.size])]);
    if (key !== renderedProject) {
      renderedProject = key;
      el("project-file-list").replaceChildren(...entries.map((entry) => {
        const row = node(attached ? "details" : "label", "project-file-row");
        if (!attached) {
          const checkbox = node("input"); checkbox.type = "checkbox";
          checkbox.dataset.projectPath = entry.path; checkbox.checked = entry.selected;
          checkbox.addEventListener("change", () => {
            if (checkbox.disabled || !projectCandidates) return;
            entry.selected = checkbox.checked;
            projectRead += 1; projectDraft = null;
            const chosen = selectedProjectFiles();
            projectFeedback(chosen.length > 4 || chosen.reduce((sum, item) => sum + item.file.size, 0) > 8192
              ? "最多选择 4 份文件，合计不超过 8192 字节；请减少选择。" : "");
            renderProject(); updateControls();
          });
          row.appendChild(checkbox);
        }
        row.appendChild(node(attached ? "summary" : "span", "", entry.path + " · " + (attached ? entry.byte_count : entry.file.size) + " 字节"
          + (attached ? " · " + entry.line_count + " 行 · SHA-256 " + entry.sha256 : "")
          + (entry.web ? "\n" + window.PactTrailTaskUI.webProvenance(entry.web) : "")));
        if (attached) {
          const original = node("pre", "project-source-original", entry.text);
          original.tabIndex = -1; original.setAttribute("aria-label", entry.path + " 已确认快照原文");
          row.appendChild(original);
        }
        return row;
      }));
    }
    text("project-scope", attached
      ? "已关联「" + attached.name + "」的 " + attached.files.length + " 份文件快照，合计 " + attached.byte_count
        + " 字节 · 清单版本 " + attached.version + " · SHA-256 " + attached.sha256
        + (snapshot.conversation ? "。后续提问沿用这些文件；" : "。每次发送需勾选本次使用；")
        + (attached.version === 2 ? "网页来源由获取组件提供，正文仅为页面声明；不会自动重新获取。" : "这份清单不代表完整目录。")
      : projectCandidates ? "「" + projectCandidates.name + "」可选文件 " + projectCandidates.files.length
        + " 份；已选 " + selected.length + " 份 / " + selected.reduce((sum, entry) => sum + entry.file.size, 0)
        + " 字节。已排除或未列出 " + projectCandidates.omitted + " 份；清单仅为候选范围，不代表完整目录。"
      : "选择目录只显示安全文件名与大小，不读取正文。支持 .txt / .md / .py / .json，最多列出 64 份候选；需主动勾选 1–4 份，总计最多 8192 字节。隐藏目录与依赖目录不参与。" );
    const preview = attached || projectDraft;
    text("project-preview-text", preview ? preview.files.map((entry) =>
      entry.path + " · " + entry.byte_count + " 字节 · " + entry.line_count + " 行 · SHA-256 " + entry.sha256
      + "\n" + entry.text).join("\n\n") : "");
    show("project-preview-text", Boolean(preview));
    show("project-attach", Boolean(projectDraft));
    show("project-remove", Boolean(attached));
    show("project-cancel", Boolean(projectCandidates) || projectReading);
  }

  function chooseProject() {
    sync();
    if (locked || mutating || !taskMode(snapshot) || snapshot.busy || snapshot.material || snapshot.project
        || materialBlocked() || projectReading || locator?.hasDraft()) return;
    const files = Array.from(el("project-files").files || []);
    clearProjectSelection();
    if (!files.length) { renderProject(); updateControls(); return; }
    const roots = new Set(); const candidates = []; let omitted = 0;
    for (const file of files) {
      const relative = file.webkitRelativePath;
      if (typeof relative !== "string" || !relative.includes("/")) { omitted += 1; continue; }
      const split = relative.indexOf("/"); const root = relative.slice(0, split); const path = relative.slice(split + 1);
      if (!window.PactTrailTaskUI.validProjectName(root)) { omitted += 1; continue; }
      roots.add(root);
      if (!window.PactTrailTaskUI.validProjectPath(path) || !integer(file.size, 1, 8192)) { omitted += 1; continue; }
      candidates.push({ path, file, selected: false });
    }
    const paths = candidates.map(entry => window.PactTrailTaskUI.foldPath(entry.path));
    if (roots.size !== 1 || new Set(paths).size !== paths.length) {
      projectFeedback("目录范围或文件名有冲突，请重新选择一个目录；大小写重复路径不能关联。");
    } else {
      candidates.sort((left, right) => window.PactTrailTaskUI.comparePaths(left.path, right.path));
      projectCandidates = { conversation_id: snapshot.conversation_id, revision: snapshot.revision,
        name: [...roots][0], files: candidates.slice(0, 64), omitted: omitted + Math.max(0, candidates.length - 64) };
      if (!candidates.length) projectFeedback("没有可选文件：仅支持安全相对路径及 .txt/.md/.py/.json，每份不超过 8192 字节。");
    }
    renderProject(); updateControls();
  }

  async function previewProject() {
    if (el("project-preview").disabled || !projectCandidates || !validProjectSelection()) return;
    const scope = projectCandidates; const selected = selectedProjectFiles(); const current = ++projectRead;
    projectDraft = null; projectReading = true; projectFeedback(""); renderProject(); updateControls();
    const isCurrent = () => { sync(); return current === projectRead && !locked && !mutating && snapshot && !snapshot.busy
      && !snapshot.material && !snapshot.project && scope.conversation_id === snapshot.conversation_id
      && scope.revision === snapshot.revision && projectCandidates === scope; };
    try {
      const reviewed = [];
      for (const entry of selected) {
        if (!isCurrent()) return;
        const content = await readText(entry.file, isCurrent, true);
        if (!content) return;
        reviewed.push({ path: entry.path, ...content });
      }
      if (isCurrent()) projectDraft = { conversation_id: scope.conversation_id, revision: scope.revision,
        name: scope.name, files: reviewed };
    } catch (error) {
      if (isCurrent()) projectFeedback(error.message === "material_limit"
        ? "选定文件超过限制：每份最多 8192 字节、256 行，合计最多 8192 字节。"
        : "选定文件未通过校验：需要非空 UTF-8 文本，不能含控制字符；未关联任何文件。");
    } finally {
      if (current === projectRead) { projectReading = false; renderProject(); updateControls(); }
    }
  }

  el("project-files").addEventListener("change", chooseProject);
  el("project-preview").addEventListener("click", previewProject);
  el("project-cancel").addEventListener("click", () => { clearProjectSelection(); renderProject(); updateControls(); });
  el("project-attach").addEventListener("click", () => {
    sync();
    if (el("project-attach").disabled || !projectDraft || !snapshot) return;
    if (projectDraft.conversation_id !== snapshot.conversation_id || projectDraft.revision !== snapshot.revision) {
      clearProjectSelection(); renderProject(); updateControls(); return;
    }
    return action("/project", { action: "attach", name: projectDraft.name,
      files: projectDraft.files.map((entry) => ({ path: entry.path, data: entry.data })) });
  });
  el("project-remove").addEventListener("click", () => {
    sync();
    if (!snapshot || !snapshot.project || el("project-remove").disabled) return;
    return action("/project", { action: "remove" });
  });
    function locate(reference, reveal) {
      sync();
      const project = snapshot?.project;
      if (locked || mutating || reference.kind !== "source" || !project
          || reference.conversation_id !== snapshot.conversation_id || reference.revision !== snapshot.revision
          || snapshot.conversation?.scope.state !== "confirmed" || reference.sha256 !== project.sha256) return false;
      const index = project.files.findIndex(entry => entry.file_id === reference.file_id && entry.path === reference.path);
      if (index < 0) return false;
      renderProject();
      const row = el("project-file-list").children[index], original = row?.children[1];
      if (!original) return false;
      reveal?.(); row.open = true;
      original.scrollIntoView?.({ block: "nearest" }); original.focus?.({ preventScroll: true });
      return true;
    }
    return { render: renderProject, clear: clearProjectSelection, locate,
      hasDraft: () => Boolean(projectCandidates) || projectReading || Boolean(locator?.hasDraft()),
      controls(unavailable) {
        sync();
        const busy = snapshot && snapshot.busy;
        const attached = snapshot && snapshot.material;
    const projectUnavailable = unavailable || busy || !taskMode(snapshot);
    locator?.controls(unavailable);
    el("project-files").disabled = projectUnavailable || Boolean(attached || snapshot.project) || materialBlocked() || projectReading || Boolean(locator?.hasDraft());
    el("project-preview").disabled = projectUnavailable || Boolean(snapshot.project) || projectReading || !validProjectSelection();
    el("project-attach").disabled = projectUnavailable || Boolean(attached || snapshot.project) || projectReading || !projectDraft;
    el("project-remove").disabled = projectUnavailable || !snapshot.project;
    el("project-cancel").disabled = !projectCandidates && !projectReading;
    for (const row of el("project-file-list").children) {
      const checkbox = row.children[0];
      if (checkbox && checkbox.tagName.toLowerCase() === "input") checkbox.disabled = projectUnavailable || projectReading;
    }
      } };
  }
  window.PactTrailSelectionUI = { createProject, createLocator, readText, materialLines, materialRows, validMaterialName, validMaterialText, validMaterial, selectTab, setPanel };
})();
