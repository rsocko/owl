import type { ComponentType } from 'react';
import ActionsWorkflow from '../components/document-workspace/workflows/ActionsWorkflow';
import InboxWorkflow from '../components/document-workspace/workflows/InboxWorkflow';
import ReviewWorkflow from '../components/document-workspace/workflows/ReviewWorkflow';

export type DocumentWorkspaceMode = 'inbox' | 'actions' | 'review';

const WORKFLOWS: Record<DocumentWorkspaceMode, ComponentType> = {
  inbox: InboxWorkflow,
  actions: ActionsWorkflow,
  review: ReviewWorkflow,
};

export default function DocumentWorkspace({ mode }: { mode: DocumentWorkspaceMode }) {
  const Workflow = WORKFLOWS[mode];
  return <Workflow />;
}
