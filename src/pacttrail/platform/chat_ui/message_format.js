(() => {
  "use strict";
  const node = (tag, className, text = "") => {
    const value = document.createElement(tag); value.className = className; value.textContent = text; return value;
  };
  function render(body, text, { citationCount = 0, onCodeCopy = () => {}, onCitation = () => {} } = {}) {
    const codeBlocks = [], citations = [], children = [];
    const count = Number.isInteger(citationCount) && citationCount >= 0 && citationCount <= 3 ? citationCount : 0;
    function inline(parent, value, bold = true) {
      const tokens = bold ? /`[^`\r\n]+`|\*\*[^\r\n]+?\*\*|\[([1-9]\d?)\]/g : /`[^`\r\n]+`|\[([1-9]\d?)\]/g;
      let cursor = 0, match;
      const plain = content => { if (content) parent.appendChild(document.createTextNode(content)); };
      while ((match = tokens.exec(value))) {
        plain(value.slice(cursor, match.index)); const token = match[0];
        if (token.startsWith("`")) parent.appendChild(node("code", "message-inline-code", token.slice(1, -1)));
        else if (token.startsWith("**")) {
          const strong = node("strong", ""); inline(strong, token.slice(2, -2), false); parent.appendChild(strong);
        } else {
          const index = Number(token.slice(1, -1));
          // Markdown links/images and numbers without matching trusted metadata remain text.
          if (index > count || value[match.index - 1] === "!" || value[match.index + token.length] === "(") plain(token);
          else {
            const button = node("button", "message-citation", token); button.type = "button";
            button.setAttribute("aria-label", "查看来源 " + index);
            button.addEventListener("click", () => onCitation(index)); parent.appendChild(button);
            citations.push({ button, index });
          }
        }
        cursor = tokens.lastIndex;
      }
      plain(value.slice(cursor));
    }
    function prose(value) {
      let paragraph = [], list = null, listKind = "";
      const flush = () => {
        if (paragraph.length) {
          const p = node("p", "message-paragraph"); inline(p, paragraph.join("\n")); children.push(p); paragraph = [];
        }
      };
      for (const line of value.split(/\r?\n/)) {
        if (!line.trim()) { flush(); list = null; listKind = ""; continue; }
        const heading = /^ {0,3}(#{1,6})[\t ]+(.+)$/.exec(line);
        if (heading) {
          flush(); list = null; listKind = "";
          const title = node("h" + Math.min(4, heading[1].length + 1), "message-heading-block");
          inline(title, heading[2]); children.push(title); continue;
        }
        const item = /^[\t ]*(?:([-+*])|([0-9]{1,3})[.)])[\t ]+(.+)$/.exec(line);
        if (item) {
          flush(); const kind = item[1] ? "ul" : "ol";
          if (!list || listKind !== kind) {
            list = node(kind, "message-list"); listKind = kind;
            if (kind === "ol" && Number(item[2]) !== 1) list.setAttribute("start", String(Number(item[2])));
            children.push(list);
          }
          const li = node("li", ""); inline(li, item[3]); list.appendChild(li); continue;
        }
        list = null; listKind = ""; paragraph.push(line);
      }
      flush();
    }
    // Only a small DOM-built Markdown subset is interpreted. No HTML, arbitrary links or image URLs.
    const fences = /^[\t ]*```([a-zA-Z0-9_+.#-]*)[\t ]*\r?\n([\s\S]*?)^[\t ]*```[\t ]*(?=\r?$)/gm;
    let cursor = 0, match;
    while ((match = fences.exec(text))) {
      prose(text.slice(cursor, match.index));
      const block = node("div", "message-code-block"), toolbar = node("div", "message-code-toolbar");
      const button = node("button", "text-button message-code-copy", "复制代码"); button.type = "button";
      button.setAttribute("aria-label", "复制" + (match[1] ? " " + match[1] : "") + "代码");
      const feedback = node("span", "message-code-feedback"); feedback.setAttribute("role", "status");
      const record = { button, feedback, text: match[2] };
      button.addEventListener("click", () => onCodeCopy(record));
      toolbar.appendChild(node("span", "message-code-language", match[1] || "代码"));
      toolbar.appendChild(button); toolbar.appendChild(feedback);
      const pre = node("pre", "message-code"); pre.appendChild(node("code", "", record.text));
      block.appendChild(toolbar); block.appendChild(pre); children.push(block); codeBlocks.push(record);
      cursor = fences.lastIndex;
    }
    prose(text.slice(cursor)); body.replaceChildren(...children);
    return { codeBlocks, citations };
  }
  window.PactTrailMessageFormat = { render };
})();
