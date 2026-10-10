import { useEffect, useMemo, useState } from 'react';
import { endpoints } from '../lib/api';
import { Badge, Button } from './ui';

export interface RelationshipNode {
  id: string;
  kind: 'obligation' | 'action' | 'document' | 'payment_evidence';
  label: string;
  subtitle?: string | null;
  occurred_at?: string | null;
  status?: string | null;
  metadata: Record<string, unknown>;
}

interface RelationshipEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  label: string;
  status: string;
}

interface RelationshipGraph {
  contract_version: string;
  scope: { action_id: number; obligation_id?: string | null };
  nodes: RelationshipNode[];
  edges: RelationshipEdge[];
  warnings: Array<{ code: string; message: string }>;
}

function displayKind(kind: RelationshipNode['kind']): string {
  return {
    obligation: 'Obligation',
    action: 'Action',
    document: 'Document',
    payment_evidence: 'Payment evidence',
  }[kind];
}

function formatDate(value?: string | null): string {
  if (!value) return 'Date unavailable';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

function nodeTone(kind: RelationshipNode['kind']) {
  if (kind === 'obligation') return 'warning' as const;
  if (kind === 'payment_evidence') return 'success' as const;
  if (kind === 'action') return 'info' as const;
  return 'muted' as const;
}

export function ObligationRelationshipView({
  actionId,
  onOpenDocument,
}: {
  actionId: number;
  onOpenDocument: (documentId: number, title: string) => void;
}) {
  const [graph, setGraph] = useState<RelationshipGraph | null>(null);
  const [view, setView] = useState<'timeline' | 'graph'>('timeline');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setGraph(null);
    setError(null);
    void (endpoints.relationships.graph(actionId) as Promise<RelationshipGraph>)
      .then((payload) => {
        if (active) setGraph(payload);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Could not load links.');
      });
    return () => {
      active = false;
    };
  }, [actionId]);

  const timeline = useMemo(() => (
    [...(graph?.nodes ?? [])]
      .filter((node) => node.kind === 'document' || node.kind === 'payment_evidence')
      .sort((left, right) => (
        (left.occurred_at || '9999').localeCompare(right.occurred_at || '9999')
        || left.id.localeCompare(right.id)
      ))
  ), [graph]);
  const graphHeight = Math.max(420, Math.ceil((graph?.nodes.length ?? 0) / 4) * 150);

  if (error) {
    return (
      <div className="aq-relationship-error" role="alert">
        <strong>Relationship view unavailable</strong>
        <span>{error} Refresh the queue and try again.</span>
      </div>
    );
  }
  if (!graph) {
    return <div className="aq-relationship-loading" role="status">Loading durable links…</div>;
  }

  return (
    <section className="aq-relationship-view" aria-label="Obligation relationships">
      <div className="aq-relationship-heading">
        <div>
          <strong>Obligation links</strong>
          <span>One OWL projection · {graph.nodes.length} nodes · {graph.edges.length} edges</span>
        </div>
        <div className="aq-relationship-toggle" role="group" aria-label="Relationship view">
          <Button
            size="sm"
            variant={view === 'timeline' ? 'primary' : 'default'}
            onClick={() => setView('timeline')}
            aria-pressed={view === 'timeline'}
          >
            Timeline
          </Button>
          <Button
            size="sm"
            variant={view === 'graph' ? 'primary' : 'default'}
            onClick={() => setView('graph')}
            aria-pressed={view === 'graph'}
          >
            Graph
          </Button>
        </div>
      </div>

      {view === 'timeline' ? (
        <ol className="aq-relationship-timeline">
          {timeline.map((node) => {
            const documentId = node.kind === 'document'
              ? Number(node.metadata.document_id)
              : null;
            return (
              <li className={`aq-relationship-event kind-${node.kind}`} key={node.id}>
                <span className="aq-relationship-event-marker" aria-hidden="true" />
                <div>
                  <span className="aq-relationship-event-date">{formatDate(node.occurred_at)}</span>
                  <strong>{node.label}</strong>
                  <span>{node.subtitle || displayKind(node.kind)}</span>
                </div>
                <Badge tone={nodeTone(node.kind)}>{displayKind(node.kind)}</Badge>
                {documentId && (
                  <Button size="sm" variant="ghost" onClick={() => onOpenDocument(documentId, node.label)}>
                    Open
                  </Button>
                )}
              </li>
            );
          })}
        </ol>
      ) : (
        <div className="aq-relationship-graph">
          <svg
            viewBox={`0 0 900 ${graphHeight}`}
            role="img"
            aria-labelledby={`relationship-graph-title-${actionId}`}
          >
            <title id={`relationship-graph-title-${actionId}`}>
              Obligation graph with {graph.nodes.length} nodes and {graph.edges.length} edges
            </title>
            {graph.edges.map((edge, index) => {
              const sourceIndex = graph.nodes.findIndex((node) => node.id === edge.source);
              const targetIndex = graph.nodes.findIndex((node) => node.id === edge.target);
              if (sourceIndex < 0 || targetIndex < 0) return null;
              const source = {
                x: 110 + (sourceIndex % 4) * 225,
                y: 75 + Math.floor(sourceIndex / 4) * 150,
              };
              const target = {
                x: 110 + (targetIndex % 4) * 225,
                y: 75 + Math.floor(targetIndex / 4) * 150,
              };
              return (
                <g key={edge.id}>
                  <line
                    className={`aq-graph-edge kind-${edge.kind}`}
                    x1={source.x}
                    y1={source.y}
                    x2={target.x}
                    y2={target.y}
                  />
                  <text
                    className="aq-graph-edge-label"
                    x={(source.x + target.x) / 2}
                    y={(source.y + target.y) / 2 - 6 - (index % 2) * 9}
                  >
                    {edge.label}
                  </text>
                </g>
              );
            })}
            {graph.nodes.map((node, index) => {
              const x = 110 + (index % 4) * 225;
              const y = 75 + Math.floor(index / 4) * 150;
              return (
                <g className={`aq-graph-node kind-${node.kind}`} key={node.id} transform={`translate(${x}, ${y})`}>
                  <rect x="-82" y="-36" width="164" height="72" rx="14" />
                  <text className="aq-graph-node-kind" x="0" y="-12">{displayKind(node.kind)}</text>
                  <text className="aq-graph-node-label" x="0" y="9">{node.label.slice(0, 24)}</text>
                  <text className="aq-graph-node-status" x="0" y="25">{node.status || 'linked'}</text>
                </g>
              );
            })}
          </svg>
        </div>
      )}

      {graph.warnings.map((warning) => (
        <p className="aq-relationship-warning" key={warning.code}>{warning.message}</p>
      ))}
    </section>
  );
}
