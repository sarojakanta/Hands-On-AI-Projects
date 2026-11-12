export function Card({ title, subtitle, children, actions, wide }) {
  return (
    <section className={`card${wide ? " card-wide" : ""}`}>
      {(title || actions) && (
        <header className="card-head">
          <div>
            {title && <h3>{title}</h3>}
            {subtitle && <p className="card-sub">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}

export function Stat({ label, value, accent }) {
  return (
    <div className="stat" style={accent ? { borderTopColor: accent } : undefined}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}
