import { useRef, useState } from "react";
import { api } from "../api.js";
import { Card } from "../components/Card.jsx";

const SUGGESTIONS = [
  "Give me an overview of the project",
  "How many issues are in progress?",
  "What is the status breakdown?",
  "Are there any open bugs?",
  "What should I test first?",
  "Who is working on what?",
];

export default function Ask() {
  const [messages, setMessages] = useState([]); // {role: 'user'|'assistant', text, source?}
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const listRef = useRef(null);

  function scrollToBottom() {
    requestAnimationFrame(() => {
      if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
    });
  }

  async function send(question) {
    const q = (question ?? input).trim();
    if (!q || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: q }]);
    setLoading(true);
    scrollToBottom();
    try {
      const res = await api.ask(q);
      setMessages((m) => [...m, {
        role: "assistant", text: res.answer, source: res.source,
        count: res.usedIssueCount, issues: res.issues || [],
      }]);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", text: `Error: ${e.message}`, source: "error" }]);
    } finally {
      setLoading(false);
      scrollToBottom();
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <h1>Ask Wizard</h1>
        <p>Ask questions about your Jira project in plain language.</p>
      </header>

      <Card>
        <div className="chat" ref={listRef}>
          {messages.length === 0 && (
            <div className="chat-empty">
              <p>Start by asking a question, or pick one below.</p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="chip" onClick={() => send(s)} disabled={loading}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`bubble-row ${m.role}`}>
              <div className={`bubble ${m.role} ${m.role === "assistant" && m.issues?.length ? "wide" : ""}`}>
                <pre className="bubble-text">{m.text}</pre>
                {m.role === "assistant" && m.issues?.length > 0 && (
                  <IssueTable issues={m.issues} />
                )}
                {m.role === "assistant" && m.source && (
                  <div className="bubble-meta">
                    source: {m.source}
                    {typeof m.count === "number" ? ` · ${m.count} issues` : ""}
                  </div>
                )}
              </div>
            </div>
          ))}
          {loading && (
            <div className="bubble-row assistant">
              <div className="bubble assistant"><span className="typing">Thinking…</span></div>
            </div>
          )}
        </div>

        <div className="chat-input">
          <textarea
            rows={1}
            value={input}
            placeholder="Ask about statuses, bugs, priorities, what to test…"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="btn" onClick={() => send()} disabled={loading || !input.trim()}>
            {loading ? "…" : "Ask"}
          </button>
        </div>
      </Card>
    </div>
  );
}

function IssueTable({ issues }) {
  return (
    <div className="ask-table-wrap">
      <table className="ask-table">
        <thead>
          <tr>
            <th>Sl No.</th>
            <th>Key</th>
            <th>Summary</th>
            <th>Created</th>
            <th>Priority</th>
            <th>Severity</th>
            <th>Creator</th>
            <th>Assignee</th>
          </tr>
        </thead>
        <tbody>
          {issues.map((it, idx) => (
            <tr key={it.key || idx}>
              <td>{idx + 1}</td>
              <td className="mono">{it.key}</td>
              <td className="ask-summary">{it.summary}</td>
              <td>{it.created || "—"}</td>
              <td><span className={`badge prio-${(it.priority || "medium").toLowerCase()}`}>{it.priority}</span></td>
              <td>{it.severity && it.severity !== "-" ? it.severity : "—"}</td>
              <td>{it.creator && it.creator !== "-" ? it.creator : "—"}</td>
              <td>{it.assignee}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
