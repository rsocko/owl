import { useNavigate, useParams } from 'react-router-dom';
import OcrFocusInspector from '../components/OcrFocusInspector';

export default function OcrFocusInspectorPage() {
  const { documentId } = useParams();
  const navigate = useNavigate();
  const id = Number(documentId);

  if (!Number.isFinite(id)) return null;

  return (
    <div style={{ height: 'calc(100dvh - var(--topnav-height))', margin: -20 }}>
      <OcrFocusInspector
        documentId={id}
        onReturn={() => navigate('/ocr-quality/queue')}
      />
    </div>
  );
}
