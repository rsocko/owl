import type { ReactNode } from 'react';

export type Tone = 'ok' | 'warn' | 'err' | 'info' | 'muted' | 'success' | 'warning' | 'danger';

export function Badge({ tone = 'muted', children }: { tone?: Tone; children: ReactNode }) {
  const toneClass: Record<Tone, string> = {
    ok: 'status-ok',
    success: 'status-ok',
    warn: 'status-warn',
    warning: 'status-warn',
    err: 'status-err',
    danger: 'status-err',
    info: 'info',
    muted: 'muted',
  };
  return <span className={`badge ${toneClass[tone]}`}>{children}</span>;
}

export function StatusDot({ tone }: { tone: 'ok' | 'warn' | 'err' }) {
  return <span className={`status-dot ${tone}`} />;
}

export function Card({
  title,
  actions,
  children,
  className = '',
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`card ${className}`}>
      {title && (
        <div className="card-header">
          <div className="card-title">{title}</div>
          {actions}
        </div>
      )}
      <div className="card-body">{children}</div>
    </div>
  );
}

export function StatCard({
  title,
  metric,
  desc,
  status,
}: {
  title: string;
  metric: ReactNode;
  desc?: ReactNode;
  status?: { label: string; tone: Tone };
}) {
  return (
    <div className="stat-card">
      <div className="stat-card-header">
        <div className="stat-card-title">{title}</div>
        {status && <Badge tone={status.tone}>{status.label}</Badge>}
      </div>
      <div className="stat-card-metric">{metric}</div>
      {desc && <div className="stat-card-desc">{desc}</div>}
    </div>
  );
}

export function StatGrid({ children }: { children: ReactNode }) {
  return <div className="stat-grid">{children}</div>;
}

export function Button({
  children,
  variant = 'default',
  size,
  onClick,
  disabled,
  type = 'button',
  title,
}: {
  children: ReactNode;
  variant?: 'default' | 'primary' | 'success' | 'danger' | 'ghost';
  size?: 'sm';
  onClick?: () => void;
  disabled?: boolean;
  type?: 'button' | 'submit';
  title?: string;
}) {
  const cls = ['btn', variant !== 'default' ? variant : '', size === 'sm' ? 'sm' : ''].filter(Boolean).join(' ');
  return (
    <button className={cls} onClick={onClick} disabled={disabled} type={type} title={title}>
      {children}
    </button>
  );
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { key: string; label: ReactNode }[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="tabs">
      {tabs.map((t) => (
        <button key={t.key} className={`tab ${active === t.key ? 'active' : ''}`} onClick={() => onChange(t.key)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function FilterPills({
  options,
  active,
  onChange,
}: {
  options: { key: string; label: ReactNode }[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="filter-pills">
      {options.map((o) => (
        <button
          key={o.key}
          className={`filter-pill ${active === o.key ? 'active' : ''}`}
          onClick={() => onChange(o.key)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function confidenceTone(pct: number): 'high' | 'medium' | 'low' {
  if (pct >= 85) return 'high';
  if (pct >= 60) return 'medium';
  return 'low';
}

export function ConfidenceBar({ label, pct }: { label: string; pct: number }) {
  const tone = confidenceTone(pct);
  return (
    <div className="confidence-row">
      <div className="confidence-label">{label}</div>
      <div className="confidence-bar-bg">
        <div className={`confidence-bar-fill ${tone}`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
      </div>
      <div className="confidence-pct">{pct}%</div>
    </div>
  );
}

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="loading-state">
      <span className="spinner" />
      {label}
    </div>
  );
}

export function EmptyState({ icon = '📭', title, desc }: { icon?: string; title: ReactNode; desc?: ReactNode }) {
  return (
    <div className="empty-state">
      <div className="icon">{icon}</div>
      <div>{title}</div>
      {desc && <div className="text-muted">{desc}</div>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="empty-state">
      <div className="icon">⚠️</div>
      <div>{message}</div>
      {onRetry && (
        <div style={{ marginTop: 12 }}>
          <Button onClick={onRetry}>Retry</Button>
        </div>
      )}
    </div>
  );
}

export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{title}</div>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

export function SidePanel({
  title,
  onClose,
  children,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <>
      <div className="side-panel-overlay" onClick={onClose} />
      <div className="side-panel">
        <div className="side-panel-header">
          <div className="modal-title">{title}</div>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="side-panel-body">{children}</div>
      </div>
    </>
  );
}

export function Toast({ message, tone = 'success' }: { message: string; tone?: 'success' | 'error' }) {
  return <div className={`toast ${tone}`}>{message}</div>;
}

export function PageHeader({ title, desc, actions }: { title: ReactNode; desc?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-header" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
      <div>
        <h1>{title}</h1>
        {desc && <p>{desc}</p>}
      </div>
      {actions}
    </div>
  );
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  emptyLabel = 'No data',
}: {
  columns: { key: string; header: ReactNode; render: (row: T) => ReactNode; width?: string }[];
  rows: T[];
  rowKey: (row: T) => string;
  emptyLabel?: string;
}) {
  if (rows.length === 0) {
    return <EmptyState title={emptyLabel} />;
  }
  return (
    <table className="data-table">
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.key} style={{ width: c.width }}>
              {c.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={rowKey(row)}>
            {columns.map((c) => (
              <td key={c.key}>{c.render(row)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
