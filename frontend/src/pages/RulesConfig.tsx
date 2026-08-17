import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, PageHeader, Toast } from '../components/ui';
import { getToastDuration } from '../lib/toast';

// ── Types ──

type RuleType = 'basic' | 'llm' | 'n8n';
type RouteTarget = 'insight' | 'triage' | 'mc';
type Schedule = 'on_ingest' | 'hourly' | 'daily' | 'weekly' | 'monthly' | 'manual';
type AppliesTo = 'all' | 'bills' | 'eobs' | 'statements' | 'bills_eobs';

interface ThresholdCondition {
  id: string;
  field: string;
  operator: string;
  value: string;
  unit: string;
  conjunction: 'IF' | 'AND' | 'OR';
}

interface Rule {
  id: string;
  name: string;
  type: RuleType;
  description: string;
  enabled: boolean;
  schedule: Schedule;
  appliesTo: AppliesTo;
  routing: RouteTarget;
  escalation?: { target: string; condition: string };
  hitsThisMonth: number;
  // Basic rule fields
  conditions?: ThresholdCondition[];
  // LLM rule fields
  promptTemplate?: string;
  model?: string;
  costPerDoc?: string;
  // n8n rule fields
  webhookUrl?: string;
  n8nStatus?: 'connected' | 'error' | 'unknown';
  n8nLastTriggered?: string;
  n8nFlowNodes?: { label: string; type: 'trigger' | 'process' | 'llm' | 'output' }[];
  // Route badges shown in list
  routeBadges?: { label: string; type: 'triage' | 'mc' | 'insight' }[];
}

type ToastState = { message: string; tone: 'success' | 'error' };
type TabFilter = 'all' | RuleType;

// ── Seed data ──

const AVAILABLE_FIELDS = [
  'spend_change_pct', 'days_since_service', 'similarity_score',
  'extraction_confidence', 'patient_responsibility',
  'document_type', 'correspondent', 'tag',
];
const OPERATORS = ['≥', '≤', '=', '≠', '>', '<', 'contains'];
const UNITS = ['%', 'days', '$', 'count'];
const MODELS = [
  { value: 'gpt-4o-mini', label: 'gpt-4o-mini (fastest, cheapest)' },
  { value: 'gpt-4o', label: 'gpt-4o (balanced)' },
  { value: 'claude-3.5-sonnet', label: 'claude-3.5-sonnet (best reasoning)' },
  { value: 'llama-3-local', label: 'llama-3 local (free, private)' },
];
const LLM_VARIABLES = [
  '{{provider_name}}', '{{patient_name}}', '{{billed_amount}}', '{{allowed_amount}}',
  '{{description}}', '{{date_of_service}}', '{{document_text}}', '{{region}}', '{{insurance_plan}}',
];

function uid() { return Math.random().toString(36).slice(2, 9); }

