// BizAssist admin dashboard. Auth is via the HTTP-only cookie set at login;
// fetch sends it automatically (same-origin).

const api = async (url, opts = {}) => {
  const res = await fetch(url, { credentials: "same-origin", ...opts });
  if (res.status === 401) {
    window.location.href = "/admin/login";
    throw new Error("unauthorized");
  }
  return res;
};

const esc = (s) =>
  (s ?? "").toString().replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

const fmtDate = (d) => (d ? new Date(d).toLocaleString() : "—");

// --- Stats + overview -----------------------------------------------------
async function loadStats() {
  const res = await api("/api/admin/stats");
  const s = await res.json();
  document.getElementById("s-total").textContent = s.total_enquiries;
  document.getElementById("s-new").textContent = s.new_leads;
  document.getElementById("s-follow").textContent = s.pending_followups;
  document.getElementById("s-esc").textContent = s.open_escalations;
  document.getElementById("s-conv").textContent = s.total_conversations;

  const cw = s.course_wise || [];
  const el = document.getElementById("courseWise");
  if (!cw.length) {
    el.innerHTML = '<span class="text-muted">No enquiries yet.</span>';
    return;
  }
  const max = Math.max(...cw.map((r) => r.count));
  el.innerHTML = cw
    .map(
      (r) => `
      <div class="mb-2">
        <div class="d-flex justify-content-between"><span>${esc(r.course)}</span><span class="fw-semibold">${r.count}</span></div>
        <div class="progress" style="height:6px;"><div class="progress-bar" style="width:${(r.count / max) * 100}%"></div></div>
      </div>`
    )
    .join("");
}

