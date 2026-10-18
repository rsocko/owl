import type { ComponentProps } from 'react';
import OcrSummaryPanel from '../../OcrSummaryPanel';

export default function DocumentOcrTab(props: ComponentProps<typeof OcrSummaryPanel>) {
  return (
    <div className="document-workspace-ocr-tab">
      <OcrSummaryPanel {...props} />
    </div>
  );
}