const SEED_RULES: Rule[] = [
  {
    id: uid(), name: 'Orphan Detection', type: 'basic', enabled: true,
    description: 'Flag EOBs with no matching bill after 30 days, escalate at 60 days',
    schedule: 'daily', appliesTo: 'all', routing: 'triage', hitsThisMonth: 3,
    conditions: [
      { id: uid(), field: 'days_since_service', operator: '≥', value: '30', unit: 'days', conjunction: 'IF' },
    ],
    routeBadges: [{ label: '→ Needs Review', type: 'triage' }, { label: '→ MC (60d)', type: 'mc' }],
    escalation: { target: 'Mission Control alert', condition: 'days_since_service ≥ 60' },
  },
  {
    id: uid(), name: 'Duplicate Detection', type: 'basic', enabled: true,
    description: 'Flag documents with ≥85% similarity score as potential duplicates',
    schedule: 'on_ingest', appliesTo: 'all', routing: 'triage', hitsThisMonth: 5,
    conditions: [
      { id: uid(), field: 'similarity_score', operator: '≥', value: '85', unit: '%', conjunction: 'IF' },
    ],
    routeBadges: [{ label: '→ Needs Review', type: 'triage' }],
  },
  {
    id: uid(), name: 'Monthly Spend Spike', type: 'basic', enabled: true,
    description: 'Alert when any account\'s monthly spend increases by ≥30% over 3-month average',
    schedule: 'monthly', appliesTo: 'bills_eobs', routing: 'insight', hitsThisMonth: 1,
    conditions: [
      { id: uid(), field: 'spend_change_pct', operator: '≥', value: '30', unit: '%', conjunction: 'IF' },
    ],
    routeBadges: [{ label: '→ Insight', type: 'insight' }, { label: '→ MC (≥50%)', type: 'mc' }],
    escalation: { target: 'Mission Control alert', condition: 'spend_change_pct ≥ 50' },
  },
  {
    id: uid(), name: 'Low Extraction Confidence', type: 'basic', enabled: true,
    description: 'Flag documents where any critical field has extraction confidence < 60%',
    schedule: 'on_ingest', appliesTo: 'all', routing: 'triage', hitsThisMonth: 2,
    conditions: [
      { id: uid(), field: 'extraction_confidence', operator: '<', value: '60', unit: '%', conjunction: 'IF' },
    ],
    routeBadges: [{ label: '→ Needs Review', type: 'triage' }],
  },
  {
    id: uid(), name: 'Bill Reasonableness Check', type: 'llm', enabled: true,
    description: 'Use LLM to analyze if billed amount seems reasonable for the procedure and region',
    schedule: 'on_ingest', appliesTo: 'bills_eobs', routing: 'insight', hitsThisMonth: 1,
    model: 'claude-3.5-sonnet',
    costPerDoc: '~$0.02/doc',
    promptTemplate: `// Analyze the medical bill for reasonableness
You are a medical billing analyst. Given the following document data:

Provider: {{provider_name}}
Procedure: {{description}}
Billed amount: {{billed_amount}}
Region: {{region}}

Analyze whether this charge is reasonable. Consider:
1. Is the amount within typical range for this procedure?
2. Are there any red flags (balance billing, surprise charges)?
3. Should the patient negotiate or appeal?

Respond with JSON: { "reasonable": bool, "confidence": 0-1, "explanation": "...", "action": "none|review|appeal" }`,
    routeBadges: [{ label: '→ Insight', type: 'insight' }, { label: '→ MC (flagged)', type: 'mc' }],
    escalation: { target: 'Mission Control alert', condition: 'action = "appeal"' },
  },
  {
    id: uid(), name: 'Coverage Change Detection', type: 'llm', enabled: true,
    description: 'Analyze EOBs to detect changes in coverage terms, deductible resets, or plan modifications',
    schedule: 'weekly', appliesTo: 'eobs', routing: 'insight', hitsThisMonth: 0,
    model: 'gpt-4o',
    costPerDoc: '~$0.05/run',
    promptTemplate: `You are a health insurance analyst. Review the following EOB data and compare with prior EOBs for the same patient.

Patient: {{patient_name}}
Plan: {{insurance_plan}}
Document text: {{document_text}}

Identify any changes in:
1. Coverage terms or limits
2. Deductible resets or changes
3. Plan tier modifications
4. New exclusions or inclusions

Respond with JSON: { "changes_detected": bool, "changes": [...], "severity": "none|low|medium|high" }`,
    routeBadges: [{ label: '→ Insight', type: 'insight' }],
  },
  {
    id: uid(), name: 'Monarch Cross-Reference', type: 'n8n', enabled: true,
    description: 'Check if medical bills appear in Monarch Money transactions. Flag unpaid bills.',
    schedule: 'weekly', appliesTo: 'bills', routing: 'triage', hitsThisMonth: 2,
    webhookUrl: 'https://n8n.example.com/webhook/di-monarch-xref',
    n8nStatus: 'connected',
    n8nLastTriggered: '2 days ago (took 3.2s)',
    n8nFlowNodes: [
      { label: 'Webhook', type: 'trigger' },
      { label: 'Parse DI Data', type: 'process' },
      { label: 'Query Monarch', type: 'process' },
      { label: 'Match Analysis', type: 'llm' },
      { label: 'Return Result', type: 'output' },
    ],
    routeBadges: [{ label: '→ Needs Review', type: 'triage' }, { label: '→ MC (unpaid)', type: 'mc' }],
  },
  {
    id: uid(), name: 'Provider Price Comparison', type: 'n8n', enabled: false,
    description: 'Cross-check billed amounts against fair-price databases (Healthcare Bluebook API)',
    schedule: 'on_ingest', appliesTo: 'bills', routing: 'insight', hitsThisMonth: 0,
    webhookUrl: 'https://n8n.example.com/webhook/di-price-check',
    n8nStatus: 'error',
    n8nFlowNodes: [
      { label: 'Webhook', type: 'trigger' },
      { label: 'Parse Bill', type: 'process' },
      { label: 'Bluebook API', type: 'process' },
      { label: 'Compare', type: 'output' },
    ],
    routeBadges: [{ label: '→ Insight', type: 'insight' }],
  },
];

