export function Loader({ label = "Loading…" }) {
  return <div className="loader">{label}</div>;
}

export function ErrorNote({ error }) {
  if (!error) return null;
  return <div className="error-note">⚠ {String(error.message || error)}</div>;
}