// --- Leads ----------------------------------------------------------------
const STATUSES = ["new", "contacted", "enrolled", "closed"];
async function loadLeads() {
  const res = await api("/api/admin/leads");
  const leads = await res.json();
  const body = document.getElementById("leadsBody");
  if (!leads.length) {
    body.innerHTML = '<tr><td colspan="6" class="text-muted">No leads yet.</td></tr>';
    return;
  }
  body.innerHTML = leads
    .map((l) => {
      const opts = STATUSES.map(
        (s) => `<option value="${s}" ${s === l.status ? "selected" : ""}>${s}</option>`
      ).join("");
      return `<tr>
        <td>${esc(l.name)}</td>
        <td>${esc(l.phone)}</td>
        <td>${esc(l.email || "—")}</td>
        <td>${esc(l.course_interest || "—")}</td>
        <td><select class="form-select form-select-sm lead-status" data-id="${l.id}" style="width:120px">${opts}</select></td>
        <td class="small text-muted">${fmtDate(l.created_at)}</td>
      </tr>`;
    })
    .join("");
  body.querySelectorAll(".lead-status").forEach((sel) => {
    sel.addEventListener("change", async (e) => {
      const id = e.target.dataset.id;
      await api(`/api/admin/leads/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: e.target.value }),
      });
      loadStats();
    });
  });
}

// --- Conversations --------------------------------------------------------
async function loadConversations() {
  const res = await api("/api/admin/conversations");
  const convos = await res.json();
  const list = document.getElementById("convoList");
  if (!convos.length) {
    list.innerHTML = '<span class="text-muted">No conversations yet.</span>';
    return;
  }
  list.innerHTML = convos
    .map(
      (c) => `
      <a class="list-group-item list-group-item-action convo-item" data-sid="${esc(c.session_id)}">
        <div class="d-flex justify-content-between">
          <span>#${c.id} <span class="badge bg-secondary">${esc(c.status)}</span></span>
          <span class="text-muted">${c.message_count} msgs</span>
        </div>
        <div class="text-muted">${fmtDate(c.created_at)}</div>
      </a>`
    )
    .join("");
  list.querySelectorAll(".convo-item").forEach((item) => {
    item.addEventListener("click", async () => {
      list.querySelectorAll(".convo-item").forEach((i) => i.classList.remove("active"));
      item.classList.add("active");
      await loadTranscript(item.dataset.sid);
    });
  });
}

async function loadTranscript(sid) {
  const res = await api(`/api/admin/conversations/${encodeURIComponent(sid)}`);
  const convo = await res.json();
  const el = document.getElementById("convoDetail");
  el.innerHTML = convo.messages
    .map(
      (m) =>
        `<div class="transcript-msg ${m.role === "user" ? "transcript-user" : "transcript-assistant"}">
          <div class="fw-semibold text-capitalize small">${esc(m.role)}</div>
          <div>${esc(m.content)}</div>
        </div>`
    )
    .join("");
}

// --- Courses --------------------------------------------------------------
async function loadCourses() {
  const res = await api("/api/admin/courses");
  const courses = await res.json();
  const body = document.getElementById("coursesBody");
  body.innerHTML =
    courses
      .map(
        (c) => `<tr>
        <td>${esc(c.name)}</td>
        <td>₹${Number(c.fee).toLocaleString()}</td>
        <td>${esc(c.duration || "—")}</td>
        <td>${esc(c.mode || "—")}</td>
        <td>${c.is_active ? "Yes" : "No"}</td>
        <td><button class="btn btn-sm btn-outline-danger del-course" data-id="${c.id}">Delete</button></td>
      </tr>`
      )
      .join("") || '<tr><td colspan="6" class="text-muted">No courses.</td></tr>';
  body.querySelectorAll(".del-course").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this course?")) return;
      await api(`/api/admin/courses/${btn.dataset.id}`, { method: "DELETE" });
      loadCourses();
    });
  });
}

document.getElementById("courseForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = {
    name: document.getElementById("c-name").value.trim(),
    fee: parseFloat(document.getElementById("c-fee").value || "0"),
    duration: document.getElementById("c-duration").value.trim() || null,
    mode: document.getElementById("c-mode").value.trim() || null,
  };
  await api("/api/admin/courses", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  e.target.reset();
  loadCourses();
});

// --- Documents ------------------------------------------------------------
async function loadDocuments() {
  const res = await api("/api/admin/documents");
  const docs = await res.json();
  const body = document.getElementById("docsBody");
  body.innerHTML =
    docs
      .map((d) => {
        const badge =
          d.status === "indexed" ? "success" : d.status === "failed" ? "danger" : "secondary";
        return `<tr>
          <td>${esc(d.filename)}</td>
          <td>${esc(d.title || "—")}</td>
          <td><span class="badge bg-${badge}">${esc(d.status)}</span></td>
          <td>${d.chunk_count}</td>
          <td><button class="btn btn-sm btn-outline-danger del-doc" data-id="${d.id}">Delete</button></td>
        </tr>`;
      })
      .join("") || '<tr><td colspan="5" class="text-muted">No documents.</td></tr>';
  body.querySelectorAll(".del-doc").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this document and its index?")) return;
      await api(`/api/admin/documents/${btn.dataset.id}`, { method: "DELETE" });
      loadDocuments();
    });
  });
}

document.getElementById("docForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fileInput = document.getElementById("d-file");
  const msg = document.getElementById("docMsg");
  const btn = document.getElementById("d-btn");
  if (!fileInput.files.length) return;
  const fd = new FormData();
  fd.append("file", fileInput.files[0]);
  const title = document.getElementById("d-title").value.trim();
  if (title) fd.append("title", title);
  btn.disabled = true;
  msg.innerHTML = '<span class="text-muted">Uploading &amp; indexing…</span>';
  try {
    const res = await api("/api/admin/documents", { method: "POST", body: fd });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Upload failed");
    }
    const data = await res.json();
    msg.innerHTML = `<span class="text-success">Indexed ${esc(data.filename)} (${data.chunks} chunks).</span>`;
    e.target.reset();
    loadDocuments();
  } catch (err) {
    msg.innerHTML = `<span class="text-danger">${esc(err.message)}</span>`;
  } finally {
    btn.disabled = false;
  }
});

// --- Embed snippet --------------------------------------------------------
let embedSnippetText = "";
async function loadEmbed() {
  const res = await api("/api/admin/institute");
  const info = await res.json();
  const base = (info.base_url || window.location.origin + "/").replace(/\/+$/, "/");
  embedSnippetText =
    `<script src="${base}static/js/widget.js"\n` +
    `  data-api="${base}"\n` +
    `  data-institute-key="${info.api_key}"\n` +
    `  data-title="${info.name}"\n` +
    `  data-accent="#2563eb"><\/script>`;
  document.getElementById("embedSnippet").textContent = embedSnippetText;
}

document.getElementById("copyEmbed").addEventListener("click", async () => {
  const note = document.getElementById("embedCopied");
  try {
    await navigator.clipboard.writeText(embedSnippetText);
    note.textContent = "Copied to clipboard.";
  } catch {
    note.textContent = "Press Ctrl+C to copy the selected text.";
  }
  setTimeout(() => (note.textContent = ""), 2500);
});

// --- Logout + init --------------------------------------------------------
document.getElementById("logoutBtn").addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  window.location.href = "/admin/login";
});

(async function init() {
  await Promise.all([
    loadStats(),
    loadLeads(),
    loadConversations(),
    loadCourses(),
    loadDocuments(),
    loadEmbed(),
  ]);
})();