const SCHEDULE_LABELS: Record<Schedule, string> = {
  on_ingest: 'On document ingest (real-time)',
  hourly: 'Hourly',
  daily: 'Daily (2:00 AM)',
  weekly: 'Weekly (Sunday 2:00 AM)',
  monthly: 'Monthly (1st, 2:00 AM)',
  manual: 'Manual only',
};
const SCHEDULE_ICONS: Record<Schedule, string> = {
  on_ingest: '🔄 On ingest',
  hourly: '🔄 Hourly',
  daily: '🔄 Daily',
  weekly: '🔄 Weekly',
  monthly: '🔄 Monthly',
  manual: '🔄 Manual',
};
const APPLIES_TO_LABELS: Record<AppliesTo, string> = {
  all: 'All documents',
  bills: 'Bills only',
  eobs: 'EOBs only',
  statements: 'Statements only',
  bills_eobs: 'Bills + EOBs',
};

// ── Helpers ──

function emptyRule(type: RuleType = 'basic'): Rule {
  return {
    id: uid(), name: '', type, description: '', enabled: true,
    schedule: 'on_ingest', appliesTo: 'all', routing: 'triage', hitsThisMonth: 0,
    conditions: type === 'basic' ? [{ id: uid(), field: 'extraction_confidence', operator: '≥', value: '60', unit: '%', conjunction: 'IF' }] : undefined,
    promptTemplate: type === 'llm' ? '' : undefined,
    model: type === 'llm' ? 'gpt-4o-mini' : undefined,
    webhookUrl: type === 'n8n' ? '' : undefined,
    n8nStatus: type === 'n8n' ? 'unknown' : undefined,
    routeBadges: [],
  };
}

// ── Sub-components ──

function EngineCards() {
  return (
    <div className="engine-banner">
      <div className="engine-card">
        <div className="engine-card-header">
          <span className="engine-card-icon">⚙️</span>
          <span className="engine-card-title">Basic Rules</span>
          <span className="engine-card-badge basic">Built-in</span>
        </div>
        <div className="engine-card-desc">Threshold-based rules using extracted metadata. Fast, free, deterministic. Best for known patterns.</div>
        <ul className="engine-card-examples">
          <li>• Orphan after N days</li>
          <li>• Duplicate similarity ≥ threshold</li>
          <li>• Spend change ≥ X%</li>
        </ul>
      </div>
      <div className="engine-card">
        <div className="engine-card-header">
          <span className="engine-card-icon">🧠</span>
          <span className="engine-card-title">LLM Rules</span>
          <span className="engine-card-badge llm">AI-Powered</span>
        </div>
        <div className="engine-card-desc">Use an LLM to analyze documents, detect anomalies, or classify content. Best for fuzzy/contextual rules.</div>
        <ul className="engine-card-examples">
          <li>• "Does this bill look inflated?"</li>
          <li>• "Summarize coverage changes"</li>
          <li>• "Classify unknown doc type"</li>
        </ul>
      </div>
      <div className="engine-card">
        <div className="engine-card-header">
          <span className="engine-card-icon">🔗</span>
          <span className="engine-card-title">n8n Workflow Rules</span>
          <span className="engine-card-badge n8n">External</span>
        </div>
        <div className="engine-card-desc">Trigger an n8n workflow for complex multi-step analysis, external API calls, or custom integrations.</div>
        <ul className="engine-card-examples">
          <li>• Cross-reference with Monarch</li>
          <li>• Call provider price-check API</li>
          <li>• Multi-doc summary pipeline</li>
        </ul>
      </div>
    </div>
  );
}

