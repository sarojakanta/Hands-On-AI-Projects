import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { Card } from "../components/Card.jsx";
import { ErrorNote, Loader } from "../components/Loader.jsx";

export default function Issues() {
  const [issues, setIssues] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("All");

  useEffect(() => {
    api.issues().then(setIssues).catch(setError);
  }, []);

  const statuses = useMemo(
    () => ["All", ...new Set((issues || []).map((i) => i.status))],
    [issues]
  );
  const rows = useMemo(
    () => (issues || []).filter((i) => filter === "All" || i.status === filter),
    [issues, filter]
  );

  if (error) return <ErrorNote error={error} />;
  if (!issues) return <Loader label="Loading issues…" />;

  return (
    <div className="page">
      <header className="page-head">
        <h1>Jira Issues</h1>
        <p>Pulled from the Jira MCP server (mock data when offline).</p>
      </header>
      <Card
        title={`${rows.length} issue${rows.length === 1 ? "" : "s"}`}
        actions={
          <select value={filter} onChange={(e) => setFilter(e.target.value)} className="select">
            {statuses.map((s) => <option key={s}>{s}</option>)}
          </select>
        }
      >
        <table className="table">
          <thead>
            <tr>
              <th>Key</th><th>Summary</th><th>Type</th><th>Status</th>
              <th>Priority</th><th>Assignee</th><th>Pts</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((i) => (
              <tr key={i.key}>
                <td className="mono">{i.key}</td>
                <td>{i.summary}</td>
                <td>{i.issueType}</td>
                <td><span className={`badge status-${i.status.replace(/\s+/g, "-").toLowerCase()}`}>{i.status}</span></td>
                <td><span className={`badge prio-${i.priority.toLowerCase()}`}>{i.priority}</span></td>
                <td>{i.assignee}</td>
                <td>{i.storyPoints ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
