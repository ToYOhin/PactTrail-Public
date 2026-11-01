(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const fields = (value, names) => value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
  const bounded = (value, max) => typeof value === "string" && Array.from(value).length <= max
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\ud800-\udfff]/u.test(value);
  const bytes = value => new TextEncoder().encode(value).byteLength;
  const text = (value, max) => bounded(value, max) && bytes(value) <= max;
  const integer = (value, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(value) && value >= 0 && value <= max;
  const positive = (value, max) => integer(value, max) && value > 0;
  const id = value => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  const target = value => fields(value, ["task_id", "run_id"]) && id(value.task_id) && id(value.run_id);
  const key = value => value ? value.task_id + ":" + value.run_id : "";
  const sourceId = value => bounded(value, 160) && Boolean(value.trim()) && !/[\r\n\t]/.test(value);
  const validAvailability = data => Boolean(data && (!Object.hasOwn(data, "source_target")
    || data.source_target === null || target(data.source_target)));
  function validWeb(value) {
    return value === null || fields(value, ["requested_url", "final_url", "fetched_at", "media_type", "transport"])
      && [value.requested_url, value.final_url].every(url => bounded(url, 2048) && /^https?:\/\/[^\s]+$/.test(url))
      && bounded(value.fetched_at, 64) && Boolean(value.fetched_at)
      && ["text/plain", "text/html"].includes(value.media_type) && ["http", "synthetic"].includes(value.transport);
  }
  function validArtifact(value, runId) {
    if (!fields(value, ["kind", "name", "sha256", "bytes", "text"])
      || !["report", "change_draft", "answer"].includes(value.kind) || !hash(value.sha256)
      || !positive(value.bytes, value.kind === "answer" ? 16384 : 8192)
      || !bounded(value.text, value.kind === "answer" ? 4096 : 8192) || bytes(value.text) !== value.bytes) return false;
    return value.kind === "answer" ? value.name === null : value.name === (value.kind === "report" ? "report-" : "draft-")
      + runId + (value.kind === "report" ? ".md" : ".json");
  }
  function validSource(value) {
    if (!fields(value, ["file_id", "name", "sha256", "bytes", "line_count", "web", "state", "observation", "steps", "text", "preview", "matches"])
      || !sourceId(value.file_id) || !sourceId(value.name) || !hash(value.sha256) || !positive(value.bytes, 8192)
      || !(value.line_count === null || positive(value.line_count, 8192)) || !validWeb(value.web)
      || !["verified", "changed", "missing", "unavailable", "unobserved"].includes(value.state)
      || !["read", "search", "none"].includes(value.observation) || !Array.isArray(value.steps) || value.steps.length > 8
      || !value.steps.every(step => positive(step, 8)) || new Set(value.steps).size !== value.steps.length
      || !(value.text === null || text(value.text, 8192) && bytes(value.text) === value.bytes)
      || !(value.preview === null || text(value.preview, 512)) || !Array.isArray(value.matches) || value.matches.length > 28
      || !value.matches.every(match => fields(match, ["step", "line", "text", "complete"])
        && positive(match.step, 8) && positive(match.line, 8192) && text(match.text, 8192) && typeof match.complete === "boolean")) return false;
    if (value.text !== null && (value.state !== "verified" || value.observation !== "read")) return false;
    if (value.state === "unobserved" && (value.text !== null || value.observation !== "none")) return false;
    return value.observation !== "none" || value.steps.length === 0 && value.matches.length === 0;
  }
  function validClaim(value) {
    const names = ["id", "text", "artifact_path", "artifact_line", "file_ids", "start", "end", "state"];
    const anchored = Boolean(value && Object.hasOwn(value, "anchor")), flagged = Boolean(value && Object.hasOwn(value, "issues"));
    return fields(value, [...names, ...(anchored ? ["anchor"] : []), ...(flagged ? ["issues"] : [])])
      && (!anchored || validAnchor(value.anchor))
      && (!flagged || Array.isArray(value.issues) && value.issues.length > 0 && value.issues.length <= 3
        && value.issues.every(issue => ["search_scope", "missing_source", "read_required"].includes(issue))
        && new Set(value.issues).size === value.issues.length)
      && positive(value.id, 64) && text(value.text, 240)
      && (value.artifact_path === null || sourceId(value.artifact_path)) && positive(value.artifact_line, 8192)
      && Array.isArray(value.file_ids) && value.file_ids.length <= 4 && value.file_ids.every(sourceId)
      && new Set(value.file_ids).size === value.file_ids.length
      && (value.start === null || positive(value.start)) && (value.end === null || positive(value.end))
      && ["pending", "out_of_range", "unresolved", "source_unavailable", "unobserved"].includes(value.state)
      && (!Object.hasOwn(value, "anchor") || (value.state === "pending"
        ? value.file_ids.length === 1 && value.start !== null && value.end !== null && value.start <= value.end
          && value.anchor.step !== null && value.anchor.observation !== null && value.anchor.excerpt !== null
        : value.anchor.excerpt === null && !value.anchor.truncated));
  }
  function validAnchor(value) {
    return fields(value, ["id", "step", "observation", "excerpt", "truncated"])
      && typeof value.id === "string" && /^[a-f0-9]{16}$/.test(value.id)
      && (value.step === null || positive(value.step, 8)) && [null, "read", "search"].includes(value.observation)
      && (value.step === null) === (value.observation === null)
      && (value.excerpt === null || text(value.excerpt, 1024)) && typeof value.truncated === "boolean";
  }
  function valid(value) {
    if (!fields(value, ["version", "kind", "conversation_id", "revision", "task_id", "run_id", "record_sha256", "record_state", "artifact_state", "artifact", "sources", "claims", "truncated"])
      || value.version !== 1 || value.kind !== "source_evidence" || !id(value.conversation_id) || !integer(value.revision)
      || !id(value.task_id) || !id(value.run_id) || !(value.record_sha256 === null || hash(value.record_sha256))
      || !["verified", "missing", "unavailable"].includes(value.record_state)
      || !["verified", "missing", "changed", "unknown", "none", "unavailable"].includes(value.artifact_state)
      || !(value.artifact === null || validArtifact(value.artifact, value.run_id))
      || !Array.isArray(value.sources) || value.sources.length > 4 || !value.sources.every(validSource)
      || new Set(value.sources.map(source => source.file_id)).size !== value.sources.length
      || !Array.isArray(value.claims) || value.claims.length > 64 || !value.claims.every(validClaim)
      || new Set(value.claims.map(claim => claim.id)).size !== value.claims.length || typeof value.truncated !== "boolean") return false;
    if ((value.artifact_state === "verified") !== Boolean(value.artifact) || value.artifact === null && value.claims.length) return false;
    for (const claim of value.claims) if (claim.anchor && claim.state === "pending") {
      const source = value.sources.find(item => item.file_id === claim.file_ids[0]);
      if (!source || source.state !== "verified" || source.observation === "none" || !source.steps.includes(claim.anchor.step)
        || source.line_count !== null && claim.end > source.line_count
        || claim.anchor.observation === "read" && source.observation !== "read") return false;
    }
    return value.record_state === "verified" ? hash(value.record_sha256)
      : value.record_sha256 === null && value.sources.length === 0 && value.claims.length === 0 && value.artifact === null;
  }
  const claimText = { pending: "模型引用待核对：位置可定位，尚未确认支持这项表述。",
    out_of_range: "模型引用待核对：行号超出已知原文范围。", unresolved: "模型引用待核对：未能唯一匹配来源或位置。",
    source_unavailable: "模型引用待核对：当时来源已变化、缺失或暂不可核对。", unobserved: "模型引用待核对：没有对应的实际读取或搜索证据。" };
  const anchorText = { pending: "实际来源锚点：位置可核对，尚未确认支持这项表述。",
    out_of_range: "来源锚点待核对：位置超出实际来源范围。", unresolved: "来源锚点待核对：未能绑定本次实际读取或搜索证据。",
    source_unavailable: "来源锚点待核对：当时来源已变化、缺失或暂不可核对。",
    unobserved: "来源锚点待核对：没有覆盖此位置的实际读取或搜索证据。" };
  const issueText = { search_scope: "搜索锚点只覆盖保留命中，不覆盖全文。",
    missing_source: "缺少唯一文件身份，未猜测对应文件。", read_required: "全文表述未绑定实际读取。" };
  const sourceText = { verified: "运行时保存的来源快照与身份一致", changed: "运行时保存的来源快照已变化", missing: "运行时保存的来源快照已缺失",
    unavailable: "来源暂不可核对", unobserved: "没有实际读取或搜索记录" };
  const artifactText = { verified: "模型原文与保存身份一致。", missing: "成果原文已缺失。", changed: "成果原文已变化，未展示未经核对的内容。",
    unknown: "原保存结果未知。", none: "这份记录没有完整成果原文；仍可查看已观察的来源证据。", unavailable: "成果原文暂不可核对。" };
  const errorText = code => ({ stale_request: "当前状态已变化，请重新读取。", closed: "服务已关闭，请重新打开页面。",
    record_missing: "这份记录已缺失，请刷新历史。" })[code] || "暂时无法核对来源引用，请稍后重新读取。";
  const paragraph = value => { const node = document.createElement("p"); node.textContent = value; return node; };
  const pre = value => { const node = document.createElement("pre"); node.className = "source-evidence-text"; node.tabIndex = 0; node.textContent = value; return node; };
  const lines = value => value.split(/\r\n|\r|\n/);
  const numbered = (value, start = 1) => lines(value).map((line, index) => (start + index) + " │ " + line).join("\n");
  function create({ state, request, updateControls }) {
    const panel = el("source-citations-panel");
    let owner = "", run = "", selected = null, generation = 0, pending = null, value = null, painted = null, status = "", closed = false;
    const currentTarget = data => target(data?.source_target) ? data.source_target : null;
    const available = data => !closed && data?.agent_history_available === true && id(data.conversation_id);
    const blocked = current => current.locked || current.mutating;
    const chosen = data => selected || currentTarget(data);
    const sourceNodes = new Map();
    function clear() {
      generation += 1; pending = null; value = null; painted = null; status = ""; sourceNodes.clear();
      el("source-citations-content").hidden = true;
      for (const name of ["original", "claims", "evidence"]) el("source-citations-" + name).open = false;
      for (const name of ["note", "original-text"]) el("source-citations-" + name).textContent = "";
      for (const name of ["claim-list", "source-list"]) el("source-citations-" + name).replaceChildren();
    }
    function paintSource(source) {
      const row = document.createElement("details"), heading = document.createElement("summary");
      row.className = "supporting-details"; heading.textContent = source.file_id + " · " + source.name;
      row.append(heading, paragraph(sourceText[source.state] + "；" + ({ read: "实际读取", search: "实际搜索命中", none: "未观察" })[source.observation]
        + (source.steps.length ? " · 步骤 " + source.steps.join("、") : "") + "。"),
      paragraph(source.bytes + " 字节 · " + (source.line_count === null ? "行数未知" : source.line_count + " 行") + " · SHA256 " + source.sha256));
      if (source.web) row.append(paragraph("请求 URL：" + source.web.requested_url + "\n最终 URL：" + source.web.final_url
        + "\n获取时间：" + source.web.fetched_at + " · " + source.web.transport));
      if (source.text !== null) row.append(paragraph("已按保存身份核对完整原文；行号来自这份原文。"), pre(numbered(source.text)));
      else if (source.preview !== null) row.append(paragraph(source.state === "verified"
        ? "当时读取的保存片段（可能不是全文）：" : "以下仅为当时记录的片段，当前来源未核验："), pre(source.preview));
      if (source.matches.length) {
        row.append(paragraph(source.state === "verified" ? "实际搜索命中；命中位置不等于支持模型结论：" : "当时搜索命中；当前来源未核验："));
        for (const match of source.matches) row.append(paragraph("步骤 " + match.step + " · 第 " + match.line + " 行"
          + (match.complete ? " · 完整命中行" : " · 命中预览未含完整行")), pre(numbered(match.text, match.line)));
      }
      if (source.observation === "none") row.append(paragraph("不根据模型自报来源补读或推断已读内容。"));
      sourceNodes.set(source.file_id, row); return row;
    }
    function paintClaim(claim) {
      const row = document.createElement("section");
      row.append(paragraph("引用 " + claim.id + " · " + (claim.artifact_path ? claim.artifact_path + " · " : "成果正文 · ")
        + "第 " + claim.artifact_line + " 行"), pre(claim.text), paragraph((claim.anchor ? anchorText : claimText)[claim.state]
          + (claim.issues ? " " + claim.issues.map(issue => issueText[issue]).join(" ") : "")));
      const matches = claim.file_ids.map(fileId => value.sources.find(source => source.file_id === fileId)).filter(Boolean);
      if (matches.length) {
        const button = document.createElement("button"); button.type = "button"; button.className = "text-button";
        button.textContent = claim.state === "pending" ? (claim.anchor ? "查看实际来源锚点（表述待核对）" : "查看对应原文位置（仍待核对）")
          : matches.some(source => source.observation !== "none") ? "查看已观察的来源" : "查看来源记录";
        button.addEventListener("click", () => {
          el("source-citations-evidence").open = true;
          for (const source of matches) { const node = sourceNodes.get(source.file_id); if (node) node.open = true; }
          if (claim.anchor) {
            if (claim.state === "pending" && claim.anchor.excerpt !== null) {
              const excerpt = Array.from(numbered(claim.anchor.excerpt, claim.start));
              focusText.textContent = (claim.anchor.observation === "search" ? "实际搜索保留的命中片段" : "实际读取的来源片段")
                + " · 步骤 " + claim.anchor.step + " · 第 " + claim.start + (claim.start === claim.end ? "" : "–" + claim.end)
                + " 行（位置可核对，表述仍待核对）：\n" + excerpt.slice(0, 2048).join("")
                + (claim.anchor.truncated || excerpt.length > 2048 ? "\n片段已截断；未显示内容不作为本锚点证据。" : "");
            } else focusText.textContent = "此来源锚点尚未绑定可核对片段；请查看实际观察记录，不以模型自报位置定位。";
          } else if (matches.length === 1 && matches[0].text !== null && claim.start !== null && claim.end !== null
            && claim.start <= claim.end && claim.end <= lines(matches[0].text).length) {
            const rows = lines(matches[0].text), first = Math.max(1, claim.start - 2), last = Math.min(rows.length, claim.end + 2, first + 11);
            const excerpt = Array.from(numbered(rows.slice(first - 1, last).join("\n"), first));
            focusText.textContent = "模型所指位置及上下文（仍待核对）：\n" + excerpt.slice(0, 2048).join("")
              + (last < claim.end || excerpt.length > 2048 ? "\n局部预览已截断，完整原文可在来源中查看。" : "");
          } else focusText.textContent = "未能核对模型所指位置；请查看当时实际读取或搜索的来源内容。";
          focusText.hidden = false; focusText.focus();
        });
        row.append(button);
      }
      const focusText = pre(""); focusText.hidden = true; row.append(focusText); return row;
    }
    function paint() {
      if (painted === value) return;
      painted = value; el("source-citations-content").hidden = !value;
      if (!value) return;
      el("source-citations-note").textContent = (value.record_state === "verified" ? artifactText[value.artifact_state]
        : value.record_state === "missing" ? "任务记录已缺失，未推断引用或来源。" : "任务记录暂不可核对，未推断引用或来源。")
        + " 来源身份和位置能核对，不代表模型表述已获得证实。"
        + (value.claims.some(claim => claim.issues) ? " " + value.claims.filter(claim => claim.issues).length + " 项引用范围或读取待核对。" : "")
        + (value.truncated ? " 引用识别列表已截断，完整模型原文仍保留。" : "");
      el("source-citations-original").hidden = !value.artifact;
      el("source-citations-original-text").textContent = value.artifact?.text || "";
      sourceNodes.clear();
      el("source-citations-source-list").replaceChildren(...(value.sources.length ? value.sources.map(paintSource) : [paragraph("没有可核对的实际来源记录。") ]));
      el("source-citations-claim-list").replaceChildren(...(value.claims.length ? value.claims.map(paintClaim) : [paragraph("没有识别到带位置的模型引用；不据此推断内容已有来源支持。") ]));
    }
    function render() {
      const current = state(), data = current.snapshot, nextOwner = available(data) ? data.conversation_id : "", nextRun = data?.task?.run_id || "";
      if (owner !== nextOwner || run !== nextRun) { owner = nextOwner; run = nextRun; selected = null; clear(); panel.open = false; }
      const candidate = chosen(data), active = currentTarget(data);
      panel.hidden = !nextOwner || !candidate;
      el("source-citations-load").disabled = !nextOwner || !candidate || blocked(current) || Boolean(pending);
      el("source-citations-load").textContent = value ? "重新核对引用与来源" : "读取引用与来源";
      el("source-citations-current").hidden = !active || key(candidate) === key(active);
      el("source-citations-current").disabled = !active || blocked(current) || Boolean(pending);
      el("source-citations-status").textContent = status || "只读查看实际证据，模型引用仍需人工核对。";
      paint();
    }
    const owns = binding => !closed && owner === binding.owner && run === binding.run && generation === binding.generation
      && state().snapshot?.conversation_id === binding.owner && (state().snapshot?.task?.run_id || "") === binding.run;
    async function verify(item, binding) {
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(item.text));
      if (!owns(binding)) return false;
      if (state().snapshot?.revision !== binding.revision) throw Error("stale_request");
      if (Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("") !== item.sha256) throw Error("invalid_response");
      return true;
    }
    async function read() {
      render();
      const current = state(), data = current.snapshot, candidate = chosen(data);
      if (!available(data) || !candidate || blocked(current) || pending) return;
      const binding = { owner, run, generation, revision: data.revision }, requested = { ...candidate };
      selected = requested; pending = binding; status = "正在核对保存原文与实际来源证据…"; updateControls();
      try {
        const response = await request("/source-evidence", { action: "read", conversation_id: owner, revision: data.revision, ...requested });
        if (!owns(binding)) return;
        if (state().snapshot?.revision !== binding.revision) throw Error("stale_request");
        if (!valid(response) || response.conversation_id !== binding.owner || response.revision !== binding.revision || key(response) !== key(requested)) throw Error("invalid_response");
        if (response.artifact && !await verify(response.artifact, binding)) return;
        for (const source of response.sources) if (source.text !== null && !await verify(source, binding)) return;
        value = response; painted = null; status = "已核对保存身份与实际观察记录；模型引用仍待核对。";
      } catch (error) {
        if (owns(binding)) { clear(); status = errorText(error.message); }
      } finally { if (pending === binding) pending = null; updateControls(); }
    }
    function open(binding) {
      if (!target(binding) || !available(state().snapshot) || blocked(state())) return false;
      render(); selected = { ...binding }; clear(); render(); panel.open = true;
      el("source-citations-load").focus(); void read(); return true;
    }
    el("source-citations-load").addEventListener("click", read);
    el("source-citations-current").addEventListener("click", () => open(currentTarget(state().snapshot)));
    el("draft-review-sources").addEventListener("click", () => {
      if (el("draft-review-sources").disabled) return;
      Promise.resolve().then(() => { if (!el("draft-review-dialog").open) open(currentTarget(state().snapshot)); });
    });
    return { render, open, close() { closed = true; selected = null; clear(); panel.open = false; render(); } };
  }
  window.PactTrailSourceCitationsUI = { valid, validArtifact, validSource, validClaim, validAnchor, validAvailability, validTarget: target, create };
})();