function RuleTypeTabs({ filter, counts, onChange }: { filter: TabFilter; counts: Record<TabFilter, number>; onChange: (f: TabFilter) => void }) {
  const tabs: { key: TabFilter; icon: string; label: string }[] = [
    { key: 'all', icon: '📋', label: 'All Rules' },
    { key: 'basic', icon: '⚙️', label: 'Basic' },
    { key: 'llm', icon: '🧠', label: 'LLM' },
    { key: 'n8n', icon: '🔗', label: 'n8n' },
  ];
  return (
    <div className="rule-tabs">
      {tabs.map((t) => (
        <button key={t.key} className={`rule-tab ${filter === t.key ? 'active' : ''}`} onClick={() => onChange(t.key)}>
          <span>{t.icon}</span> {t.label} <span className="tab-count">{counts[t.key]}</span>
        </button>
      ))}
    </div>
  );
}

function Toggle({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <button
      className={`rule-toggle ${on ? 'on' : ''}`}
      onClick={(e) => { e.stopPropagation(); onToggle(); }}
      aria-label={on ? 'Disable rule' : 'Enable rule'}
    >
      <div className="toggle-dot" />
    </button>
  );
}

function RuleRow({ rule, selected, onSelect, onToggle }: { rule: Rule; selected: boolean; onSelect: () => void; onToggle: () => void }) {
  return (
    <div className={`rule-row ${!rule.enabled ? 'disabled' : ''} ${selected ? 'selected' : ''}`} onClick={onSelect}>
      <Toggle on={rule.enabled} onToggle={onToggle} />
      <span className={`rule-type-badge ${rule.type}`}>
        {rule.type === 'basic' ? '⚙️ Basic' : rule.type === 'llm' ? '🧠 LLM' : '🔗 n8n'}
      </span>
      <div className="rule-info">
        <div className="rule-name">{rule.name}</div>
        <div className="rule-desc">{rule.description}</div>
      </div>
      <div className="rule-meta">
        <span className="rule-meta-item">{SCHEDULE_ICONS[rule.schedule]}</span>
        {rule.type === 'llm' && rule.costPerDoc && <span className="rule-meta-item">💰 {rule.costPerDoc}</span>}
        {rule.type === 'n8n' && rule.n8nStatus === 'error' && <span className="rule-meta-item" style={{ color: 'var(--danger)' }}>⚠ Workflow offline</span>}
        {rule.hitsThisMonth > 0 && <span className="rule-meta-item">📊 {rule.hitsThisMonth} hits this month</span>}
        {rule.hitsThisMonth === 0 && !(rule.type === 'n8n' && rule.n8nStatus === 'error') && (
          <span className="rule-meta-item">📊 0 hits</span>
        )}
        {rule.routeBadges?.map((b, i) => (
          <span key={i} className={`rule-route ${b.type}`}>{b.label}</span>
        ))}
      </div>
    </div>
  );
}

// ── Threshold Builder ──

