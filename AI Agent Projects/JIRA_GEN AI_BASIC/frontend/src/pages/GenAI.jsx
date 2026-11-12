import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Card } from "../components/Card.jsx";
import { ErrorNote, Loader } from "../components/Loader.jsx";

export default function GenAI() {
  const [issues, setIssues] = useState(null);
  const [selected, setSelected] = useState("");
  const [count, setCount] = useState(4);
  const [error, setError] = useState(null);

  const [tcResult, setTcResult] = useState(null);
  const [tcLoading, setTcLoading] = useState(false);
  const [summary, setSummary] = useState(null);
  const [sumLoading, setSumLoading] = useState(false);

  useEffect(() => {
    api.issues()
      .then((rows) => {
        setIssues(rows);
        if (rows.length) setSelected(rows[0].key);
      })
      .catch(setError);
  }, []);

  const selectedIssue = (issues || []).find((i) => i.key === selected);

  async function onGenerate() {
    setTcLoading(true); setTcResult(null); setError(null);
    try {
      setTcResult(await api.generateTestCases(selected, count));
    } catch (e) { setError(e); } finally { setTcLoading(false); }
  }

  async function onSummarize() {
    setSumLoading(true); setSummary(null); setError(null);
    try {
      setSummary(await api.summarize(selected));
    } catch (e) { setError(e); } finally { setSumLoading(false); }
  }

  if (!issues) return <Loader label="Loading GenAI Studio…" />;

  return (
    <div className="page">
      <header className="page-head">
        <h1>GenAI Studio</h1>
        <p>Generate test cases and summaries from Jira issues with AI.</p>
      </header>

      <ErrorNote error={error} />

      <Card title="Pick an issue">
        <div className="genai-controls">
          <select className="select grow" value={selected} onChange={(e) => setSelected(e.target.value)}>
            {issues.map((i) => (
              <option key={i.key} value={i.key}>{i.key} — {i.summary}</option>
            ))}
          </select>
          <label className="count-label">
            Cases
            <input type="number" min={1} max={5} value={count}
                   onChange={(e) => setCount(Number(e.target.value))} className="count-input" />
          </label>
          <button className="btn" onClick={onGenerate} disabled={tcLoading || !selected}>
            {tcLoading ? "Generating…" : "Generate Test Cases"}
          </button>
          <button className="btn btn-ghost" onClick={onSummarize} disabled={sumLoading || !selected}>
            {sumLoading ? "Summarizing…" : "Summarize"}
          </button>
        </div>
        {selectedIssue && (
          <p className="issue-desc">{selectedIssue.description}</p>
        )}
      </Card>

      {summary && (
        <Card title="AI Summary" subtitle={`source: ${summary.source}`}>
          <pre className="summary">{summary.content}</pre>
        </Card>
      )}

      {tcResult && (
        <Card title="Generated Test Cases" subtitle={`source: ${tcResult.source}`}>
          <div className="tc-grid">
            {(tcResult.testCases || []).map((tc) => (
              <div key={tc.id} className="card inner">
                <div className="card-head">
                  <h3>{tc.title}</h3>
                  <span className={`badge prio-${tc.priority.toLowerCase()}`}>{tc.priority}</span>
                </div>
                <ol className="steps">
                  {tc.steps.map((s, idx) => <li key={idx}>{s}</li>)}
                </ol>
                <div className="expected"><strong>Expected:</strong> {tc.expectedResult}</div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
