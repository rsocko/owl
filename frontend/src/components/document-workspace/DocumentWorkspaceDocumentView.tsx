import type { ReactNode } from 'react';
import {
  DocumentWorkspaceBody,
  DocumentWorkspaceHeader,
  DocumentWorkspaceTabs,
  type DocumentWorkspaceTab,
} from '../DocumentWorkspace';
import DocumentPreviewPane from './DocumentPreviewPane';

export interface DocumentWorkspaceIdentity {
  id: number;
  title: ReactNode;
  context?: ReactNode;
  date?: ReactNode;
  correspondent?: ReactNode;
  documentType?: ReactNode;
}

export interface DocumentWorkspaceContentTab extends DocumentWorkspaceTab {
  content?: ReactNode;
}

export default function DocumentWorkspaceDocumentView({
  document,
  activeTab,
  tabs,
  onTabChange,
  headerActions,
  previewUrl,
  preview,
  onExpandPreview,
  className = '',
  headerClassName = '',
  titleClassName = '',
  metadataClassName = '',
  actionsClassName = '',
  bodyClassName = '',
  previewClassName = '',
  panelClassName = '',
  tabsClassName = '',
  contentClassName = '',
  footer,
  children,
}: {
  document: DocumentWorkspaceIdentity;
  activeTab: string;
  tabs: DocumentWorkspaceContentTab[];
  onTabChange: (key: string) => void;
  headerActions?: ReactNode;
  previewUrl?: string | null;
  preview?: ReactNode;
  onExpandPreview?: () => void;
  className?: string;
  headerClassName?: string;
  titleClassName?: string;
  metadataClassName?: string;
  actionsClassName?: string;
  bodyClassName?: string;
  previewClassName?: string;
  panelClassName?: string;
  tabsClassName?: string;
  contentClassName?: string;
  footer?: ReactNode;
  children?: ReactNode;
}) {
  const activeContent = children
    ?? tabs.find((tab) => tab.key === activeTab)?.content
    ?? tabs[0]?.content;
  const sourcePreview = preview ?? (
    previewUrl ? (
      <DocumentPreviewPane
        title={typeof document.title === 'string' ? document.title : `Document #${document.id}`}
        previewUrl={previewUrl}
        onExpand={onExpandPreview}
      />
    ) : undefined
  );

  return (
    <div className={`document-workspace-document${className ? ` ${className}` : ''}`}>
      <DocumentWorkspaceHeader
        className={headerClassName}
        titleClassName={titleClassName}
        metadataClassName={metadataClassName}
        actionsClassName={actionsClassName}
        context={document.context ?? `Document #${document.id}`}
        title={document.title}
        metadata={[
          { label: 'Date', value: document.date ?? 'Not set' },
          { label: 'Correspondent', value: document.correspondent ?? 'Not set' },
          { label: 'Type', value: document.documentType ?? 'Not set' },
        ]}
        actions={headerActions}
      />
      <DocumentWorkspaceBody
        className={bodyClassName}
        previewClassName={previewClassName}
        panelClassName={panelClassName}
        preview={sourcePreview}
      >
        <DocumentWorkspaceTabs
          tabs={tabs}
          active={activeTab}
          onChange={onTabChange}
          className={tabsClassName}
          contentClassName={contentClassName}
          ariaLabel="Document workspace sections"
        >
          {activeContent}
        </DocumentWorkspaceTabs>
      </DocumentWorkspaceBody>
      {footer}
    </div>
  );
}
