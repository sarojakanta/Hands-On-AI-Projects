/*
 * BizAssist embeddable chat widget.
 *
 * Embed on any website with:
 *   <script src="https://YOUR_HOST/static/js/widget.js"
 *           data-api="https://YOUR_HOST"
 *           data-institute-key="demo-sgen-academy-key"
 *           data-title="SGEN Computer Academy"
 *           data-accent="#2563eb"></script>
 *
 * Only the PUBLIC institute key is ever used client-side. No admin
 * credentials or model API keys are exposed here.
 */
(function () {
  "use strict";

  // --- Resolve config from the script tag -------------------------------
  const script =
    document.currentScript ||
    document.querySelector('script[src*="widget.js"]');
  const cfg = {
    api: (script && script.getAttribute("data-api")) || window.location.origin,
    instituteKey: (script && script.getAttribute("data-institute-key")) || "",
    title: (script && script.getAttribute("data-title")) || "Admissions Assistant",
    subtitle:
      (script && script.getAttribute("data-subtitle")) ||
      "AI assistant · a human is available",
    accent: (script && script.getAttribute("data-accent")) || "#2563eb",
    greeting:
      (script && script.getAttribute("data-greeting")) ||
      "Hi! I can help with courses, fees, batches, eligibility and admissions. How can I help you today?",
  };

  const STORAGE_KEY = "bz_session_" + (cfg.instituteKey || "default");

  // --- Helpers ----------------------------------------------------------
  const el = (tag, cls, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  };
  const escapeHtml = (s) =>
    (s ?? "").toString().replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

  // --- Build DOM --------------------------------------------------------
  const root = el("div", "bz-widget");
  root.style.setProperty("--bz-accent", cfg.accent);

  const launcher = el("button", "bz-launcher", "&#128172;");
  launcher.setAttribute("aria-label", "Open chat");

  const panel = el("div", "bz-panel");
  panel.innerHTML = `
    <div class="bz-header">
      <div>
        <div class="bz-header-title">${escapeHtml(cfg.title)}</div>
        <div class="bz-header-sub">${escapeHtml(cfg.subtitle)}</div>
      </div>
      <div class="bz-header-actions">
        <button class="bz-newchat" id="bz-newchat" aria-label="Start new chat" title="Start new chat">&#8635;</button>
        <button class="bz-close" aria-label="Close chat">&times;</button>
      </div>
    </div>
    <div class="bz-messages" id="bz-messages"></div>
    <div class="bz-footer">
      <div class="bz-input-row">
        <textarea class="bz-input" id="bz-input" rows="1" placeholder="Type your message…"></textarea>
        <button class="bz-send" id="bz-send" aria-label="Send">&#10148;</button>
      </div>
      <div class="bz-actions">
        <button class="bz-human-btn" id="bz-human">Talk to a human counsellor</button>
      </div>
    </div>`;

  root.appendChild(panel);
  root.appendChild(launcher);
  document.body.appendChild(root);

  const messagesEl = panel.querySelector("#bz-messages");
  const inputEl = panel.querySelector("#bz-input");
  const sendBtn = panel.querySelector("#bz-send");
  const humanBtn = panel.querySelector("#bz-human");
  const closeBtn = panel.querySelector(".bz-close");
  const newChatBtn = panel.querySelector("#bz-newchat");

  let sessionId = localStorage.getItem(STORAGE_KEY) || null;
  let busy = false;
  let loaded = false; // transcript has been rendered/restored at least once

  // --- Rendering --------------------------------------------------------
  function addMessage(role, text, sources) {
    const wrap = el("div", "bz-msg " + (role === "user" ? "bz-msg-user" : "bz-msg-bot"));
    const bubble = el("div", "bz-bubble");
    bubble.textContent = text;
    wrap.appendChild(bubble);
    if (sources && sources.length) {
      const src = el(
        "div",
        "bz-sources",
        "Sources: " + sources.map((s) => escapeHtml(s.title)).join(", ")
      );
      bubble.appendChild(src);
    }
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function showTyping() {
    const wrap = el("div", "bz-msg bz-msg-bot");
    wrap.id = "bz-typing";
    wrap.innerHTML = '<div class="bz-bubble"><div class="bz-typing"><span></span><span></span><span></span></div></div>';
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
  function hideTyping() {
    const t = document.getElementById("bz-typing");
    if (t) t.remove();
  }

  function showEscalatedNote() {
    if (document.getElementById("bz-esc-note")) return;
    const note = el(
      "div",
      "bz-escalated-note",
      "A human counsellor has been notified and will follow up with you."
    );
    note.id = "bz-esc-note";
    messagesEl.appendChild(note);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function clearMessages() {
    messagesEl.innerHTML = "";
  }

  // Restore a previous conversation (same browser, stored session_id) so the
  // student still sees their history after closing the bubble or refreshing.
  // Returns true if any prior messages were rendered.
  async function restoreHistory() {
    if (!sessionId) return false;
    try {
      const headers = {};
      if (cfg.instituteKey) headers["X-Institute-Key"] = cfg.instituteKey;
      const res = await fetch(
        cfg.api.replace(/\/$/, "") +
          "/api/conversations/" +
          encodeURIComponent(sessionId),
        { headers }
      );
      if (!res.ok) {
        // Unknown/expired session on the server — start fresh.
        if (res.status === 404) localStorage.removeItem(STORAGE_KEY);
        sessionId = res.status === 404 ? null : sessionId;
        return false;
      }
      const data = await res.json();
      const msgs = (data.messages || []).filter((m) => m.role !== "system");
      if (!msgs.length) return false;
      clearMessages();
      msgs.forEach((m) => addMessage(m.role === "user" ? "user" : "bot", m.content));
      if (data.status === "escalated") showEscalatedNote();
      return true;
    } catch (_err) {
      return false; // network issue -> fall back to greeting
    }
  }

  // --- Networking -------------------------------------------------------
  async function send(text) {
    if (busy || !text.trim()) return;
    busy = true;
    sendBtn.disabled = true;
    addMessage("user", text);
    inputEl.value = "";
    inputEl.style.height = "auto";
    showTyping();

    try {
      const headers = { "Content-Type": "application/json" };
      if (cfg.instituteKey) headers["X-Institute-Key"] = cfg.instituteKey;
      const res = await fetch(cfg.api.replace(/\/$/, "") + "/api/chat", {
        method: "POST",
        headers,
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });
      hideTyping();
      if (!res.ok) throw new Error("request failed (" + res.status + ")");
      const data = await res.json();
      sessionId = data.session_id;
      localStorage.setItem(STORAGE_KEY, sessionId);
      addMessage("bot", data.reply, data.sources);
      if (data.escalated) showEscalatedNote();
    } catch (err) {
      hideTyping();
      addMessage(
        "bot",
        "Sorry, I'm having trouble connecting right now. Please try again, or contact the institute directly."
      );
    } finally {
      busy = false;
      sendBtn.disabled = false;
      inputEl.focus();
    }
  }

  // --- Events -----------------------------------------------------------
  async function openPanel() {
    panel.classList.add("bz-open");
    if (!loaded) {
      // Try to restore a prior conversation; otherwise greet.
      const restored = await restoreHistory();
      if (!restored) {
        clearMessages();
        addMessage("bot", cfg.greeting);
      }
      loaded = true;
    }
    inputEl.focus();
  }
  function closePanel() {
    panel.classList.remove("bz-open");
  }

  function startNewChat() {
    // Forget the current session and begin a fresh one.
    sessionId = null;
    localStorage.removeItem(STORAGE_KEY);
    clearMessages();
    addMessage("bot", cfg.greeting);
    loaded = true;
    inputEl.focus();
  }

  launcher.addEventListener("click", () =>
    panel.classList.contains("bz-open") ? closePanel() : openPanel()
  );
  closeBtn.addEventListener("click", closePanel);
  newChatBtn.addEventListener("click", startNewChat);

  sendBtn.addEventListener("click", () => send(inputEl.value));
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(inputEl.value);
    }
  });
  inputEl.addEventListener("input", () => {
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(inputEl.scrollHeight, 80) + "px";
  });
  humanBtn.addEventListener("click", () =>
    send("I would like to talk to a human counsellor.")
  );
})();
