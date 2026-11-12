// Thin API client for the Jira GenAI backend.
const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json();
}

export const api = {
  health: () => request("/health"),
  issues: () => request("/issues"),
  issue: (key) => request(`/issues/${key}`),
  testCases: () => request("/test-cases"),
  metrics: () => request("/metrics"),
  generateTestCases: (issueKey, count = 4) =>
    request("/genai/test-cases", {
      method: "POST",
      body: JSON.stringify({ issueKey, count }),
    }),
  summarize: (issueKey) =>
    request("/genai/summarize", {
      method: "POST",
      body: JSON.stringify({ issueKey }),
    }),
  ask: (question) =>
    request("/genai/ask", {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
};
