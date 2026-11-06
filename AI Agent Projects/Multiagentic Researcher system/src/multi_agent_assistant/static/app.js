const form = document.querySelector("#chat-form");
const input = document.querySelector("#question");
const sendButton = document.querySelector("#send-button");
const conversation = document.querySelector("#conversation");
const welcome = document.querySelector("#welcome");
const characterCount = document.querySelector("#char-count");

const reportEmpty = document.querySelector("#report-empty");
const reportContent = document.querySelector("#report-content");
const reportSub = document.querySelector("#report-sub");
const reportTokens = document.querySelector("#report-tokens");
const reportExternal = document.querySelector("#report-external");
const reportPerformance = document.querySelector("#report-performance");
// Agent flow lives in the LEFT panel.
const flowEmpty = document.querySelector("#flow-empty");
const reportFlow = document.querySelector("#report-flow");

const AGENT_ICONS = {
  supervisor: "⌘",
  web_search: "🔍",
  weather: "🌤",
  formatter: "✎",
};
const AGENT_LABELS = {
  supervisor: "Supervisor",
  web_search: "Web Search",
  weather: "Weather",
  formatter: "Result Formatter",
};
const PROVIDER_ICONS = { Tavily: "🔍", OpenWeather: "🌤" };

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmt(n) {
  return Number(n || 0).toLocaleString();
}

function fmtMs(ms) {
  const value = Number(ms || 0);
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
}

// Minimal, XSS-safe Markdown renderer: escapes first, then applies a small set
// of inline/block formats. Links are restricted to http/https.
function renderMarkdown(raw) {
  const inline = (text) => {
    let out = escapeHtml(text);
    out = out.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/gi, (_, label, url) => {
      return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
    });
    return out;
  };

  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let list = null;
  const closeList = () => {
    if (list) {
      html.push(`<${list.type}>${list.items.join("")}</${list.type}>`);
      list = null;
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      closeList();
      continue;
    }
    const heading = trimmed.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 2, 6);
      html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }
    const bullet = trimmed.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      if (!list || list.type !== "ul") {
        closeList();
        list = { type: "ul", items: [] };
      }
      list.items.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    const ordered = trimmed.match(/^\d+\.\s+(.*)$/);
    if (ordered) {
      if (!list || list.type !== "ol") {
        closeList();
        list = { type: "ol", items: [] };
      }
      list.items.push(`<li>${inline(ordered[1])}</li>`);
      continue;
    }
    closeList();
    html.push(`<p>${inline(trimmed)}</p>`);
  }
  closeList();
  return html.join("");
}

function createMessageShell(role, label) {
  const message = document.createElement("article");
  message.className = `message message-${role}`;

  const heading = document.createElement("div");
  heading.className = "message-heading";
  heading.textContent = label;

  const body = document.createElement("div");
  body.className = "message-body";

  message.append(heading, body);
  conversation.append(message);
  scrollToLatest();
  return { message, body };
}

// Keep the newest message in view by scrolling the conversation container
// (not the whole page) to its bottom.
function scrollToLatest() {
  conversation.scrollTo({ top: conversation.scrollHeight, behavior: "smooth" });
}

/* ---------- Report panel rendering ---------- */

function labelFor(name) {
  return AGENT_LABELS[name] || name || "Agent";
}

function renderFlow(agents) {
  if (!Array.isArray(agents) || agents.length === 0) {
    reportFlow.hidden = true;
    reportFlow.innerHTML = "";
    flowEmpty.hidden = false;
    flowEmpty.textContent = "No agent activity recorded.";
    return;
  }
  const steps = agents
    .map((agent, index) => {
      const arrow =
        index < agents.length - 1 ? `<span class="flow-arrow" aria-hidden="true">↓</span>` : "";
      return `
        <div class="flow-step flow-${escapeHtml(agent.name || "agent")}">
          <span class="flow-icon" aria-hidden="true">${escapeHtml(AGENT_ICONS[agent.name] || "•")}</span>
          <span class="flow-text">
            <span class="flow-name">${escapeHtml(agent.label || labelFor(agent.name))}</span>
            ${agent.detail ? `<span class="flow-detail">${escapeHtml(agent.detail)}</span>` : ""}
          </span>
        </div>${arrow}`;
    })
    .join("");
  flowEmpty.hidden = true;
  reportFlow.hidden = false;
  reportFlow.innerHTML = steps;
}

function renderTokens(usage, usageTotal) {
  const llmCalls = Array.isArray(usage) ? usage.filter((u) => (u.total_tokens || 0) > 0) : [];
  if (llmCalls.length === 0) {
    reportTokens.innerHTML = `<p class="report-note">No LLM calls recorded.</p>`;
    return;
  }
  const rows = llmCalls
    .map(
      (u) => `
      <div class="metric-row">
        <span class="metric-name"><span class="dot ${escapeHtml(u.agent || "")}"></span>${escapeHtml(labelFor(u.agent))}</span>
        <span>${fmt(u.prompt_tokens)}</span>
        <span>${fmt(u.completion_tokens)}</span>
        <span class="strong">${fmt(u.total_tokens)}</span>
      </div>`
    )
    .join("");
  const foot = usageTotal
    ? `
      <div class="metric-row metric-foot">
        <span>Total</span>
        <span>${fmt(usageTotal.prompt_tokens)}</span>
        <span>${fmt(usageTotal.completion_tokens)}</span>
        <span class="strong">${fmt(usageTotal.total_tokens)}</span>
      </div>`
    : "";
  reportTokens.innerHTML = `
    <div class="metric-table tokens">
      <div class="metric-row metric-head"><span>Agent</span><span>Input</span><span>Output</span><span>Total</span></div>
      ${rows}${foot}
    </div>`;
}

