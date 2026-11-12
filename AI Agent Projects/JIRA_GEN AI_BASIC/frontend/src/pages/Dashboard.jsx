import { useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api.js";
import { Card, Stat } from "../components/Card.jsx";
import { ErrorNote, Loader } from "../components/Loader.jsx";

const STATUS_COLORS = {
  "To Do": "#94a3b8", "In Progress": "#3b82f6", "In Review": "#a855f7",
  Done: "#22c55e", Blocked: "#ef4444",
};
const PRIORITY_COLORS = {
  Critical: "#ef4444", High: "#f97316", Medium: "#eab308", Low: "#22c55e",
};
const PIE = ["#6366f1", "#22c55e", "#f97316", "#ef4444", "#a855f7", "#06b6d4"];

export default function Dashboard() {
  const [metrics, setMetrics] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.metrics().then(setMetrics).catch(setError);
  }, []);

  if (error) return <ErrorNote error={error} />;
  if (!metrics) return <Loader label="Loading dashboard…" />;

  const { totals } = metrics;

  return (
    <div className="page">
      <header className="page-head">
        <h1>Project Dashboard</h1>
        <p>Generative-AI powered view of Jira issues and test coverage.</p>
      </header>

      <div className="stat-grid">
        <Stat label="Total Issues" value={totals.issues} accent="#6366f1" />
        <Stat label="Test Cases" value={totals.testCases} accent="#06b6d4" />
        <Stat label="Test Coverage" value={`${totals.coveragePct}%`} accent="#22c55e" />
        <Stat label="Open Bugs" value={totals.openBugs} accent="#ef4444" />
      </div>

      <div className="chart-grid">
        <Card title="Issues by Status" subtitle="Distribution across workflow states">
          <ResponsiveContainer width="100%" height={260}>
            <PieChart>
              <Pie data={metrics.statusDistribution} dataKey="value" nameKey="name"
                   cx="50%" cy="50%" outerRadius={90} label>
                {metrics.statusDistribution.map((d) => (
                  <Cell key={d.name} fill={STATUS_COLORS[d.name] || "#6366f1"} />
                ))}
              </Pie>
              <Tooltip /><Legend />
            </PieChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Issues by Priority" subtitle="Where the risk sits">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={metrics.priorityDistribution}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="name" stroke="#94a3b8" /><YAxis allowDecimals={false} stroke="#94a3b8" />
              <Tooltip cursor={{ fill: "#1e293b55" }} />
              <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                {metrics.priorityDistribution.map((d) => (
                  <Cell key={d.name} fill={PRIORITY_COLORS[d.name] || "#6366f1"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Test Coverage" subtitle="Issues with vs without test cases">
          <ResponsiveContainer width="100%" height={260}>
            <PieChart>
              <Pie data={metrics.coverage} dataKey="value" nameKey="name"
                   cx="50%" cy="50%" innerRadius={55} outerRadius={90} label>
                <Cell fill="#22c55e" /><Cell fill="#334155" />
              </Pie>
              <Tooltip /><Legend />
            </PieChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Test Case Status" subtitle="Execution results">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={metrics.testCaseStatus}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="name" stroke="#94a3b8" /><YAxis allowDecimals={false} stroke="#94a3b8" />
              <Tooltip cursor={{ fill: "#1e293b55" }} />
              <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                {metrics.testCaseStatus.map((d, i) => (
                  <Cell key={d.name} fill={PIE[i % PIE.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Sprint Burndown" subtitle="Ideal vs actual remaining points" wide>
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={metrics.burndown}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="day" stroke="#94a3b8" /><YAxis stroke="#94a3b8" />
              <Tooltip /><Legend />
              <Line type="monotone" dataKey="ideal" stroke="#64748b" strokeDasharray="5 5" dot={false} />
              <Line type="monotone" dataKey="remaining" stroke="#6366f1" strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Story Points by Sprint" subtitle="Committed workload">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={metrics.pointsBySprint}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="name" stroke="#94a3b8" /><YAxis stroke="#94a3b8" />
              <Tooltip cursor={{ fill: "#1e293b55" }} />
              <Bar dataKey="value" fill="#06b6d4" radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>
    </div>
  );
}
