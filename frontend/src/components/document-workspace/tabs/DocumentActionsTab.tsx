import type { ComponentProps } from 'react';
import DocumentActionsPanel from '../../DocumentActionsPanel';

export default function DocumentActionsTab(props: ComponentProps<typeof DocumentActionsPanel>) {
  return (
    <div className="document-workspace-actions-tab">
      <DocumentActionsPanel {...props} />
    </div>
  );
}
