import type { ReactNode } from 'react';

export type DocumentWorkspaceTab = {
  key: string;
  label: ReactNode;
  ariaLabel?: string;
  className?: string;
};

export function DocumentWorkspaceFrame({
  collapsed = false,
  className = '',
  railClassName = '',
  mainClassName = '',
  rail,
  children,
}: {
  collapsed?: boolean;
  className?: string;
  railClassName?: string;
  mainClassName?: string;
  rail: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={`document-workspace-frame${collapsed ? ' rail-collapsed' : ''}${className ? ` ${className}` : ''}`}>
      <aside className={`document-workspace-frame-rail${railClassName ? ` ${railClassName}` : ''}`}>
        {rail}
      </aside>
      <main className={`document-workspace-frame-main${mainClassName ? ` ${mainClassName}` : ''}`}>
        {children}
      </main>
    </div>
  );
}

export function DocumentWorkspaceHeader({
  context,
  title,
  metadata,
  actions,
  className = '',
  titleClassName = '',
  metadataClassName = '',
  actionsClassName = '',
}: {
  context?: ReactNode;
  title: ReactNode;
  metadata?: Array<{ label: string; value: ReactNode }>;
  actions?: ReactNode;
  className?: string;
  titleClassName?: string;
  metadataClassName?: string;
  actionsClassName?: string;
}) {
  return (
    <header className={`document-workspace-frame-header${className ? ` ${className}` : ''}`}>
      <div className={`document-workspace-frame-title${titleClassName ? ` ${titleClassName}` : ''}`}>
        {context ? <span>{context}</span> : null}
        <h2>{title}</h2>
      </div>
      {metadata?.length ? (
        <dl className={`document-workspace-frame-metadata${metadataClassName ? ` ${metadataClassName}` : ''}`}>
          {metadata.map((item) => (
            <div key={item.label}>
              <dt>{item.label}</dt>
              <dd>{item.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {actions ? <div className={`document-workspace-frame-header-actions${actionsClassName ? ` ${actionsClassName}` : ''}`}>{actions}</div> : null}
    </header>
  );
}

export function DocumentWorkspaceBody({
  preview,
  children,
  className = '',
  previewClassName = '',
  panelClassName = '',
}: {
  preview?: ReactNode;
  children: ReactNode;
  className?: string;
  previewClassName?: string;
  panelClassName?: string;
}) {
  return (
    <div className={`document-workspace-frame-body${preview ? '' : ' without-preview'}${className ? ` ${className}` : ''}`}>
      {preview ? (
        <section className={`document-workspace-frame-preview${previewClassName ? ` ${previewClassName}` : ''}`} aria-label="Source document">
          {preview}
        </section>
      ) : null}
      <section className={`document-workspace-frame-panel${panelClassName ? ` ${panelClassName}` : ''}`}>
        {children}
      </section>
    </div>
  );
}

export function DocumentWorkspaceTabs({
  tabs,
  active,
  onChange,
  children,
  ariaLabel = 'Document workspace sections',
  className = '',
  contentClassName = '',
}: {
  tabs: DocumentWorkspaceTab[];
  active: string;
  onChange: (key: string) => void;
  children: ReactNode;
  ariaLabel?: string;
  className?: string;
  contentClassName?: string;
}) {
  return (
    <>
      <div className={`document-workspace-frame-tabs${className ? ` ${className}` : ''}`} role="tablist" aria-label={ariaLabel}>
        {tabs.map((tab) => (
          <button
            type="button"
            role="tab"
            aria-label={tab.ariaLabel}
            aria-selected={active === tab.key}
            className={`${active === tab.key ? 'active' : ''}${tab.className ? ` ${tab.className}` : ''}`}
            key={tab.key}
            onClick={() => onChange(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className={`document-workspace-frame-tab-content${contentClassName ? ` ${contentClassName}` : ''}`}>{children}</div>
    </>
  );
}
