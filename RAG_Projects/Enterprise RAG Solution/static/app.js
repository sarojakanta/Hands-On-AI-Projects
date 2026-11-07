/**
 * saroj RAG Demo — single-page chat UI.
 *
 * Chat is the product. Retrieval details (chunks, scores, vector vs keyword contribution) live in a
 * collapsible panel on each answer and are only shown when "Developer view" is on. The sidebar lists
 * past conversations, the model settings and a document table.
 *
 * Everything the server or the model returns is inserted with textContent, never innerHTML, so
 * document or model content can never inject markup.
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const CONV_KEY = 'rag.conversation';
  const DEV_KEY = 'rag.devview';

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function errorMessage(status, body) {
    if (body && body.error && body.error.message) return body.error.message;
    if (body && typeof body.detail === 'string') return body.detail;
    if (body && Array.isArray(body.detail) && body.detail[0]) return body.detail[0].msg || 'The request was not valid.';
    return `Server error ${status}`;
  }

  async function readJson(response) {
    return response.json().catch(() => ({}));
  }

  // ───────────────────────── Developer view toggle ─────────────────────────
  const devToggle = $('dev-toggle');
  let devView = localStorage.getItem(DEV_KEY) === '1';
  devToggle.checked = devView;
  document.body.classList.toggle('dev', devView);
  devToggle.addEventListener('change', () => {
    devView = devToggle.checked;
    localStorage.setItem(DEV_KEY, devView ? '1' : '0');
    document.body.classList.toggle('dev', devView);
  });

  // ───────────────────────── Chat ─────────────────────────
  const log = $('chat-log');
  const input = $('chat-input');
  const sendBtn = $('chat-send');
  const stopBtn = $('chat-stop');
  const newBtn = $('chat-new');
  const deleteBtn = $('chat-delete');
  const statusEl = $('chat-status');
  const emptyEl = $('chat-empty');

  let conversationId = localStorage.getItem(CONV_KEY) || null;
  let controller = null;

  const setStatus = (text) => { statusEl.textContent = text || ''; };
  const scrollToEnd = () => { log.scrollTop = log.scrollHeight; };

  function showEmpty(show) {
    emptyEl.hidden = !show;
    deleteBtn.hidden = show || !conversationId;
  }

  function addUser(text) {
    showEmpty(false);
    const row = el('div', 'msg msg-user');
    row.appendChild(el('div', 'msg-role', 'You'));
    row.appendChild(el('div', 'msg-text', text));
    log.appendChild(row);
    scrollToEnd();
  }

  function addAssistant() {
    showEmpty(false);
    const row = el('div', 'msg msg-assistant');
    row.appendChild(el('div', 'msg-role', 'Assistant'));
    const body = el('div', 'msg-text');
    const extra = el('div', 'msg-extra');
    row.append(body, extra);
    log.appendChild(row);
    scrollToEnd();
    return { row, body, extra };
  }

  /** Answer text; "[1]" markers become buttons that open and highlight the matching source. */
  function renderAnswerText(container, text, prefix) {
    container.textContent = '';
    for (const part of (text || '').split(/(\[\d+\])/g)) {
      const m = /^\[(\d+)\]$/.exec(part);
      if (m) {
        const chip = el('button', 'cite-chip', part);
        chip.type = 'button';
        chip.setAttribute('aria-label', `Show source ${m[1]}`);
        chip.addEventListener('click', () => {
          const sources = document.getElementById(`${prefix}-sources`);
          if (sources && !sources.open) sources.open = true;
          const target = document.getElementById(`${prefix}-${m[1]}`);
          if (!target) return;
          target.open = true;
          target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          target.classList.add('flash');
          setTimeout(() => target.classList.remove('flash'), 1500);
        });
        container.appendChild(chip);
      } else if (part) {
        container.appendChild(document.createTextNode(part));
      }
    }
  }

  /** Collapsible "Sources" panel: cited passages first, then (in dev view) all retrieved passages. */
  function renderSources(extra, data, prefix) {
    const cited = data.citations || [];
    const retrieved = data.retrieved || [];
    if (!cited.length && !(devView && retrieved.length)) return;

    const panel = el('details', 'sources');
    panel.id = `${prefix}-sources`;
    panel.open = cited.length > 0 && !devView ? false : false;
    const summary = el('summary');
    const count = cited.length || retrieved.length;
    summary.appendChild(el('span', 'sources-title', `Sources (${count})`));
    panel.appendChild(summary);

    for (const c of cited) {
      panel.appendChild(sourceCard(c, prefix, true));
    }

    if (devView) {
      const extraHits = retrieved.filter((r) => !cited.some((c) => c.number === r.number));
      if (extraHits.length) {
        panel.appendChild(el('div', 'sources-sub', 'Also retrieved (not cited)'));
        for (const r of extraHits) panel.appendChild(sourceCard(r, prefix, false));
      }
    }
    extra.appendChild(panel);
  }

  function sourceCard(c, prefix, cited) {
    const d = el('details', 'source-card' + (cited ? '' : ' uncited'));
    if (c.number != null) d.id = `${prefix}-${c.number}`;
    const s = el('summary');
    if (c.number != null) s.appendChild(el('span', 'source-num', `[${c.number}]`));
    s.appendChild(el('span', 'source-doc', c.document || ''));
    const bits = [];
    if (c.version != null) bits.push(`v${c.version}`);
    if (c.page != null) bits.push(`page ${c.page}`);
    if (c.score != null) bits.push(`${(c.score * 100).toFixed(0)}% match`);
    if (devView) {
      if (c.lexical_coverage != null) bits.push(`kw ${(c.lexical_coverage * 100).toFixed(0)}%`);
      if (c.rerank_score != null) bits.push(`rerank ${(c.rerank_score * 100).toFixed(0)}%`);
      if (c.chunk_id) bits.push(c.chunk_id);
    }
    s.appendChild(el('span', 'source-meta', bits.join(' · ')));
    d.appendChild(s);
    d.appendChild(el('p', 'source-excerpt', c.excerpt || c.text || ''));
    return d;
  }

  function renderMeta(extra, data) {
    const bits = [];
    if (data.provider) bits.push(`${data.provider}${data.model ? ' / ' + data.model : ''}`);
    if (data.timings_ms && data.timings_ms.total != null) {
      const ms = data.timings_ms.total;
      bits.push(ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`);
    }
    if (devView && data.usage && data.usage.total_tokens != null) bits.push(`${data.usage.total_tokens} tokens`);
    if (bits.length) extra.appendChild(el('div', 'msg-meta', bits.join(' · ')));

    if (data.used_fallback) {
      extra.appendChild(el('div', 'msg-note warn',
        `Answered by the fallback provider (${data.provider}) because ${data.fallback_from || 'the first provider'} failed: ${data.fallback_reason || 'unknown reason'}.`));
    }
    const warnings = {
      query_rewrite_failed: 'The follow-up question could not be rewritten; the original wording was used.',
      rerank_failed: 'The reranker was unavailable; results are in their original order.',
      context_truncated: 'Some retrieved text did not fit and was cut.',
    };
    for (const w of data.warnings || []) extra.appendChild(el('div', 'msg-note', warnings[w] || w));
    if (data.found_in_documents === false) {
      extra.appendChild(el('div', 'msg-note', 'No passage in the documents was relevant enough, so the model was not asked.'));
    }
  }

  function setBusy(busy) {
    sendBtn.hidden = busy;
    stopBtn.hidden = !busy;
    input.disabled = busy;
    newBtn.disabled = busy;
    log.setAttribute('aria-busy', String(busy));
    if (!busy) input.focus();
  }

  async function readEvents(response, onEvent) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        let name = 'message';
        const data = [];
        for (const line of raw.split('\n')) {
          if (line.startsWith('event:')) name = line.slice(6).trim();
          else if (line.startsWith('data:')) data.push(line.slice(5).trim());
        }
        if (data.length) {
          try { onEvent(name, JSON.parse(data.join('\n'))); } catch { /* skip malformed event */ }
        }
      }
    }
  }

  async function ask(question) {
    question = (question || '').trim();
    if (!question || controller) return;
    addUser(question);
    const out = addAssistant();
    const prefix = `src-${Date.now()}`;
    setStatus('Searching the documents…');
    setBusy(true);
    controller = new AbortController();
    let streamed = '';
    let finished = false;

    try {
      const response = await fetch('/api/v1/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ question, conversation_id: conversationId, stream: true }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const body = await readJson(response);
        if (response.status === 404 && conversationId) {
          await startNewConversation(false);
          throw new Error('That conversation was no longer available. Ask again to start a new one.');
        }
        throw new Error(errorMessage(response.status, body));
      }

      conversationId = response.headers.get('X-Conversation-Id') || conversationId;
      if (conversationId) localStorage.setItem(CONV_KEY, conversationId);
      deleteBtn.hidden = false;

      await readEvents(response, (name, data) => {
        if (name === 'retrieval') {
          setStatus(data.found_context ? 'Writing the answer…' : 'Nothing relevant found.');
        } else if (name === 'token') {
          streamed += data.delta;
          out.body.textContent = streamed;
          scrollToEnd();
        } else if (name === 'done') {
          finished = true;
          renderAnswerText(out.body, data.answer, prefix);
          renderSources(out.extra, data, prefix);
          renderMeta(out.extra, data);
          if (data.conversation_id) {
            conversationId = data.conversation_id;
            localStorage.setItem(CONV_KEY, conversationId);
          }
          loadConversations();
        } else if (name === 'error') {
          finished = true;
          out.body.textContent = streamed;
          out.extra.appendChild(el('div', 'msg-note error', `Error: ${data.message || 'The answer could not be completed.'}`));
        }
      });
      if (!finished) out.extra.appendChild(el('div', 'msg-note error', 'The connection ended before the answer was complete.'));
    } catch (err) {
      if (err.name === 'AbortError') {
        out.body.textContent = streamed;
        out.extra.appendChild(el('div', 'msg-note', 'Stopped. This turn was not saved to the conversation.'));
      } else {
        out.row.classList.add('msg-failed');
        out.body.textContent = streamed;
        out.extra.appendChild(el('div', 'msg-note error', `Error: ${err.message}`));
      }
    } finally {
      controller = null;
      setStatus('');
      setBusy(false);
      scrollToEnd();
    }
  }

  function clearLog() {
    log.querySelectorAll('.msg').forEach((n) => n.remove());
  }

  async function startNewConversation(callServer) {
    conversationId = null;
    localStorage.removeItem(CONV_KEY);
    clearLog();
    showEmpty(true);
    input.focus();
    if (callServer) loadConversations();
  }

  async function openConversation(id) {
    if (controller) return;
    try {
      const response = await fetch(`/api/v1/conversations/${encodeURIComponent(id)}`);
      if (!response.ok) { await startNewConversation(false); loadConversations(); return; }
      const data = await response.json();
      conversationId = id;
      localStorage.setItem(CONV_KEY, id);
      clearLog();
      showEmpty(false);
      deleteBtn.hidden = false;
      let n = 0;
      for (const m of data.messages) {
        if (m.role === 'user') {
          addUser(m.content);
        } else {
          const out = addAssistant();
          const prefix = `hist-${n++}`;
          renderAnswerText(out.body, m.content, prefix);
          renderSources(out.extra, { citations: m.citations || [], retrieved: [], found_in_documents: m.found_in_documents }, prefix);
        }
      }
      markActiveConversation();
      scrollToEnd();
    } catch { /* offline */ }
  }

  async function deleteConversation(id) {
    try { await fetch(`/api/v1/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' }); } catch { /* ignore */ }
    if (id === conversationId) await startNewConversation(false);
    loadConversations();
  }

  $('chat-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value;
    input.value = '';
    ask(text);
  });
  stopBtn.addEventListener('click', () => controller && controller.abort());
  newBtn.addEventListener('click', () => startNewConversation(true));
  deleteBtn.addEventListener('click', () => {
    if (conversationId && confirm('Delete this conversation? This cannot be undone.')) deleteConversation(conversationId);
  });
  $('suggestions').addEventListener('click', (e) => {
    const btn = e.target.closest('.chip-btn');
    if (btn) ask(btn.dataset.q);
  });

  // ───────────────────────── Conversation list ─────────────────────────
  const convList = $('conversation-list');
  const convEmpty = $('conversation-empty');

  function markActiveConversation() {
    convList.querySelectorAll('.conv-item').forEach((n) => {
      n.classList.toggle('active', n.dataset.id === conversationId);
    });
  }

  async function loadConversations() {
    try {
      const response = await fetch('/api/v1/conversations?limit=100');
      if (!response.ok) return;
      const data = await response.json();
      convList.textContent = '';
      convEmpty.hidden = data.conversations.length > 0;
      for (const c of data.conversations) {
        const li = el('li', 'conv-item');
        li.dataset.id = c.id;
        const open = el('button', 'conv-open', c.title);
        open.type = 'button';
        open.title = c.title;
        open.addEventListener('click', () => openConversation(c.id));
        const count = el('span', 'conv-count', String(c.message_count));
        const del = el('button', 'conv-del', '×');
        del.type = 'button';
        del.title = 'Delete conversation';
        del.setAttribute('aria-label', `Delete conversation: ${c.title}`);
        del.addEventListener('click', (e) => { e.stopPropagation(); deleteConversation(c.id); });
        const meta = el('div', 'conv-meta');
        meta.append(count, del);
        li.append(open, meta);
        convList.appendChild(li);
      }
      markActiveConversation();
    } catch { /* offline */ }
  }

  // ───────────────────────── Model picker ─────────────────────────
  const providerSel = $('model-provider');
  const modelInput = $('model-name');
  const modelList = $('model-options');
  const modelStatus = $('model-status');
  const modelSave = $('model-save');
  const modelTest = $('model-test');

  const modelMsg = (text, kind) => { modelStatus.textContent = text; modelStatus.className = `model-status ${kind || ''}`; };

  async function loadModels(name) {
    modelList.textContent = '';
    if (!name) return;
    try {
      const response = await fetch(`/api/v1/llm/providers/${encodeURIComponent(name)}/models`);
      if (!response.ok) return;
      const data = await response.json();
      for (const m of data.models) {
        const opt = document.createElement('option');
        opt.value = m.id;
        modelList.appendChild(opt);
      }
    } catch { /* the name can still be typed */ }
  }

  async function loadProviders() {
    try {
      const response = await fetch('/api/v1/llm/providers');
      const data = await response.json();
      providerSel.textContent = '';
      for (const p of data.providers) {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = p.configured ? p.name : `${p.name} (no API key)`;
        opt.disabled = !p.configured;
        providerSel.appendChild(opt);
      }
      providerSel.value = data.active.provider;
      modelInput.value = data.active.model || '';
      modelMsg(`Active: ${data.active.provider} / ${data.active.model || 'default'}`, '');
      loadModels(data.active.provider);
    } catch {
      modelMsg('Could not load the model settings.', 'error');
    }
  }

  providerSel.addEventListener('change', () => { modelInput.value = ''; loadModels(providerSel.value); });

  modelSave.addEventListener('click', async () => {
    modelSave.disabled = true;
    modelMsg('Checking the connection…', '');
    try {
      const response = await fetch('/api/v1/llm/config?validate=true', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: providerSel.value, model: modelInput.value.trim() }),
      });
      const body = await readJson(response);
      if (!response.ok) throw new Error(errorMessage(response.status, body));
      modelMsg(`Saved: ${body.provider} / ${body.model || 'default'}`, 'ok');
    } catch (err) {
      modelMsg(err.message, 'error');
    } finally {
      modelSave.disabled = false;
    }
  });

  modelTest.addEventListener('click', async () => {
    modelTest.disabled = true;
    modelMsg('Testing…', '');
    try {
      const response = await fetch(`/api/v1/llm/providers/${encodeURIComponent(providerSel.value)}/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelInput.value.trim() || null }),
      });
      const body = await readJson(response);
      if (!response.ok) throw new Error(errorMessage(response.status, body));
      modelMsg(body.ok ? 'Connection works.' : `Failed: ${body.message || body.error || 'not reachable'}`, body.ok ? 'ok' : 'error');
    } catch (err) {
      modelMsg(err.message, 'error');
    } finally {
      modelTest.disabled = false;
    }
  });

  // ───────────────────────── Documents table ─────────────────────────
  const docStatus = $('doc-status');
  const docRows = $('doc-rows');
  const uploadStatus = $('upload-status');

  function uploadMsg(text, kind) {
    uploadStatus.hidden = !text;
    uploadStatus.textContent = text || '';
    uploadStatus.className = `upload-status ${kind || ''}`;
  }

  function statusLabel(doc) {
    if (doc.status === 'processing') return { text: 'indexing', kind: 'pill-warn' };
    if (doc.status === 'failed') return { text: 'failed', kind: 'pill-error' };
    if (doc.stale) return { text: 'stale', kind: 'pill-warn' };
    return { text: 'ready', kind: 'pill-ok' };
  }

  async function loadDocuments() {
    try {
      const response = await fetch('/api/documents');
      const data = await response.json();
      if (data.status === 'indexing') {
        docStatus.className = 'status-badge loading';
        docStatus.textContent = 'Indexing…';
        setTimeout(loadDocuments, 2000);
        return;
      }
      docStatus.className = 'status-badge ready';
      docStatus.textContent = `${data.documents.length} doc(s)`;
      docRows.textContent = '';
      for (const doc of data.documents) {
        docRows.appendChild(buildDocRow(doc));
      }
    } catch {
      docStatus.className = 'status-badge error';
      docStatus.textContent = 'Unreachable';
    }
  }

  function buildDocRow(doc) {
    const tr = el('tr', 'doc-row');
    const name = el('td', 'doc-cell-name');
    name.appendChild(el('span', 'doc-name', doc.filename));
    name.title = doc.filename;
    tr.appendChild(name);
    tr.appendChild(el('td', 'doc-cell-ver', doc.version != null ? `v${doc.version}` : '—'));
    tr.appendChild(el('td', 'doc-cell-chunks', String(doc.chunks)));

    const statusCell = el('td', 'doc-cell-status');
    const s = statusLabel(doc);
    statusCell.appendChild(el('span', `pill ${s.kind}`, s.text));
    tr.appendChild(statusCell);

    const actions = el('td', 'doc-cell-actions');
    if (doc.id) {
      const info = el('button', 'tiny-btn', 'Details');
      info.type = 'button';
      info.setAttribute('aria-expanded', 'false');
      const reindex = el('button', 'tiny-btn', 'Re-index');
      reindex.type = 'button';
      const del = el('button', 'tiny-btn danger', 'Delete');
      del.type = 'button';

      info.addEventListener('click', () => toggleDetails(tr, doc, info));
      reindex.addEventListener('click', () => runDocAction(reindex, `Re-indexing ${doc.filename}…`, () => docAction(`/api/v1/documents/${doc.id}/reindex`, 'POST')));
      del.addEventListener('click', () => {
        if (!confirm(`Delete ${doc.filename} and all its versions? This cannot be undone.`)) return;
        runDocAction(del, `Deleting ${doc.filename}…`, () => docAction(`/api/v1/documents/${doc.id}`, 'DELETE'));
      });
      actions.append(info, reindex, del);
    }
    tr.appendChild(actions);
    return tr;
  }

  async function toggleDetails(row, doc, button) {
    const existing = row.nextElementSibling;
    if (existing && existing.classList.contains('doc-detail-row')) {
      existing.remove();
      button.setAttribute('aria-expanded', 'false');
      return;
    }
    button.setAttribute('aria-expanded', 'true');
    const detailRow = el('tr', 'doc-detail-row');
    const cell = el('td');
    cell.colSpan = 5;
    cell.textContent = 'Loading versions…';
    detailRow.appendChild(cell);
    row.after(detailRow);
    try {
      const response = await fetch(`/api/v1/documents/${encodeURIComponent(doc.id)}/versions`);
      const data = await response.json();
      if (!response.ok) throw new Error(errorMessage(response.status, data));
      cell.textContent = '';
      const tbl = el('table', 'ver-table');
      const head = el('tr');
      ['Ver', 'Status', 'Chunks', 'Strategy', 'Reason', 'Size'].forEach((h) => head.appendChild(el('th', null, h)));
      tbl.appendChild(head);
      for (const v of data.versions) {
        const vr = el('tr', v.is_active ? 'ver-active' : '');
        vr.appendChild(el('td', null, `v${v.version_number}${v.is_active ? ' ●' : ''}`));
        vr.appendChild(el('td', null, v.status));
        vr.appendChild(el('td', null, String(v.chunk_count)));
        vr.appendChild(el('td', null, v.chunk_strategy || 'sentence'));
        vr.appendChild(el('td', null, v.reason));
        vr.appendChild(el('td', null, `${Math.max(1, Math.round((v.byte_size || 0) / 1024))} KB`));
        tbl.appendChild(vr);
      }
      cell.appendChild(tbl);
    } catch (err) {
      cell.textContent = err.message;
    }
  }

  async function docAction(url, method) {
    const response = await fetch(url, { method });
    const body = await readJson(response);
    if (!response.ok) throw new Error(errorMessage(response.status, body));
  }

  async function runDocAction(button, label, fn) {
    button.disabled = true;
    uploadMsg(`⏳ ${label}`, 'loading');
    try {
      await fn();
      uploadMsg('✓ Done.', 'success');
      loadDocuments();
    } catch (err) {
      uploadMsg(`✗ ${err.message}`, 'error');
      button.disabled = false;
    }
  }

  // ───────────────────────── Upload (with a real progress bar) ─────────────────────────
  const fileInput = $('file-input');
  const fileLabel = $('file-label');
  const fileLabelTxt = $('file-label-text');
  const uploadBtn = $('upload-btn');
  const uploadProgress = $('upload-progress');
  const uploadBar = $('upload-bar');

  const DEFAULT_LABEL = '1. Select a file…';

  function resetUpload(keepStatus) {
    fileInput.value = '';
    fileLabelTxt.textContent = DEFAULT_LABEL;
    fileLabel.classList.remove('has-file');
    uploadBtn.disabled = true;
    uploadProgress.hidden = true;
    uploadBar.style.width = '0%';
    if (!keepStatus) uploadMsg('', '');
  }

  fileInput.addEventListener('change', () => {
    const file = fileInput.files[0];
    if (file) {
      fileLabelTxt.textContent = file.name;
      fileLabel.classList.add('has-file');
      uploadBtn.disabled = false;
      uploadMsg('', '');
    } else {
      resetUpload(false);
    }
  });

  uploadBtn.addEventListener('click', () => {
    const file = fileInput.files[0];
    if (!file) return;
    uploadBtn.disabled = true;
    uploadProgress.hidden = false;
    uploadBar.style.width = '0%';
    uploadMsg('⏳ Uploading…', 'loading');

    // XHR is used (not fetch) so the upload progress bar can reflect real bytes sent.
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/v1/documents');
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 100);
        uploadBar.style.width = `${pct}%`;
        if (pct >= 100) uploadMsg('⏳ Indexing…', 'loading');
      }
    });
    xhr.addEventListener('load', () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        const outcome = (body.outcome || 'created');
        const name = (body.document && body.document.name) || file.name;
        const label = { created: 'uploaded and indexed', updated: `updated to v${body.version && body.version.version_number}`, reindexed: 're-indexed', unchanged: 'already up to date' }[outcome] || 'done';
        uploadMsg(`✓ ${name}: ${label}.`, 'success');
        resetUpload(true);
        loadDocuments();
      } else {
        uploadMsg(`✗ ${errorMessage(xhr.status, body)}`, 'error');
        uploadBtn.disabled = false;
        uploadProgress.hidden = true;
      }
    });
    xhr.addEventListener('error', () => {
      uploadMsg('✗ Upload failed (network error).', 'error');
      uploadBtn.disabled = false;
      uploadProgress.hidden = true;
    });
    const form = new FormData();
    form.append('file', file);
    xhr.send(form);
  });

  // ───────────────────────── Boot ─────────────────────────
  showEmpty(!conversationId);
  loadProviders();
  loadDocuments();
  loadConversations();
  if (conversationId) openConversation(conversationId);
})();
