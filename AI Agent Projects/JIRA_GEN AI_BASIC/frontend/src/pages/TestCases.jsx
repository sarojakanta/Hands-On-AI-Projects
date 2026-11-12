import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Card } from "../components/Card.jsx";
import { ErrorNote, Loader } from "../components/Loader.jsx";

export default function TestCases() {
  const [cases, setCases] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.testCases().then(setCases).catch(setError);
  }, []);

  if (error) return <ErrorNote error={error} />;
  if (!cases) return <Loader label="Loading test cases…" />;

  return (
    <div className="page">
      <header className="page-head">
        <h1>Test Cases</h1>
        <p>Test coverage linked to Jira issues.</p>
      </header>
      <div className="tc-grid">
        {cases.map((tc) => (
          <Card key={tc.id} title={tc.title}
                subtitle={`${tc.id} · ${tc.issueKey}`}
                actions={<span className={`badge tc-${tc.status.replace(/\s+/g, "-").toLowerCase()}`}>{tc.status}</span>}>
            <ol className="steps">
              {tc.steps.map((s, idx) => <li key={idx}>{s}</li>)}
            </ol>
            <div className="expected">
              <strong>Expected:</strong> {tc.expectedResult}
            </div>
            <div className="tc-foot">
              <span className={`badge prio-${tc.priority.toLowerCase()}`}>{tc.priority}</span>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
