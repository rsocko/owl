import { useNavigate, useParams } from 'react-router-dom';
import OcrFocusInspector from '../components/OcrFocusInspector';

export default function OcrFocusInspectorPage() {
  const { documentId } = useParams();
  const navigate = useNavigate();
  const id = Number(documentId);

  if (!Number.isFinite(id)) return null;

  return (
    <div className="ocr-focus-page">
      <OcrFocusInspector
        documentId={id}
        onReturn={() => navigate('/needs-review?type=ocr_quality_review')}
      />
    </div>
  );
}
