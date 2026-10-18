export type DocumentActionType = 'PAY' | 'RESPOND' | 'REVIEW' | 'SCHEDULE' | 'SHARE' | 'SIGN' | 'TASK';
export type DocumentActionStatus = 'completed' | 'dismissed' | 'pending' | 'acknowledged' | 'snoozed' | 'not_an_action';

export interface DocumentAction {
  id: number;
  document_id?: number | null;
  document_title?: string | null;
  action_type?: string | null;
  title?: string | null;
  summary?: string | null;
  payee_or_owner?: string | null;
  reference?: string | null;
  due_date?: string | null;
  amount?: number | null;
  confidence?: number | null;
  status?: string | null;
  correspondent?: string | null;
  document_date?: string | null;
  extracted_data?: {
    payment_url?: string | null;
    phone?: string | null;
    email?: string | null;
    reference_number?: string | null;
    links?: Array<{ url: string; label?: string | null; purpose?: string | null }> | null;
  } | null;
  ai_reasoning?: string | null;
  recommended_cta?: {
    id: string;
    label: string;
    url?: string | null;
    phone?: string | null;
    metadata?: Record<string, unknown>;
  } | null;
  version?: number | null;
  is_primary?: boolean;
  origin?: 'inferred' | 'user';
  obligation_id?: string | null;
  history?: Array<{
    id: number;
    event_type?: string | null;
    previous_status?: string | null;
    new_status?: string | null;
    created_at?: string | null;
  }>;
}

export interface DocumentActionDraft {
  action_type: DocumentActionType;
  title: string;
  summary: string;
  payee_or_owner: string;
  amount: string;
  due_date: string;
  reference: string;
}

export const DOCUMENT_ACTION_TYPES: DocumentActionType[] = ['PAY', 'RESPOND', 'REVIEW', 'SCHEDULE', 'SHARE', 'SIGN', 'TASK'];

export const DOCUMENT_ACTION_FIELD_SCHEMA: Record<DocumentActionType, {
  owner?: string;
  due: string;
  reference?: string;
  amount: boolean;
}> = {
  PAY: { owner: 'Payee', due: 'Pay by', reference: 'Invoice or reference', amount: true },
  RESPOND: { owner: 'Recipient', due: 'Respond by', reference: 'Case or reference', amount: false },
  REVIEW: { due: 'Review by', amount: false },
  SCHEDULE: { owner: 'With or provider', due: 'Event date', reference: 'Confirmation or reference', amount: false },
  SHARE: { owner: 'Recipient', due: 'Share by', amount: false },
  SIGN: { owner: 'Counterparty or recipient', due: 'Sign by', reference: 'Agreement or reference', amount: false },
  TASK: { owner: 'Owner', due: 'Due date', reference: 'Reference', amount: false },
};

export function normalizeDocumentActionType(value?: string | null): DocumentActionType {
  const normalized = (value || 'TASK').toUpperCase();
  return DOCUMENT_ACTION_TYPES.includes(normalized as DocumentActionType) ? normalized as DocumentActionType : 'TASK';
}

export function documentActionDraft(action?: DocumentAction | null): DocumentActionDraft {
  return {
    action_type: normalizeDocumentActionType(action?.action_type),
    title: action?.title || '',
    summary: action?.summary || '',
    payee_or_owner: action?.payee_or_owner || action?.correspondent || '',
    amount: action?.amount == null ? '' : String(action.amount),
    due_date: action?.due_date?.slice(0, 10) || '',
    reference: action?.reference || action?.extracted_data?.reference_number || '',
  };
}

export function documentActionMutation(draft: DocumentActionDraft) {
  const schema = DOCUMENT_ACTION_FIELD_SCHEMA[draft.action_type];
  return {
    action_type: draft.action_type,
    title: draft.title.trim(),
    summary: draft.summary.trim() || null,
    payee_or_owner: schema.owner ? draft.payee_or_owner.trim() || null : null,
    amount: schema.amount && draft.amount ? Number(draft.amount) : null,
    due_date: draft.due_date || null,
    reference: schema.reference ? draft.reference.trim() || null : null,
  };
}