function ThresholdBuilder({ conditions, onChange }: { conditions: ThresholdCondition[]; onChange: (c: ThresholdCondition[]) => void }) {
  const updateCondition = (idx: number, patch: Partial<ThresholdCondition>) => {
    const next = conditions.map((c, i) => i === idx ? { ...c, ...patch } : c);
    onChange(next);
  };
  const removeCondition = (idx: number) => onChange(conditions.filter((_, i) => i !== idx));
  const addCondition = () =>
    onChange([...conditions, { id: uid(), field: 'document_type', operator: '=', value: '', unit: 'count', conjunction: 'AND' }]);

  return (
    <div className="threshold-builder">
      {conditions.map((c, idx) => (
        <div key={c.id} className="threshold-row">
          <span style={{ fontWeight: 600, color: idx === 0 ? undefined : 'var(--accent)' }}>
            {c.conjunction}
          </span>
          <select className="threshold-field" value={c.field} onChange={(e) => updateCondition(idx, { field: e.target.value })}>
            {AVAILABLE_FIELDS.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <select className="threshold-field" style={{ width: 50 }} value={c.operator} onChange={(e) => updateCondition(idx, { operator: e.target.value })}>
            {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          <input
            type="text"
            className="threshold-field"
            style={{ width: 60 }}
            value={c.value}
            onChange={(e) => updateCondition(idx, { value: e.target.value })}
          />
          <select className="threshold-field" style={{ width: 60 }} value={c.unit} onChange={(e) => updateCondition(idx, { unit: e.target.value })}>
            {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
          {idx > 0 && <button className="threshold-remove" onClick={() => removeCondition(idx)}>✕</button>}
        </div>
      ))}
      <button className="threshold-add" onClick={addCondition}>+ Add condition</button>
    </div>
  );
}

// ── LLM Editor ──

function LLMEditor({ rule, onChange }: { rule: Rule; onChange: (patch: Partial<Rule>) => void }) {
  return (
    <>
      <div className="editor-section">
        <div className="editor-label">LLM Prompt Template</div>
        <textarea
          className="llm-prompt-area"
          value={rule.promptTemplate ?? ''}
          onChange={(e) => onChange({ promptTemplate: e.target.value })}
          rows={10}
        />
        <div className="llm-model-row">
          <span style={{ fontSize: 11, fontWeight: 600 }}>Model:</span>
          <select
            className="editor-select"
            style={{ padding: '4px 8px', fontSize: 11 }}
            value={rule.model ?? 'gpt-4o-mini'}
            onChange={(e) => onChange({ model: e.target.value })}
          >
            {MODELS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
          <span className="llm-model-badge">{rule.costPerDoc ?? '~$0.01/doc'}</span>
          <span className="llm-cost-est">Est. $0.40/month at current volume</span>
        </div>
        <div style={{ marginTop: 8 }}>
          <div className="editor-label">Available Variables</div>
          <div className="llm-vars">
            {LLM_VARIABLES.map((v) => (
              <button
                key={v}
                className="llm-var-chip"
                onClick={() => onChange({ promptTemplate: (rule.promptTemplate ?? '') + ' ' + v })}
                title={`Insert ${v}`}
              >
                {v}
              </button>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

// ── n8n Editor ──

function N8nEditor({ rule, onChange }: { rule: Rule; onChange: (patch: Partial<Rule>) => void }) {
  const [testing, setTesting] = useState(false);

  const handleTest = () => {
    setTesting(true);
    setTimeout(() => {
      onChange({ n8nStatus: rule.webhookUrl ? 'connected' : 'error' });
      setTesting(false);
    }, 1200);
  };

  const NODE_ICONS: Record<string, string> = { trigger: '🔴', process: '📄', llm: '🧠', output: '✅' };

  return (
    <div className="editor-section">
      <div className="editor-label">n8n Workflow Configuration</div>
      <div className="n8n-config">
        <div className="n8n-url-row">
          <span style={{ fontSize: 11, fontWeight: 600 }}>Webhook URL:</span>
          <input
            type="text"
            className="n8n-url-input"
            value={rule.webhookUrl ?? ''}
            onChange={(e) => onChange({ webhookUrl: e.target.value })}
            placeholder="https://n8n.example.com/webhook/..."
          />
          <Button size="sm" onClick={handleTest} disabled={testing}>
            {testing ? '⏳ Testing…' : '🔍 Test'}
          </Button>
        </div>
        <div className="n8n-status">
          <span className={`n8n-status-dot ${rule.n8nStatus === 'connected' ? 'connected' : rule.n8nStatus === 'error' ? 'error' : ''}`} />
          {rule.n8nStatus === 'connected' && (
            <>
              <span style={{ color: 'var(--success)', fontWeight: 600 }}>Connected</span>
              {rule.n8nLastTriggered && <span style={{ color: 'var(--text-muted)' }}>— Last triggered {rule.n8nLastTriggered}</span>}
            </>
          )}
          {rule.n8nStatus === 'error' && <span style={{ color: 'var(--danger)', fontWeight: 600 }}>Connection failed</span>}
          {rule.n8nStatus === 'unknown' && <span style={{ color: 'var(--text-muted)' }}>Not tested yet</span>}
        </div>
        {rule.n8nFlowNodes && rule.n8nFlowNodes.length > 0 && (
          <div className="n8n-flow-preview">
            <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', marginBottom: 6 }}>WORKFLOW PREVIEW:</div>
            {rule.n8nFlowNodes.map((node, i) => (
              <span key={i}>
                {i > 0 && <span className="n8n-arrow">→</span>}
                <span className={`n8n-node ${node.type}`}>{NODE_ICONS[node.type]} {node.label}</span>
              </span>
            ))}
          </div>
        )}
        <div style={{ marginTop: 8, fontSize: 10, color: 'var(--text-muted)' }}>
          💡 The n8n workflow receives document data via webhook and returns a JSON result. DI routes the result based on your routing config below.
        </div>
      </div>
    </div>
  );
}

// ── Rule Editor Panel ──

function RuleEditor({
  rule,
  isNew,
  onChange,
  onSave,
  onDelete,
  confirmingDelete,
  onCancel,
  onTest,
}: {
  rule: Rule;
  isNew: boolean;
  onChange: (patch: Partial<Rule>) => void;
  onSave: () => void;
  onDelete: () => void;
  confirmingDelete: boolean;
  onCancel: () => void;
  onTest: () => void;
}) {
  const handleTypeChange = (newType: RuleType) => {
    const patch: Partial<Rule> = { type: newType };
    if (newType === 'basic' && !rule.conditions?.length) {
      patch.conditions = [{ id: uid(), field: 'extraction_confidence', operator: '≥', value: '60', unit: '%', conjunction: 'IF' }];
    }
    if (newType === 'llm' && rule.promptTemplate === undefined) {
      patch.promptTemplate = '';
      patch.model = 'gpt-4o-mini';
    }
    if (newType === 'n8n' && rule.webhookUrl === undefined) {
      patch.webhookUrl = '';
      patch.n8nStatus = 'unknown';
    }
    onChange(patch);
  };

  return (
    <div className="rule-editor">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div style={{ fontSize: 16, fontWeight: 700 }}>
          {isNew ? '➕ New Rule' : `✏️ Edit Rule: ${rule.name}`}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <Button onClick={onTest}>🧪 Test Rule</Button>
          {!isNew && (
            <Button variant="danger" onClick={onDelete}>
              {confirmingDelete ? '⚠️ Confirm Delete' : '🗑️ Delete'}
            </Button>
          )}
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant="success" onClick={onSave}>💾 Save Rule</Button>
        </div>
      </div>

      {/* Name + Type */}
      <div className="editor-row" style={{ marginBottom: 16 }}>
        <div className="editor-section">
          <div className="editor-label">Rule Name</div>
          <input
            type="text"
            className="editor-input"
            value={rule.name}
            onChange={(e) => onChange({ name: e.target.value })}
            placeholder="Enter rule name…"
          />
        </div>
        <div className="editor-section">
          <div className="editor-label">Rule Type</div>
          <select
            className="editor-select editor-input"
            value={rule.type}
            onChange={(e) => handleTypeChange(e.target.value as RuleType)}
          >
            <option value="basic">⚙️ Basic — Threshold / Field comparison</option>
            <option value="llm">🧠 LLM — AI-powered analysis</option>
            <option value="n8n">🔗 n8n — External workflow</option>
          </select>
        </div>
      </div>

      {/* Description */}
      <div className="editor-section">
        <div className="editor-label">Description</div>
        <input
          type="text"
          className="editor-input"
          value={rule.description}
          onChange={(e) => onChange({ description: e.target.value })}
          placeholder="What does this rule check for?"
        />
      </div>

      {/* Type-specific config */}
      {rule.type === 'basic' && (
        <div className="editor-section">
          <div className="editor-label">Conditions</div>
          <ThresholdBuilder
            conditions={rule.conditions ?? []}
            onChange={(conditions) => onChange({ conditions })}
          />
        </div>
      )}
      {rule.type === 'llm' && <LLMEditor rule={rule} onChange={onChange} />}
      {rule.type === 'n8n' && <N8nEditor rule={rule} onChange={onChange} />}

      {/* Schedule + Applies To */}
      <div className="editor-row" style={{ marginTop: 16 }}>
        <div className="editor-section">
          <div className="editor-label">Schedule</div>
          <select
            className="editor-select editor-input"
            value={rule.schedule}
            onChange={(e) => onChange({ schedule: e.target.value as Schedule })}
          >
            {Object.entries(SCHEDULE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div className="editor-section">
          <div className="editor-label">Applies To</div>
          <select
            className="editor-select editor-input"
            value={rule.appliesTo}
            onChange={(e) => onChange({ appliesTo: e.target.value as AppliesTo })}
          >
            {Object.entries(APPLIES_TO_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
      </div>

      {/* Routing */}
      <div className="editor-section">
        <div className="editor-label">Default Routing</div>
        <div className="route-config">
          {([
            { key: 'insight' as RouteTarget, icon: '📊', label: 'Insight Only', desc: 'Show in DI Insights tab. No action needed.' },
            { key: 'triage' as RouteTarget, icon: '📥', label: 'Needs Review', desc: 'Ask for human review before proceeding.' },
            { key: 'mc' as RouteTarget, icon: '🚨', label: 'Mission Control', desc: 'Create MC alert for immediate action.' },
          ]).map((r) => (
            <div
              key={r.key}
              className={`route-option ${rule.routing === r.key ? 'selected' : ''}`}
              onClick={() => onChange({ routing: r.key })}
            >
              <span className="route-label">{r.icon} {r.label}</span>
              <span className="route-desc">{r.desc}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Escalation */}
      <div className="editor-section">
        <div className="editor-label">Escalation (Optional)</div>
        <div className="escalation-row">
          <span className="esc-label">⬆️ Escalate to</span>
          <select
            className="editor-select"
            style={{ padding: '4px 8px', fontSize: 12 }}
            value={rule.escalation?.target ?? 'Mission Control alert'}
            onChange={(e) => onChange({ escalation: { ...rule.escalation, target: e.target.value, condition: rule.escalation?.condition ?? '' } })}
          >
            <option>Mission Control alert</option>
            <option>Needs Review (high priority)</option>
          </select>
          <span style={{ fontSize: 12 }}>when</span>
          <input
            type="text"
            className="threshold-field"
            style={{ width: 180 }}
            value={rule.escalation?.condition ?? ''}
            onChange={(e) => onChange({ escalation: { target: rule.escalation?.target ?? 'Mission Control alert', condition: e.target.value } })}
            placeholder="e.g. confidence ≥ 0.8"
          />
        </div>
      </div>
    </div>
  );
}

// ── Test Rule Modal ──

function TestRuleModal({ rule, onClose }: { rule: Rule; onClose: () => void }) {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<{ pass: boolean; output: string } | null>(null);

  const handleRun = () => {
    setRunning(true);
    setResult(null);
    setTimeout(() => {
      const pass = Math.random() > 0.3;
      setResult({
        pass,
        output: pass
          ? JSON.stringify({ match: true, triggered: true, confidence: 0.87, routing: rule.routing, details: `Rule "${rule.name}" would fire for this sample document.` }, null, 2)
          : JSON.stringify({ match: false, triggered: false, confidence: 0.23, details: `Rule "${rule.name}" would NOT fire — conditions not met.` }, null, 2),
      });
      setRunning(false);
    }, 1500);
  };

  return (
    <div className="test-rule-overlay" onClick={onClose}>
      <div className="test-rule-panel" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <div style={{ fontSize: 16, fontWeight: 700 }}>🧪 Test Rule: {rule.name}</div>
          <button className="btn" onClick={onClose} style={{ background: 'none', border: 'none', fontSize: 18, cursor: 'pointer' }}>✕</button>
        </div>
        <div className="editor-section">
          <div className="editor-label">Sample Document ID (optional)</div>
          <input type="text" className="editor-input" placeholder="Enter a Paperless document ID or leave blank for mock data…" />
        </div>
        <div className="editor-section">
          <div className="editor-label">Sample Data Override (JSON)</div>
          <textarea
            className="editor-input"
            rows={5}
            defaultValue={JSON.stringify({ provider_name: 'Acme Medical Group', billed_amount: 2450.00, description: 'MRI Brain w/o Contrast', region: 'Northeast', date_of_service: '2024-01-15' }, null, 2)}
          />
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <Button variant="primary" onClick={handleRun} disabled={running}>
            {running ? '⏳ Running…' : '▶️ Run Test'}
          </Button>
          <Button onClick={onClose}>Close</Button>
        </div>
        {result && (
          <div className={`test-result ${result.pass ? 'pass' : 'fail'}`}>
            <div style={{ fontWeight: 700, marginBottom: 4 }}>{result.pass ? '✅ Rule TRIGGERED' : '❌ Rule did NOT trigger'}</div>
            {result.output}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main Page ──

export default function RulesConfig() {
  const [rules, setRules] = useState<Rule[]>(SEED_RULES);
  const [filter, setFilter] = useState<TabFilter>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editingRule, setEditingRule] = useState<Rule | null>(null);
  const [isNewRule, setIsNewRule] = useState(false);
  const [testingRule, setTestingRule] = useState<Rule | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  const editorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  useEffect(() => {
    if (editingRule && editorRef.current) {
      editorRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [editingRule?.id, isNewRule]);

  const counts = useMemo<Record<TabFilter, number>>(() => ({
    all: rules.length,
    basic: rules.filter((r) => r.type === 'basic').length,
    llm: rules.filter((r) => r.type === 'llm').length,
    n8n: rules.filter((r) => r.type === 'n8n').length,
  }), [rules]);

  const filteredRules = useMemo(() =>
    filter === 'all' ? rules : rules.filter((r) => r.type === filter),
    [rules, filter],
  );

  const toggleRule = useCallback((id: string) => {
    setRules((prev) => prev.map((r) => r.id === id ? { ...r, enabled: !r.enabled } : r));
  }, []);

  const selectRule = useCallback((id: string) => {
    setSelectedId(id);
    const found = rules.find((r) => r.id === id);
    if (found) {
      setEditingRule({
        ...found,
        conditions: found.conditions?.map((c) => ({ ...c })),
        n8nFlowNodes: found.n8nFlowNodes?.map((n) => ({ ...n })),
        routeBadges: found.routeBadges?.map((b) => ({ ...b })),
      });
      setIsNewRule(false);
    }
  }, [rules]);

  const handleNewRule = useCallback(() => {
    const nr = emptyRule('basic');
    setEditingRule(nr);
    setIsNewRule(true);
    setSelectedId(null);
  }, []);

  const handleEditorChange = useCallback((patch: Partial<Rule>) => {
    setEditingRule((prev) => prev ? { ...prev, ...patch } : prev);
  }, []);

  const handleSave = useCallback(() => {
    if (!editingRule) return;
    if (!editingRule.name.trim()) {
      setToast({ message: 'Rule name is required.', tone: 'error' });
      return;
    }
    if (isNewRule) {
      setRules((prev) => [...prev, editingRule]);
      setToast({ message: `Rule "${editingRule.name}" created.`, tone: 'success' });
    } else {
      setRules((prev) => prev.map((r) => r.id === editingRule.id ? editingRule : r));
      setToast({ message: `Rule "${editingRule.name}" updated.`, tone: 'success' });
    }
    setSelectedId(editingRule.id);
    setEditingRule(null);
    setIsNewRule(false);
  }, [editingRule, isNewRule]);

  const handleDelete = useCallback(() => {
    if (!editingRule) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setRules((prev) => prev.filter((r) => r.id !== editingRule.id));
    setToast({ message: `Rule "${editingRule.name}" deleted.`, tone: 'success' });
    setEditingRule(null);
    setSelectedId(null);
    setIsNewRule(false);
    setConfirmDelete(false);
  }, [editingRule, confirmDelete]);

  const handleCancel = useCallback(() => {
    setEditingRule(null);
    setIsNewRule(false);
    setConfirmDelete(false);
  }, []);

  const handleTest = useCallback(() => {
    if (editingRule) setTestingRule(editingRule);
  }, [editingRule]);

  return (
    <>
      <PageHeader
        title="Analysis Rules"
        desc="Configure rules that detect issues, generate insights, and route items to triage or Mission Control."
        actions={
          <div className="btn-group">
            <Button onClick={handleNewRule}>+ New Rule</Button>
          </div>
        }
      />

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />}

      <EngineCards />

      <RuleTypeTabs filter={filter} counts={counts} onChange={setFilter} />

      <div className="rules-list">
        {filteredRules.map((rule) => (
          <RuleRow
            key={rule.id}
            rule={rule}
            selected={selectedId === rule.id}
            onSelect={() => selectRule(rule.id)}
            onToggle={() => toggleRule(rule.id)}
          />
        ))}
        {filteredRules.length === 0 && (
          <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)' }}>
            No {filter !== 'all' ? filter : ''} rules configured yet.
          </div>
        )}
      </div>

      {editingRule && (
        <div ref={editorRef}>
          <RuleEditor
            rule={editingRule}
            isNew={isNewRule}
            onChange={handleEditorChange}
            onSave={handleSave}
            onDelete={handleDelete}
            confirmingDelete={confirmDelete}
            onCancel={handleCancel}
            onTest={handleTest}
          />
        </div>
      )}

      {testingRule && <TestRuleModal rule={testingRule} onClose={() => setTestingRule(null)} />}
    </>
  );
}
