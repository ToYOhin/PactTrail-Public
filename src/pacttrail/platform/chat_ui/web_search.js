(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const exact = (value, names) => value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
  const bounded = (value, max) => typeof value === "string" && Array.from(value).length <= max
    && !/[\u0000-\u001f\u007f-\u009f\ud800-\udfff]/u.test(value);
  const providerLabel = value => value === "bing" ? "Bing" : value === "deepseek" ? "DeepSeek Search" : "";
  function publicURL(value) {
    if (!window.PactTrailTaskUI.validWebURL(value)) return false;
    const host = new URL(value).hostname.toLowerCase().replace(/\.$/, "");
    if (!host.includes(".") || host.includes(":") || host.includes("[")
      || /(?:^|\.)(localhost|local|internal|invalid|test|onion)$/.test(host)) return false;
    if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
      const [a, b, c] = host.split(".").map(Number);
      if (a === 0 || a === 10 || a === 127 || a >= 224 || a === 169 && b === 254
        || a === 172 && b >= 16 && b <= 31 || a === 192 && b === 168
        || a === 100 && b >= 64 && b <= 127 || a === 198 && [18, 19].includes(b)
        || a === 192 && b === 0 || a === 198 && b === 51 && c === 100
        || a === 203 && b === 0 && c === 113) return false;
    }
    return true;
  }
  const timestamp = value => {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)) return false;
    const date = new Date(value);
    return Number.isFinite(date.getTime()) && date.getUTCFullYear() >= 1
      && date.toISOString() === value.replace("Z", ".000Z");
  };
  const withinDomains = (url, domains) => !domains.length || domains.some(domain => {
    const host = new URL(url).hostname.toLowerCase().replace(/\.$/, "");
    return host === domain || host.endsWith("." + domain);
  });
  function validDomain(value) {
    if (typeof value !== "string" || !publicURL("https://" + value + "/")) return false;
    const url = new URL("https://" + value + "/");
    return url.hostname === value && !value.endsWith(".") && !/^\d+$/.test(value.split(".").at(-1))
      && url.pathname === "/" && !url.search && !url.hash;
  }
  function validPage(value, domains) {
    return Boolean(exact(value, ["url", "fetched_at", "text", "selection", "sha256"])
      && value.selection === "relevant_excerpt" && publicURL(value.url) && withinDomains(value.url, domains)
      && timestamp(value.fetched_at) && typeof value.text === "string" && value.text.trim()
      && !/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\ud800-\udfff]/u.test(value.text)
      && new TextEncoder().encode(value.text).byteLength <= 4096
      && typeof value.sha256 === "string" && /^[a-f0-9]{64}$/.test(value.sha256));
  }
  function validSearch(value) {
    if (!value || ![1, 2].includes(value.version)) return false;
    const fields = ["version", "query", "searched_at", "provider", "transport", "results"];
    if (value.version === 2) fields.push("queries", "official_domains");
    if (!exact(value, fields) || !bounded(value.query, 160) || !value.query.trim()
      || !["bing", "deepseek"].includes(value.provider) || !["http", "synthetic"].includes(value.transport)
      || !timestamp(value.searched_at)
      || !Array.isArray(value.results) || value.results.length < 1 || value.results.length > 3
      || !value.results.every(result => result && typeof result === "object" && !Array.isArray(result))
      || new Set(value.results.map(result => result.url)).size !== value.results.length
      || new TextEncoder().encode(JSON.stringify(value)).byteLength > 8192) return false;
    const domains = value.version === 2 ? value.official_domains : [];
    if (value.version === 2 && (!Array.isArray(value.queries) || value.queries.length < 1 || value.queries.length > 2
      || value.queries[0] !== value.query || !value.queries.every(query => bounded(query, 160)
        && query.trim() && query.trim().split(/\s+/u).join(" ") === query)
      || new Set(value.queries).size !== value.queries.length || !Array.isArray(domains) || domains.length > 3
      || !domains.every(validDomain) || new Set(domains).size !== domains.length)) return false;
    return value.results.every(result => {
      const names = ["title", "url", "snippet"];
      if (value.version === 2 && result && Object.hasOwn(result, "page")) names.push("page");
      return exact(result, names) && bounded(result.title, 160) && result.title.trim() && publicURL(result.url)
        && withinDomains(result.url, domains) && bounded(result.snippet, 600)
        && (!Object.hasOwn(result, "page") || validPage(result.page, domains));
    });
  }
  function validMessage(message) {
    return Boolean(message && (!Object.hasOwn(message, "web_search")
      || message.role === "assistant" && message.status === "complete" && Array.isArray(message.sources)
        && message.sources.length === 0 && validSearch(message.web_search)));
  }
  function validState(value) {
    return value === null || Boolean(exact(value, ["run_id", "status", "error"])
      && typeof value.run_id === "string" && /^[a-f0-9]{32}$/.test(value.run_id)
      && (["searching", "ready"].includes(value.status) ? value.error === null
        : value.status === "error" ? ["unavailable", "empty", "timeout", "response_limit", "usage_unavailable", "budget_limit"].includes(value.error)
          : value.status === "cancelled" && value.error === "cancelled"));
  }
  const node = (tag, className, value = "") => {
    const result = document.createElement(tag); result.className = className; result.textContent = value; return result;
  };
  function renderSearch(value) {
    if (!validSearch(value)) return null;
    const details = node("details", "web-search-sources");
    details.appendChild(node("summary", "", "来源 · " + value.results.length));
    const content = node("div", "web-search-source-body");
    const hasPages = value.results.some(result => result.page);
    const note = value.version === 1 ? "搜索摘要，未读取全文。" : hasPages
      ? "已读取网页选段，选段不代表全文。" : "搜索摘要，未读取网页正文。";
    content.appendChild(node("p", "web-search-note", (value.transport === "synthetic" ? "合成" : "") + note));
    content.appendChild(node("p", "web-search-query", "检索词：" + value.query));
    if (value.queries?.length === 2) content.appendChild(node("p", "web-search-query", "二次检索：" + value.queries[1]));
    if (value.official_domains?.length) content.appendChild(node("p", "web-search-query", "限定域名：" + value.official_domains.join("、")));
    content.appendChild(node("p", "web-search-time", "检索时间：" + value.searched_at + " · " + providerLabel(value.provider)));
    const list = node("ol", "web-search-result-list");
    for (const result of value.results) {
      const item = node("li", "web-search-result"), link = node("a", "web-search-link", result.title);
      link.setAttribute("href", result.url); link.setAttribute("target", "_blank");
      link.setAttribute("rel", "noopener noreferrer"); link.setAttribute("referrerpolicy", "no-referrer");
      item.appendChild(link); item.appendChild(node("p", "web-search-url", result.url));
      if (result.snippet) item.appendChild(node("p", "web-search-snippet", result.snippet));
      if (result.page) {
        item.appendChild(node("p", "web-search-note", "已读取网页选段"));
        item.appendChild(node("p", "web-search-snippet web-search-page-text", result.page.text));
        item.appendChild(node("p", "web-search-time", "获取时间：" + result.page.fetched_at));
        if (result.page.url !== result.url) item.appendChild(node("p", "web-search-url", "读取地址：" + result.page.url));
      } else if (value.version === 2) item.appendChild(node("p", "web-search-note", "搜索摘要，未读取网页正文。"));
      list.appendChild(item);
    }
    content.appendChild(list); details.appendChild(content); return details;
  }
  function create({ state, updateControls }) {
    const toggle = el("web-search-toggle"), container = el("web-search-controls"), feedback = el("web-search-feedback");
    let owner = null, unavailable = true, closed = false;
    const available = () => {
      const current = state(), data = current.snapshot;
      return Boolean(!closed && !unavailable && !current.locked && !current.mutating && !data?.busy
        && data?.web_search_available === true && data?.conversation && data?.agent_history_available
        && el("task-intent").value === "answer");
    };
    function controls(value = false) {
      unavailable = value;
      const data = state().snapshot, nextOwner = data?.conversation_id ?? null;
      if (nextOwner !== owner) { toggle.checked = false; owner = nextOwner; }
      const visible = Boolean(data?.web_search_available === true && data?.conversation && data?.agent_history_available
        && el("task-intent").value === "answer");
      container.hidden = !visible; toggle.disabled = !available();
      if (!visible) toggle.checked = false;
      const search = data?.web_search_state;
      let message = !closed && !unavailable && visible && !data?.busy && data.web_search_provider === "deepseek"
        ? "DeepSeek Search · 每次成功检索 $0.00075，每轮最多两次；正文由本机读取。" : "";
      if (!closed && !unavailable && data?.web_search_available === true && search && validState(search)
          && data.operation?.id === search.run_id) {
        const errors = { unavailable: "联网搜索暂不可用，本轮未调用模型。", empty: "未找到可用来源，本轮未调用模型。",
          timeout: "联网搜索超时，本轮未调用模型。", response_limit: "搜索结果超过本次限制，本轮未调用模型。",
          usage_unavailable: "搜索费用未能核实，本轮已停止。", budget_limit: "本次搜索预算不足，本轮已停止。" };
        if (search.status === "searching") message = "正在联网搜索…";
        else if (search.status === "error") message = errors[search.error];
        else if (search.status === "cancelled") message = "联网搜索已停止。";
      }
      feedback.textContent = message; feedback.hidden = !message;
    }
    toggle.addEventListener("change", () => { if (!available()) toggle.checked = false; updateControls(); });
    return { controls, fields: () => available() && toggle.checked ? { web_search: true } : {},
      close: () => { closed = true; toggle.checked = false; controls(true); } };
  }
  window.PactTrailWebSearchUI = { validSearch, validMessage, validState, providerLabel, create, renderSearch };
})();
