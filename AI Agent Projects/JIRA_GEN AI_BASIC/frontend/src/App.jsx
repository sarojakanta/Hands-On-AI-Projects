import { NavLink, Route, Routes } from "react-router-dom";
import { useEffect, useState } from "react";
import { api } from "./api.js";
import Dashboard from "./pages/Dashboard.jsx";
import Issues from "./pages/Issues.jsx";
import TestCases from "./pages/TestCases.jsx";
import GenAI from "./pages/GenAI.jsx";
import Ask from "./pages/Ask.jsx";

export default function App() {
  const [health, setHealth] = useState(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">◆</span>
          <div>
            <div className="brand-title">saroj Gen AI</div>
            <div className="brand-sub">JIRA Dashboard</div>
          </div>
        </div>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/issues">Issues</NavLink>
          <NavLink to="/test-cases">Test Cases</NavLink>
          <NavLink to="/genai">GenAI Studio</NavLink>
          <NavLink to="/ask">Ask Wizard</NavLink>
        </nav>
        <div className="status-box">
          <div className="status-row">
            <span>Data</span>
            <span className={`pill ${health?.mockData ? "pill-warn" : "pill-ok"}`}>
              {health ? (health.mockData ? "Mock" : "Live MCP") : "…"}
            </span>
          </div>
          <div className="status-row">
            <span>LLM</span>
            <span className={`pill ${health?.llmEnabled ? "pill-ok" : "pill-muted"}`}>
              {health ? (health.llmEnabled ? "On" : "Template") : "…"}
            </span>
          </div>
        </div>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/issues" element={<Issues />} />
          <Route path="/test-cases" element={<TestCases />} />
          <Route path="/genai" element={<GenAI />} />
          <Route path="/ask" element={<Ask />} />
        </Routes>
      </main>
    </div>
  );
}
