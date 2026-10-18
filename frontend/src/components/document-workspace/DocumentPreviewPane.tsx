import { ExternalLink, Maximize2 } from 'lucide-react';

export default function DocumentPreviewPane({
  title,
  previewUrl,
  onExpand,
}: {
  title: string;
  previewUrl: string;
  onExpand?: () => void;
}) {
  return (
    <>
      <iframe title={`Preview of ${title}`} src={previewUrl} />
      <div className="document-workspace-preview-tools">
        {onExpand ? (
          <button type="button" onClick={onExpand}>
            <Maximize2 size={15} /> Expand
          </button>
        ) : null}
        <a href={previewUrl} target="_blank" rel="noopener noreferrer">
          <ExternalLink size={15} /> Open preview
        </a>
      </div>
    </>
  );
}