function renderExternal(external) {
  if (!Array.isArray(external) || external.length === 0) {
    reportExternal.innerHTML = `<p class="report-note">No external API calls for this request.</p>`;
    return;
  }
  const rows = external
    .map((call) => {
      const ok = (call.status || "").toLowerCase() === "success";
      return `
      <div class="api-card">
        <div class="api-top">
          <span class="api-provider"><span class="api-ic" aria-hidden="true">${escapeHtml(PROVIDER_ICONS[call.provider] || "☍")}</span>${escapeHtml(call.provider || "API")}</span>
          <span class="status-pill ${ok ? "ok" : "fail"}">${escapeHtml(call.status || "unknown")}</span>
        </div>
        <div class="api-meta">
          <span class="api-op">${escapeHtml(call.operation || "request")}</span>
          <span class="api-dur">${fmtMs(call.duration_ms)}</span>
        </div>
        ${call.detail ? `<div class="api-detail">${escapeHtml(call.detail)}</div>` : ""}
      </div>`;
    })
    .join("");
  reportExternal.innerHTML = `<div class="api-list">${rows}</div>`;
}

function renderPerformance(performance) {
  if (!Array.isArray(performance) || performance.length === 0) {
    reportPerformance.innerHTML = `<p class="report-note">No performance data recorded.</p>`;
    return;
  }
  const rows = performance
    .map((p) => {
      const ok = (p.status || "success").toLowerCase() === "success";
      const retries = Number(p.retries || 0);
      return `
      <div class="metric-row perf">
        <span class="metric-name"><span class="dot ${escapeHtml(p.name || "")}"></span>${escapeHtml(p.label || labelFor(p.name))}</span>
        <span class="perf-dur">${fmtMs(p.duration_ms)}</span>
        <span class="perf-retry ${retries > 0 ? "warn" : ""}">${retries}</span>
        <span class="status-pill ${ok ? "ok" : "fail"}">${ok ? "OK" : "Fail"}</span>
      </div>`;
    })
    .join("");
  reportPerformance.innerHTML = `
    <div class="metric-table perf">
      <div class="metric-row metric-head"><span>Agent</span><span>Time</span><span>Retries</span><span>Status</span></div>
      ${rows}
    </div>`;
}

function renderReport(payload) {
  renderFlow(payload.agents);
  renderTokens(payload.usage, payload.usage_total);
  renderExternal(payload.external_api);
  renderPerformance(payload.performance);

  const totalTokens = payload.usage_total ? payload.usage_total.total_tokens : 0;
  const totalTime = Array.isArray(payload.performance)
    ? payload.performance.reduce((sum, p) => sum + Number(p.duration_ms || 0), 0)
    : 0;
  reportSub.textContent = `${fmt(totalTokens)} tokens · ${fmtMs(totalTime)} total`;

  reportEmpty.hidden = true;
  reportContent.hidden = false;
}

function showReportLoading() {
  resetReportEmptyText();
  reportSub.textContent = "Processing…";
  flowEmpty.hidden = false;
  flowEmpty.textContent = "Running the agent pipeline…";
  reportFlow.hidden = true;
}

function showReportError(message) {
  // Replace the neutral "No run yet" empty state with a clear error so the
  // panel does not look like nothing happened.
  reportContent.hidden = true;
  reportEmpty.hidden = false;
  const title = reportEmpty.querySelector(".report-empty-title");
  const text = reportEmpty.querySelector(".report-empty-text");
  if (title) title.textContent = "Run failed";
  if (text) {
    text.textContent =
      message || "The request could not be completed. Check the configuration and try again.";
  }
  reportSub.textContent = "Request failed";
  flowEmpty.hidden = false;
  flowEmpty.textContent = "The request did not complete.";
  reportFlow.hidden = true;
}

function resetReportEmptyText() {
  const title = reportEmpty.querySelector(".report-empty-title");
  const text = reportEmpty.querySelector(".report-empty-text");
  if (title) title.textContent = "No run yet";
  if (text) {
    text.textContent =
      "Submit a question and token usage, external API calls, and agent performance will appear here.";
  }
}

/* ---------- Chat ---------- */

function setLoading(loading) {
  sendButton.disabled = loading;
  input.disabled = loading;
  sendButton.querySelector("span").textContent = loading ? "Working…" : "Submit";
}

async function ask(question) {
  const cleanQuestion = question.trim();
  if (!cleanQuestion || sendButton.disabled) return;

  welcome.hidden = true;
  const user = createMessageShell("user", "YOU");
  user.body.textContent = cleanQuestion;

  const response = createMessageShell("assistant", "ASSISTANT");
  response.message.classList.add("message-pending");
  response.body.innerHTML = `
    <span class="spinner" aria-hidden="true"></span>
    <span class="pending-text">The agent pipeline is working on your request…</span>`;
  setLoading(true);
  showReportLoading();

  try {
    const result = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: cleanQuestion }),
    });
    const payload = await result.json();
    if (!result.ok) throw new Error(payload.error || "The request could not be completed.");

    response.message.classList.remove("message-pending");
    response.body.innerHTML = renderMarkdown(payload.answer || "");
    renderReport(payload);
    scrollToLatest();
  } catch (error) {
    response.message.classList.remove("message-pending");
    response.message.classList.add("message-error");
    response.body.textContent =
      error.message || "Could not reach the assistant. Please try again.";
    showReportError(error.message);
  } finally {
    setLoading(false);
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = input.value;
  input.value = "";
  characterCount.textContent = "0 / 4000";
  input.style.height = "auto";
  ask(question);
});

input.addEventListener("input", () => {
  characterCount.textContent = `${input.value.length} / 4000`;
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll(".suggestion").forEach((button) => {
  button.addEventListener("click", () => {
    ask(button.dataset.question || "");
  });
});
