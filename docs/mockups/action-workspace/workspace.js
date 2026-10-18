const actions = [
  {
    id: 1043,
    title: 'Pay Cedar Dental balance',
    document: 'Cedar Dental · patient balance',
    correspondent: 'Cedar Dental',
    type: 'PAY',
    documentType: 'Invoice',
    date: 'Oct 15, 2026',
    due: 'Oct 30, 2026',
    dueValue: '2026-10-30',
    dueLabel: 'Due in 13 days',
    dueRank: 13,
    amount: 248,
    urgency: 'High',
    confidence: 94,
    reference: 'ACCT-4821',
    summary: 'A patient statement shows an unpaid balance of $248.00 due October 30.',
    reason: 'The statement includes “balance due,” a future due date, and a payment URL. No receipt or payment confirmation was found.',
    paperKind: 'PATIENT STATEMENT',
    paperTitle: 'Your account has a balance due.',
    paperDescription: 'Please submit payment for the services listed below by October 30, 2026.',
    paperCallout: 'PAYMENT DUE · OCTOBER 30',
    color: '#2c6c9e',
    quick: [['Pay online', 'primary'], ['Call (555) 014-8821', 'phone']],
  },
  {
    id: 1061,
    title: 'Respond to Harbor Bank verification',
    document: 'Harbor Bank · identity verification',
    correspondent: 'Harbor Bank',
    type: 'RESPOND',
    documentType: 'Letter',
    date: 'Oct 16, 2026',
    due: 'Oct 20, 2026',
    dueValue: '2026-10-20',
    dueLabel: 'Due in 3 days',
    dueRank: 3,
    amount: null,
    urgency: 'High',
    confidence: 91,
    reference: 'CASE-91KQ',
    summary: 'Harbor Bank requests confirmation of account activity within five days.',
    reason: 'The letter explicitly requests a response and includes a case number, callback phone number, and response deadline.',
    paperKind: 'ACCOUNT VERIFICATION',
    paperTitle: 'Please confirm recent account activity.',
    paperDescription: 'Contact our verification team by October 20, 2026 and reference the case number below.',
    paperCallout: 'RESPONSE REQUESTED · CASE-91KQ',
    color: '#314d7d',
    quick: [['Call verification', 'primary'], ['Open secure message', 'link']],
  },
  {
    id: 1038,
    title: 'Sign school consent form',
    document: 'Northwood School · field trip consent',
    correspondent: 'Northwood School',
    type: 'SIGN',
    documentType: 'Form',
    date: 'Oct 12, 2026',
    due: 'Oct 18, 2026',
    dueValue: '2026-10-18',
    dueLabel: 'Due tomorrow',
    dueRank: 1,
    amount: null,
    urgency: 'High',
    confidence: 97,
    reference: 'TRIP-6B',
    summary: 'A parent signature is required before the class trip registration closes.',
    reason: 'The form contains a signature line, explicit consent language, and a submission deadline.',
    paperKind: 'PARENT CONSENT FORM',
    paperTitle: 'Field trip permission is required.',
    paperDescription: 'Sign and return this form with the $35.00 trip fee before October 18, 2026 for student participation.',
    paperCallout: 'SIGNATURE & $35 FEE REQUIRED',
    color: '#795a9d',
    quick: [['Open signing page', 'primary'], ['Email coordinator', 'email']],
  },
  {
    id: 2038,
    paperlessId: 1038,
    title: 'Pay school field trip fee',
    document: 'Northwood School · field trip consent',
    correspondent: 'Northwood School',
    type: 'PAY',
    documentType: 'Form',
    date: 'Oct 12, 2026',
    due: 'Oct 18, 2026',
    dueValue: '2026-10-18',
    dueLabel: 'Due tomorrow',
    dueRank: 1,
    amount: 35,
    urgency: 'High',
    confidence: 95,
    reference: 'TRIP-6B',
    summary: 'A $35.00 participation fee must accompany the signed field trip consent form.',
    reason: 'The same source document contains a required signature and a separately actionable participation fee.',
    paperKind: 'PARENT CONSENT FORM',
    paperTitle: 'Field trip permission and payment are required.',
    paperDescription: 'Sign and return this form with the $35.00 trip fee before October 18, 2026 for student participation.',
    paperCallout: 'SIGNATURE & $35 FEE REQUIRED',
    color: '#795a9d',
    quick: [['Pay trip fee', 'primary'], ['Email coordinator', 'email']],
  },
  {
    id: 1029,
    title: 'Review home insurance renewal',
    document: 'Pine Mutual · renewal notice',
    correspondent: 'Pine Mutual',
    type: 'REVIEW',
    documentType: 'Policy notice',
    date: 'Oct 8, 2026',
    due: 'Oct 16, 2026',
    dueValue: '2026-10-16',
    dueLabel: 'Overdue 1 day',
    dueRank: -1,
    amount: 1380,
    urgency: 'Critical',
    confidence: 86,
    reference: 'POL-77291',
    summary: 'The annual premium and coverage terms changed before automatic renewal.',
    reason: 'The notice describes material premium and deductible changes but does not explicitly require acceptance.',
    paperKind: 'POLICY RENEWAL NOTICE',
    paperTitle: 'Your coverage renews with updated terms.',
    paperDescription: 'Review the enclosed premium, deductible, and coverage changes before the renewal date.',
    paperCallout: 'RENEWAL DATE · OCTOBER 16',
    color: '#8f483f',
    quick: [['Review policy portal', 'primary'], ['Call agent', 'phone']],
  },
  {
    id: 1017,
    title: 'Schedule annual HVAC service',
    document: 'Lakeview Heating · maintenance reminder',
    correspondent: 'Lakeview Heating',
    type: 'SCHEDULE',
    documentType: 'Reminder',
    date: 'Oct 3, 2026',
    due: 'Nov 4, 2026',
    dueValue: '2026-11-04',
    dueLabel: 'Due in 18 days',
    dueRank: 18,
    amount: null,
    urgency: 'Medium',
    confidence: 89,
    reference: 'PLAN-203',
    summary: 'The annual maintenance window is open and appointment slots are available.',
    reason: 'The reminder recommends booking service and includes a scheduling link, but no fixed appointment exists.',
    paperKind: 'MAINTENANCE REMINDER',
    paperTitle: 'It is time to schedule annual service.',
    paperDescription: 'Choose an appointment before the seasonal maintenance window closes.',
    paperCallout: 'BOOK SERVICE · PLAN-203',
    color: '#28766c',
    quick: [['Schedule service', 'primary'], ['Call service desk', 'phone']],
  },
];

const reviewItems = [
  {
    id: 'review-action-1',
    category: 'action',
    kind: 'Action classification',
    title: 'Is this payment evidence or an unpaid balance?',
    document: 'Northline Energy · payment notice',
    correspondent: 'Northline Energy',
    documentType: 'Payment notice',
    reason: 'Conflicting payment signals',
    reasonCopy: 'The document says “payment received” and “remaining balance due.” OWL cannot safely decide whether this is evidence or an unpaid obligation.',
    confidence: 62,
    ageDays: 2,
    age: '2 days',
    priority: 'High',
    color: '#9b6411',
    paperKind: 'PAYMENT NOTICE',
    paperTitle: 'We received your partial payment.',
    paperDescription: 'A payment of $40.00 was received. Please pay the remaining $84.20 by October 24, 2026.',
    paperCallout: 'PAYMENT RECEIVED · BALANCE REMAINS',
    proposedActions: [
      {
        type: 'PAY',
        title: 'Pay remaining Northline balance',
        summary: 'Pay the remaining $84.20 balance after the documented $40.00 partial payment.',
        due: '2026-10-24',
        amount: '84.20',
      },
      {
        type: 'REVIEW',
        title: 'Review partial payment allocation',
        summary: 'Confirm that the recorded $40.00 payment was applied to the correct Northline Energy account.',
        due: '2026-10-20',
        amount: '',
      },
    ],
  },
  {
    id: 'review-eob-1',
    category: 'relationship',
    kind: 'EOB match',
    title: 'Does this EOB belong to the City Medical bill?',
    document: 'UHC EOB ↔ City Medical bill',
    correspondent: 'City Medical',
    documentType: 'EOB and bill',
    reason: 'Patient-responsibility amounts differ',
    reasonCopy: 'Dates and provider align, but the EOB says $36.00 while the bill requests $42.00.',
    confidence: 72,
    ageDays: 3,
    age: '3 days',
    priority: 'High',
    color: '#6e53a6',
    paperKind: 'RELATIONSHIP REVIEW',
  },
  {
    id: 'review-duplicate-1',
    category: 'relationship',
    kind: 'Possible duplicate',
    title: 'Are these Harbor Bank letters duplicates?',
    document: 'Harbor Bank · two verification letters',
    correspondent: 'Harbor Bank',
    documentType: 'Letters',
    reason: 'Same case number, different scan dates',
    reasonCopy: 'The content is nearly identical, but one copy contains a handwritten annotation.',
    confidence: 79,
    ageDays: 1,
    age: '1 day',
    priority: 'Medium',
    color: '#3e6790',
    paperKind: 'DUPLICATE REVIEW',
  },
  {
    id: 'review-grouping-1',
    category: 'relationship',
    kind: 'Grouping anomaly',
    title: 'Should two Chase accounts share one statement series?',
    document: 'Chase · account series',
    correspondent: 'Chase',
    documentType: 'Statements',
    reason: 'Two masked account identifiers detected',
    reasonCopy: 'Documents ending 4321 and 8876 were grouped together despite distinct account identifiers.',
    confidence: 68,
    ageDays: 4,
    age: '4 days',
    priority: 'High',
    color: '#28766c',
    paperKind: 'GROUPING REVIEW',
  },
  {
    id: 'review-orphan-1',
    category: 'relationship',
    kind: 'Orphan document',
    title: 'Find evidence for an unmatched Lab Corp bill',
    document: 'Lab Corp · unmatched invoice',
    correspondent: 'Lab Corp',
    documentType: 'Invoice',
    reason: 'No EOB found after 45 days',
    reasonCopy: 'The bill has no matched explanation of benefits or payment evidence.',
    confidence: 41,
    ageDays: 7,
    age: '7 days',
    priority: 'Medium',
    color: '#9a5b07',
    paperKind: 'ORPHAN REVIEW',
  },
];

let visibleActions = [...actions];
let selectedId = actions[0].id;
let activeFilter = 'active';
let actionTypeFilter = 'all';
let actionStatusFilter = 'pending';
let actionDirty = false;
const resolvedActions = new Map();
let pendingConfirmationId = null;
let pendingBulkDoneIds = [];
let visibleReviews = [...reviewItems];
let selectedReviewId = reviewItems[0].id;
let selectedReviewActionIndex = 0;
let reviewFilter = 'all';
let reviewStatus = 'pending';
const resolvedReviews = new Map();

const actionFieldSchemas = {
  PAY: { owner: 'Payee', due: 'Pay by', reference: 'Invoice or reference', amount: true },
  RESPOND: { owner: 'Recipient', due: 'Respond by', reference: 'Case or reference', amount: false },
  REVIEW: { due: 'Review by', amount: false },
  SCHEDULE: { owner: 'With or provider', due: 'Event date', reference: 'Confirmation or reference', amount: false },
  SHARE: { owner: 'Recipient', due: 'Share by', amount: false },
  SIGN: { owner: 'Counterparty or recipient', due: 'Sign by', reference: 'Agreement or reference', amount: false },
  TASK: { owner: 'Owner', due: 'Due date', reference: 'Reference', amount: false },
};

const list = document.querySelector('#queue-list');
const toast = document.querySelector('#toast');
const reviewList = document.querySelector('#review-list');

function money(value) {
  return value == null ? '—' : value.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}

function documentKey(action) {
  return action.paperlessId || action.id;
}

function documentGroups(actionList = visibleActions) {
  const groups = new Map();
  actionList.forEach((action) => {
    const key = documentKey(action);
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        primary: action,
        matchingActions: [],
        allActions: actions.filter((candidate) => documentKey(candidate) === key),
      });
    }
    groups.get(key).matchingActions.push(action);
  });
  return [...groups.values()];
}

function queueTemplate(group) {
  const action = group.primary;
  const selectedAction = actions.find((candidate) => candidate.id === selectedId);
  const selected = selectedAction && documentKey(selectedAction) === group.key ? ' active' : '';
  const overdue = group.matchingActions.some((candidate) => candidate.dueRank < 0) ? ' overdue' : '';
  const actionTypes = [...new Set(group.allActions.map((candidate) => candidate.type))].join(' + ');
  const pendingCount = group.allActions.filter((candidate) => !resolvedActions.has(candidate.id)).length;
  const deferredCount = group.allActions.filter((candidate) => resolvedActions.get(candidate.id) === 'snoozed').length;
  const resolvedCount = group.allActions.length - pendingCount - deferredCount;
  const lifecycle = [
    pendingCount ? `${pendingCount} pending` : '',
    deferredCount ? `${deferredCount} reminder${deferredCount === 1 ? '' : 's'}` : '',
    resolvedCount ? `${resolvedCount} resolved` : '',
  ].filter(Boolean).join(' · ');
  const amount = group.matchingActions.reduce((sum, candidate) => sum + (candidate.amount || 0), 0);
  return `<button class="queue-item${selected}" type="button" data-id="${action.id}" data-document-key="${group.key}" aria-current="${selected ? 'true' : 'false'}">
    <img class="queue-thumb" src="${thumbnailData(action)}" alt="">
    <span class="queue-copy">
      <strong>${action.document}</strong>
      <span>${action.correspondent} · ${action.documentType}</span>
      <span class="queue-sibling">${group.allActions.length > 1 ? `${group.allActions.length} actions · ` : ''}${actionTypes}</span>
      <span>${lifecycle}</span>
      <span class="queue-meta"><em class="${overdue.trim()}">${action.dueLabel}</em><b class="queue-amount">${amount ? money(amount) : '—'}</b></span>
    </span>
    <span class="queue-chevron" aria-hidden="true">›</span>
  </button>`;
}

function thumbnailData(action) {
  const safeProvider = action.correspondent.replaceAll('&', '&amp;').slice(0, 18);
  const safeKind = action.paperKind.replaceAll('&', '&amp;').slice(0, 22);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="100" height="126" viewBox="0 0 100 126">
    <rect width="100" height="126" fill="#fff"/>
    <rect x="10" y="10" width="80" height="8" rx="1" fill="${action.color}"/>
    <text x="10" y="31" fill="#26384b" font-family="Arial,sans-serif" font-size="7" font-weight="700">${safeProvider}</text>
    <text x="10" y="42" fill="#69788a" font-family="Arial,sans-serif" font-size="5">${safeKind}</text>
    <rect x="10" y="52" width="74" height="3" rx="1" fill="#d8dfe6"/>
    <rect x="10" y="61" width="62" height="3" rx="1" fill="#d8dfe6"/>
    <rect x="10" y="70" width="78" height="3" rx="1" fill="#d8dfe6"/>
    <rect x="10" y="87" width="80" height="20" rx="2" fill="#eef4f8"/>
    <rect x="17" y="94" width="42" height="4" rx="1" fill="${action.color}"/>
  </svg>`;
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function matchesActionDueWindow(action) {
  return activeFilter === 'active'
    || (activeFilter === 'overdue' && action.dueRank < 0)
    || (activeFilter === 'upcoming' && action.dueRank > 14);
}

function renderQueue() {
  const groups = documentGroups();
  list.innerHTML = groups.length
    ? groups.map(queueTemplate).join('')
    : '<p style="padding:18px;color:#667388;font-size:.72rem">No documents contain actions matching these filters.</p>';
  const pendingActions = actions.filter((action) => !resolvedActions.has(action.id));
  const deferredActions = actions.filter((action) => resolvedActions.get(action.id) === 'snoozed');
  const completedActions = actions.filter((action) => resolvedActions.has(action.id) && resolvedActions.get(action.id) !== 'snoozed');
  const statusActions = actionStatusFilter === 'deferred'
    ? deferredActions
    : actionStatusFilter === 'resolved'
      ? completedActions
      : pendingActions;
  const typeScopedActions = actionTypeFilter === 'all'
    ? statusActions
    : statusActions.filter((action) => action.type === actionTypeFilter);
  const dueScopedActions = statusActions.filter(matchesActionDueWindow);
  const statusLabel = actionStatusFilter === 'deferred' ? 'reminder' : actionStatusFilter;
  document.querySelector('#queue-count').textContent = `${groups.length} document${groups.length === 1 ? '' : 's'} · ${visibleActions.length} ${statusLabel} action${visibleActions.length === 1 ? '' : 's'}`;
  document.querySelector('#action-nav-count').textContent = String(pendingActions.length);
  document.querySelector('[data-action-status="pending"] span').textContent = String(pendingActions.length);
  document.querySelector('[data-action-status="deferred"] span').textContent = String(deferredActions.length);
  document.querySelector('[data-action-status="resolved"] span').textContent = String(completedActions.length);
  document.querySelector('[data-filter="active"] span').textContent = String(typeScopedActions.length);
  document.querySelector('[data-filter="overdue"] span').textContent = String(typeScopedActions.filter((action) => action.dueRank < 0).length);
  document.querySelector('[data-filter="upcoming"] span').textContent = String(typeScopedActions.filter((action) => action.dueRank > 14).length);
  document.querySelectorAll('[data-action-type]').forEach((button) => {
    button.querySelector('span').textContent = String(
      dueScopedActions.filter((action) => button.dataset.actionType === 'all' || action.type === button.dataset.actionType).length
    );
  });
  list.querySelectorAll('[data-id]').forEach((button) => {
    button.addEventListener('click', () => selectAction(Number(button.dataset.id)));
  });
}

function reviewQueueTemplate(item) {
  const selected = item.id === selectedReviewId ? ' active' : '';
  const outcome = resolvedReviews.get(item.id);
  const lifecycle = outcome === 'defer' ? 'Review later' : outcome ? 'Resolved' : 'Pending';
  return `<button class="queue-item${selected}" type="button" data-review-id="${item.id}" aria-current="${item.id === selectedReviewId ? 'true' : 'false'}">
    <img class="queue-thumb" src="${thumbnailData({
      correspondent: item.correspondent,
      paperKind: item.paperKind,
      color: item.color,
    })}" alt="">
    <span class="queue-copy">
      <span class="review-item-top">
        <span class="review-kind${item.category === 'relationship' ? ' relationship' : ''}">${item.kind}</span>
        <span class="review-confidence">${item.confidence}%</span>
        <span class="review-confidence">${lifecycle}</span>
        ${item.priority === 'High' ? '<span class="review-priority">High</span>' : ''}
      </span>
      <strong>${item.title}</strong>
      <span class="review-item-reason">${item.reason}</span>
      <span class="review-item-age">Waiting ${item.age}</span>
    </span>
  </button>`;
}

function renderReviewQueue() {
  reviewList.innerHTML = visibleReviews.length
    ? visibleReviews.map(reviewQueueTemplate).join('')
    : '<p style="padding:18px;color:#667388;font-size:.72rem">No review items match these filters.</p>';
  const pendingReviews = reviewItems.filter((item) => !resolvedReviews.has(item.id));
  const deferredReviews = reviewItems.filter((item) => resolvedReviews.get(item.id) === 'defer');
  const completedReviews = reviewItems.filter((item) => resolvedReviews.has(item.id) && resolvedReviews.get(item.id) !== 'defer');
  const statusItems = reviewStatus === 'deferred' ? deferredReviews : reviewStatus === 'resolved' ? completedReviews : pendingReviews;
  const statusLabel = reviewStatus === 'deferred' ? 'review later' : reviewStatus;
  document.querySelector('#review-queue-count').textContent = `${visibleReviews.length} of ${statusItems.length} ${statusLabel}`;
  document.querySelector('#review-nav-count').textContent = String(pendingReviews.length);
  document.querySelector('[data-review-status="pending"] span').textContent = String(pendingReviews.length);
  document.querySelector('[data-review-status="deferred"] span').textContent = String(deferredReviews.length);
  document.querySelector('[data-review-status="resolved"] span').textContent = String(completedReviews.length);
  document.querySelector('[data-review-filter="all"] span').textContent = String(statusItems.length);
  document.querySelector('[data-review-filter="action"] span').textContent = String(statusItems.filter((item) => item.category === 'action').length);
  document.querySelector('[data-review-filter="relationship"] span').textContent = String(statusItems.filter((item) => item.category === 'relationship').length);
  reviewList.querySelectorAll('[data-review-id]').forEach((button) => {
    button.addEventListener('click', () => selectReview(button.dataset.reviewId));
  });
}

function singleReviewEvidence(item) {
  return `<article class="paper review-evidence-single">
    <div class="paper-provider">${item.correspondent.toUpperCase()}</div>
    <div class="paper-kind">${item.paperKind}</div>
    <h3>${item.paperTitle}</h3>
    <p>${item.paperDescription}</p>
    <div class="paper-rule"></div>
    <dl class="paper-details">
      <div><dt>Payment received</dt><dd>$40.00</dd></div>
      <div><dt>Account</dt><dd>•••• 4821</dd></div>
      <div><dt>Remaining balance</dt><dd>$84.20</dd></div>
      <div><dt>Due date</dt><dd>Oct 24, 2026</dd></div>
    </dl>
    <div class="paper-callout">${item.paperCallout}</div>
    <div class="paper-lines" aria-hidden="true"><i></i><i></i><i></i><i></i></div>
    <p class="paper-note">Synthetic document created for interaction design review.</p>
  </article>`;
}

function relationshipEvidence(item) {
  const presentations = {
    'EOB match': {
      leftTitle: 'United Healthcare EOB',
      rightTitle: 'City Medical bill',
      leftDate: 'Sep 28, 2026',
      rightDate: 'Oct 2, 2026',
      leftLabel: 'Patient responsibility',
      rightLabel: 'Bill balance',
      leftValue: '$36.00',
      rightValue: '$42.00',
      rightCopy: 'Dates and provider align, but the billed amount differs from the EOB responsibility.',
    },
    'Possible duplicate': {
      leftTitle: 'Harbor Bank letter · original',
      rightTitle: 'Harbor Bank letter · annotated scan',
      leftDate: 'Oct 11, 2026',
      rightDate: 'Oct 14, 2026',
      leftLabel: 'Content fingerprint',
      rightLabel: 'Difference',
      leftValue: '98% identical',
      rightValue: 'Handwritten note',
      rightCopy: 'The second scan contains a handwritten annotation, so consolidation may discard useful evidence.',
    },
    'Grouping anomaly': {
      leftTitle: 'Statement series · •••• 4321',
      rightTitle: 'Statement series · •••• 8876',
      leftDate: 'Sep 30, 2026',
      rightDate: 'Sep 30, 2026',
      leftLabel: 'Account identifier',
      rightLabel: 'Account identifier',
      leftValue: '•••• 4321',
      rightValue: '•••• 8876',
      rightCopy: 'The documents share a correspondent and period but identify different accounts.',
    },
    'Orphan document': {
      leftTitle: 'Lab Corp bill · unmatched',
      rightTitle: 'Nearest EOB candidate',
      leftDate: 'Aug 19, 2026',
      rightDate: 'Aug 18, 2026',
      leftLabel: 'Bill balance',
      rightLabel: 'Candidate confidence',
      leftValue: '$126.40',
      rightValue: '34% · weak',
      rightCopy: 'The nearest candidate has a different ordering provider and no matching service code.',
    },
  };
  const presentation = presentations[item.kind] || presentations['EOB match'];
  return `<div class="review-evidence-comparison">
    <article class="evidence-sheet">
      <span>${presentation.leftTitle.toUpperCase()}</span>
      <h3>${item.correspondent}</h3>
      <p>${item.reasonCopy}</p>
      <dl>
        <div><dt>Document date</dt><dd>${presentation.leftDate}</dd></div>
        <div><dt>Reference</dt><dd>REF-10482</dd></div>
        <div class="evidence-mismatch"><dt>${presentation.leftLabel}</dt><dd>${presentation.leftValue}</dd></div>
      </dl>
    </article>
    <article class="evidence-sheet">
      <span>${presentation.rightTitle.toUpperCase()}</span>
      <h3>${item.correspondent}</h3>
      <p>${presentation.rightCopy}</p>
      <dl>
        <div><dt>Document date</dt><dd>${presentation.rightDate}</dd></div>
        <div><dt>Reference</dt><dd>REF-10482</dd></div>
        <div class="evidence-mismatch"><dt>${presentation.rightLabel}</dt><dd>${presentation.rightValue}</dd></div>
      </dl>
    </article>
  </div>`;
}

function relationshipDecisionPresentation(item) {
  const presentations = {
    'EOB match': {
      labels: ['Provider', 'Service date', 'Patient responsibility', 'Bill balance'],
      values: [item.correspondent, 'Sep 28, 2026', '$36.00', '$42.00'],
      alternativeTitle: 'Alternative candidate',
      alternativeCopy: 'City Medical statement · 58% match',
      alternativeAction: 'Choose another match',
      outcomeTitle: 'Relationship outcome',
      outcomeCopy: 'Confirming links the documents and re-runs obligation analysis. It may close an existing action, create Needs Review, or create no action.',
      reject: 'Reject match',
      confirm: 'Confirm match & next',
    },
    'Possible duplicate': {
      labels: ['Correspondent', 'Case number', 'Similarity', 'Material difference'],
      values: [item.correspondent, 'CASE-91KQ', '98%', 'Handwritten annotation'],
      alternativeTitle: 'Consolidation choice',
      alternativeCopy: 'Keep the annotated scan as the canonical document',
      alternativeAction: 'Choose canonical copy',
      outcomeTitle: 'Duplicate outcome',
      outcomeCopy: 'Consolidating preserves the canonical document and annotation evidence while suppressing the redundant scan from future analysis.',
      reject: 'Keep both',
      confirm: 'Consolidate duplicate & next',
    },
    'Grouping anomaly': {
      labels: ['Correspondent', 'Statement period', 'First account', 'Second account'],
      values: [item.correspondent, 'September 2026', '•••• 4321', '•••• 8876'],
      alternativeTitle: 'Grouping rule',
      alternativeCopy: 'Separate future statements by masked account identifier',
      alternativeAction: 'Edit grouping rule',
      outcomeTitle: 'Grouping outcome',
      outcomeCopy: 'Splitting creates independent statement series and re-evaluates expectations for each account.',
      reject: 'Keep grouped',
      confirm: 'Split series & next',
    },
    'Orphan document': {
      labels: ['Provider', 'Bill date', 'Bill balance', 'Search coverage'],
      values: [item.correspondent, 'Aug 19, 2026', '$126.40', '45 days'],
      alternativeTitle: 'Candidate search',
      alternativeCopy: 'No strong EOB or payment evidence candidate was found',
      alternativeAction: 'Search all documents',
      outcomeTitle: 'Orphan outcome',
      outcomeCopy: 'Confirming no current match keeps the bill available for future relationship analysis without fabricating a link.',
      reject: 'No match expected',
      confirm: 'Keep unmatched & next',
    },
  };
  return presentations[item.kind] || presentations['EOB match'];
}

function reviewProposals(item) {
  return item.proposedActions || [{
    type: 'REVIEW',
    title: 'Review Northline Energy balance',
    summary: 'Determine whether the notice confirms a partial payment or requests the remaining $84.20 balance.',
    due: '2026-10-24',
    amount: '84.20',
  }];
}

function renderReviewProposedActions(item, outcome) {
  const proposals = reviewProposals(item);
  selectedReviewActionIndex = Math.min(selectedReviewActionIndex, proposals.length - 1);
  const selected = proposals[selectedReviewActionIndex];
  document.querySelector('#review-proposed-action-list').innerHTML = proposals.map((proposal, index) =>
    `<button type="button" class="sibling-action-button${index === selectedReviewActionIndex ? ' active' : ''}" data-review-proposal="${index}">
      <strong>${proposal.type} · ${proposal.title}</strong>
      <span>${proposal.due ? `Due ${new Date(`${proposal.due}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : 'No due date'}${proposal.amount ? ` · $${proposal.amount}` : ''}</span>
    </button>`
  ).join('');
  document.querySelectorAll('[data-review-proposal]').forEach((button) => {
    button.addEventListener('click', () => {
      selectedReviewActionIndex = Number(button.dataset.reviewProposal);
      renderReviewProposedActions(item, outcome);
    });
  });
  setText('#review-suggestion-title', selected.title);
  document.querySelector('#review-action-type').value = selected.type;
  document.querySelector('#review-action-title').value = selected.title;
  document.querySelector('#review-action-summary').value = selected.summary;
  document.querySelector('#review-action-due').value = selected.due;
  document.querySelector('#review-action-amount').value = selected.amount;
  setText('#review-action-trust-copy', proposals.length > 1
    ? `Accepting publishes all ${proposals.length} proposed actions to this document in Action Review and Mission Control. It does not mark them done.`
    : 'Accepting publishes this proposed action to the document in Action Review and Mission Control. It does not mark it done.');
  document.querySelector('[data-review-outcome="accept"]').textContent = outcome
    ? 'Save revised decision & next'
    : proposals.length > 1
      ? `Accept ${proposals.length} actions & next`
      : 'Accept action & next';
}

function selectReview(id) {
  const item = reviewItems.find((candidate) => candidate.id === id);
  if (!item) return;
  document.querySelector('#needs-review .document-workspace').hidden = false;
  selectedReviewId = id;
  const position = reviewItems.indexOf(item) + 1;
  const relationship = item.category === 'relationship';
  const outcome = resolvedReviews.get(item.id);
  selectedReviewActionIndex = 0;
  setText('#review-position', `Review ${position} of ${reviewItems.length} · ${item.kind.toLowerCase()}`);
  setText('#review-document-title', item.document);
  setText('#review-header-reason', item.reason);
  setText('#review-header-confidence', `${item.confidence}%`);
  setText('#review-header-age', item.age);
  setText('#review-reason-title', item.reason);
  setText('#review-confidence-pill', `${item.confidence}% confidence`);
  setText('#review-reason-copy', item.reasonCopy);
  setText('#review-footer-title', item.reason);
  setText('#review-metadata-title', item.document);
  setText('#review-metadata-correspondent', item.correspondent);
  setText('#review-metadata-type', item.documentType);
  setText('#review-source-kind', item.kind);
  setText('#review-evidence-count', relationship ? '2 documents' : '1 document');
  setText('#review-evidence-title', relationship ? 'Compare evidence' : 'Source evidence');
  document.querySelector('#review-evidence').innerHTML = relationship
    ? relationshipEvidence(item)
    : singleReviewEvidence(item);
  document.querySelector('#action-classification-decision').hidden = relationship;
  document.querySelector('#relationship-decision').hidden = !relationship;
  document.querySelector('#action-review-outcomes').hidden = relationship;
  document.querySelector('#relationship-review-outcomes').hidden = !relationship;
  document.querySelector('#resolved-review-outcomes').hidden = !outcome;
  document.querySelector('#resolved-review-warning').hidden = !outcome;
  if (!relationship) renderReviewProposedActions(item, outcome);
  setText('#relationship-title', item.title);
  setText('#relationship-summary', item.reasonCopy);
  setText('#relationship-type', item.kind.toUpperCase());
  const relationshipPresentation = relationshipDecisionPresentation(item);
  ['provider', 'date', 'left', 'right'].forEach((field, index) => {
    setText(`#relationship-${field}-label`, relationshipPresentation.labels[index]);
    setText(`#relationship-${field}${field === 'left' || field === 'right' ? '-amount' : ''}`, relationshipPresentation.values[index]);
  });
  setText('#alternative-title', relationshipPresentation.alternativeTitle);
  setText('#alternative-copy', relationshipPresentation.alternativeCopy);
  setText('#alternative-action', relationshipPresentation.alternativeAction);
  setText('#relationship-outcome-title', relationshipPresentation.outcomeTitle);
  setText('#relationship-outcome-copy', relationshipPresentation.outcomeCopy);
  setText('#reject-relationship', relationshipPresentation.reject);
  setText('#confirm-relationship', outcome ? `Save revised decision & next` : relationshipPresentation.confirm);
  setText('#review-footer-copy', outcome === 'defer'
    ? 'This review is deferred. You may revise it now or move it back to Pending.'
    : outcome
      ? 'This review is resolved. Revisions are allowed and recorded in audit history.'
      : relationship
        ? 'Resolve the proposed relationship; downstream analysis decides whether work exists.'
        : 'Review every proposed action attached to this document; accepting does not complete the work.');
  renderReviewQueue();
}

function applyReviewSearchAndFilter() {
  const query = document.querySelector('#review-search').value.trim().toLowerCase();
  visibleReviews = reviewItems.filter((item) => {
    const outcome = resolvedReviews.get(item.id);
    const matchesStatus = reviewStatus === 'pending'
      ? !outcome
      : reviewStatus === 'deferred'
        ? outcome === 'defer'
        : Boolean(outcome) && outcome !== 'defer';
    if (!matchesStatus) return false;
    const matchesQuery = [item.title, item.document, item.correspondent, item.reason, item.kind]
      .some((value) => value.toLowerCase().includes(query));
    const matchesFilter = reviewFilter === 'all' || item.category === reviewFilter;
    return matchesQuery && matchesFilter;
  });
  const sort = document.querySelector('#review-sort').value;
  visibleReviews.sort((a, b) => {
    if (sort === 'confidence') return a.confidence - b.confidence;
    if (sort === 'priority') return Number(b.priority === 'High') - Number(a.priority === 'High') || b.ageDays - a.ageDays;
    return b.ageDays - a.ageDays;
  });
  renderReviewQueue();
}

function resolveReview(outcome) {
  const item = reviewItems.find((candidate) => candidate.id === selectedReviewId);
  if (!item) return;
  const proposedActionCount = item.category === 'action' ? reviewProposals(item).length : 0;
  const currentIndex = visibleReviews.findIndex((candidate) => candidate.id === item.id);
  const messages = {
    accept: proposedActionCount > 1
      ? `${proposedActionCount} actions accepted and attached to this document in Action Review.`
      : 'Action accepted and attached to this document in Action Review.',
    'no-action': 'False positive recorded. No action was created.',
    reanalyze: 'Document analysis rerun. The item remains in Needs Review until the new result is ready.',
    defer: 'Review postponed for 7 days.',
    reject: 'Proposed relationship rejected and retained as correction evidence.',
    'confirm-match': 'Relationship confirmed. OWL will re-run obligation analysis.',
  };
  if (outcome !== 'reanalyze') {
    resolvedReviews.set(item.id, outcome);
    applyReviewSearchAndFilter();
    const next = visibleReviews[currentIndex] || visibleReviews[currentIndex - 1] || visibleReviews[0];
    if (next) selectReview(next.id);
  }
  showToast(messages[outcome], outcome === 'reanalyze' ? undefined : () => {
    resolvedReviews.delete(item.id);
    applyReviewSearchAndFilter();
    selectReview(item.id);
  });
}

function setText(selector, value) {
  document.querySelector(selector).textContent = value;
}

function quickActionIcon(kind) {
  if (kind === 'phone') return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4H4v3c0 7.2 5.8 13 13 13h3v-3l-4-2-2 2c-3.1-1.1-5.9-3.9-7-7l2-2Z"/></svg>';
  if (kind === 'email') return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16v12H4zM4 7l8 6 8-6"/></svg>';
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M13 11l6-6M19 14v5H5V5h5"/></svg>';
}

function quickActionHref(kind) {
  if (kind === 'phone') return 'tel:+15550148821';
  if (kind === 'email') return 'mailto:office@example.invalid';
  return 'https://example.invalid/';
}

function applyActionFieldSchema(type) {
  const schema = actionFieldSchemas[type] || actionFieldSchemas.TASK;
  document.querySelector('#action-owner-field').hidden = !schema.owner;
  document.querySelector('#action-amount-field').hidden = !schema.amount;
  document.querySelector('#action-reference-field').hidden = !schema.reference;
  if (schema.owner) setText('#action-owner-label', schema.owner);
  if (schema.reference) setText('#action-reference-label', schema.reference);
  setText('#action-due-label', schema.due);
}

function renderActionHistory(action, outcome) {
  const events = action.origin === 'Manual'
    ? [['Today', 'Action manually added to this document.']]
    : [
        ['Oct 15, 2026', `OWL inferred a ${action.type} action from this document.`],
        ['Oct 15, 2026', `Confidence recorded at ${action.confidence}%.`],
      ];
  if (outcome === 'snoozed') {
    events.unshift(['Today', 'Reminder scheduled; the action remains attached to this document.']);
  } else if (outcome) {
    events.unshift(['Today', `Action marked ${outcome === 'done' ? 'done' : outcome.replaceAll('-', ' ')}.`]);
  }
  document.querySelector('#action-history-list').innerHTML = events
    .map(([date, copy]) => `<li><time>${date}</time><span>${copy}</span></li>`)
    .join('');
  setText('#action-history-summary', outcome
    ? `Latest lifecycle change: ${events[0][1]}`
    : 'Created from this document · no lifecycle changes');
}

function selectAction(id) {
  const action = actions.find((item) => item.id === id);
  if (!action) return;
  document.querySelector('#workspace .document-workspace').hidden = false;
  selectedId = id;
  actionDirty = false;
  const outcome = resolvedActions.get(action.id);
  const siblings = actions.filter((candidate) => documentKey(candidate) === documentKey(action));
  const groups = documentGroups();
  const documentIndex = groups.findIndex((group) => group.key === documentKey(action));
  document.querySelector('#document-action-count').textContent = siblings.length;
  setText('#document-position', `Document ${documentIndex >= 0 ? documentIndex + 1 : '—'} of ${groups.length} · Paperless #${documentKey(action)}`);
  setText('#document-title', action.document);
  setText('#document-date', action.date);
  setText('#document-correspondent', action.correspondent);
  setText('#document-type', action.documentType);
  setText('#risk-badge', action.dueLabel);
  document.querySelector('#risk-badge').classList.toggle('overdue', action.dueRank < 0);
  document.querySelector('#action-type-input').value = action.type;
  document.querySelector('#action-title-input').value = action.title;
  document.querySelector('#action-owner-input').value = action.correspondent;
  document.querySelector('#action-amount-input').value = action.amount ?? '';
  document.querySelector('#action-due-input').value = action.dueValue;
  document.querySelector('#action-reference-input').value = action.reference;
  document.querySelector('#action-details-input').value = action.summary;
  applyActionFieldSchema(action.type);
  setText('#action-status-badge', outcome === 'snoozed' ? 'Remind later' : outcome ? 'Resolved' : 'Pending');
  setText('#action-origin', action.origin || 'Inferred');
  setText('#action-save-status', outcome
    ? 'Resolved actions remain editable; saving records a new audit event.'
    : 'Fields are editable by default, matching Inbox.');
  document.querySelector('#active-action-outcomes').hidden = Boolean(outcome);
  document.querySelector('#resolved-action-outcomes').hidden = !outcome;
  const siblingContainer = document.querySelector('#sibling-actions');
  siblingContainer.hidden = false;
  document.querySelector('#sibling-action-list').innerHTML = siblings.map((sibling) =>
    `<div class="action-set-row">
      <button type="button" class="sibling-action-button${sibling.id === action.id ? ' active' : ''}" data-sibling-action="${sibling.id}">
        <strong>${sibling.type} · ${sibling.title}</strong>
        <span>${resolvedActions.get(sibling.id) === 'snoozed' ? 'Remind later' : resolvedActions.has(sibling.id) ? 'Resolved' : sibling.dueLabel}</span>
      </button>
      <div class="action-row-menu-wrap">
        <button type="button" class="action-row-menu-button" data-action-menu="${sibling.id}" aria-label="More outcomes for ${sibling.title}" aria-expanded="false">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/></svg>
        </button>
        <div class="action-row-menu" data-action-menu-panel="${sibling.id}" hidden>
          ${resolvedActions.has(sibling.id)
            ? `<button type="button" data-quick-action-outcome="reopen" data-quick-action-id="${sibling.id}">Move back to Pending</button>`
            : `<button type="button" data-quick-action-outcome="done" data-quick-action-id="${sibling.id}">Mark done</button>
              <button type="button" data-quick-action-outcome="snoozed" data-quick-action-id="${sibling.id}">Remind later</button>
              <button type="button" data-quick-action-outcome="not-action" data-quick-action-id="${sibling.id}">Not an action</button>
              <button type="button" data-quick-action-outcome="wont-do" data-quick-action-id="${sibling.id}">Won't do</button>`}
          <button type="button" data-quick-action-outcome="history" data-quick-action-id="${sibling.id}">View history</button>
        </div>
      </div>
    </div>`
  ).join('');
  document.querySelectorAll('[data-sibling-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const siblingId = Number(button.dataset.siblingAction);
      if (actionDirty && siblingId !== selectedId) {
        showToast('Save the selected action before switching actions.');
        document.querySelector('#save-action').focus();
        return;
      }
      selectAction(siblingId);
    });
  });
  document.querySelectorAll('[data-action-menu]').forEach((button) => {
    button.addEventListener('click', () => {
      const panel = document.querySelector(`[data-action-menu-panel="${button.dataset.actionMenu}"]`);
      const expanded = !panel.hidden;
      document.querySelector('#done-menu').hidden = true;
      document.querySelector('#done-menu-toggle').setAttribute('aria-expanded', 'false');
      document.querySelectorAll('[data-action-menu-panel]').forEach((menu) => { menu.hidden = true; });
      document.querySelectorAll('[data-action-menu]').forEach((trigger) => trigger.setAttribute('aria-expanded', 'false'));
      panel.hidden = expanded;
      button.setAttribute('aria-expanded', String(!expanded));
    });
  });
  document.querySelectorAll('[data-quick-action-outcome]').forEach((button) => {
    button.addEventListener('click', () => {
      const siblingId = Number(button.dataset.quickActionId);
      const quickOutcome = button.dataset.quickActionOutcome;
      if (actionDirty) {
        showToast('Save the selected action before changing another action.');
        document.querySelector('#save-action').focus();
        return;
      }
      const sibling = actions.find((candidate) => candidate.id === siblingId);
      if (!sibling) return;
      selectedId = sibling.id;
      if (quickOutcome === 'history') {
        selectAction(sibling.id);
        document.querySelector('.action-history').open = true;
        document.querySelector('.action-history').scrollIntoView({ block: 'nearest' });
        return;
      }
      if (quickOutcome === 'reopen') {
        resolvedActions.delete(sibling.id);
        actionStatusFilter = 'pending';
        document.querySelectorAll('[data-action-status]').forEach((statusButton) => statusButton.classList.toggle('active', statusButton.dataset.actionStatus === 'pending'));
        updateActionSectionName();
        applySearchAndFilter();
        selectAction(sibling.id);
        showToast(`${sibling.title} moved back to Pending.`);
        return;
      }
      if (quickOutcome === 'not-action') {
        pendingConfirmationId = sibling.id;
        selectAction(sibling.id);
        document.querySelector('#outcome-confirmation').hidden = false;
        document.querySelector('#cancel-confirmation').focus();
        return;
      }
      if (quickOutcome === 'snoozed') {
        resolveAndMove('snoozed', `Reminder set for next Monday. Moving to the next action.`);
        return;
      }
      const outcomeLabel = quickOutcome === 'done' ? 'marked done' : "marked won't do";
      resolveAndMove(quickOutcome, `${sibling.title} ${outcomeLabel}. Moving to the next action.`);
    });
  });
  setText('#footer-action-scope', siblings.length > 1
    ? 'These outcomes apply only to the selected action.'
    : 'Choose the action outcome, then OWL advances.');
  setText('#done-action-label', 'Done');
  const nextDocumentButton = document.querySelector('#next-document');
  nextDocumentButton.disabled = documentIndex < 0 || documentIndex >= groups.length - 1;
  const doneMenuToggle = document.querySelector('#done-menu-toggle');
  doneMenuToggle.hidden = siblings.length < 2 || Boolean(outcome);
  doneMenuToggle.setAttribute('aria-expanded', 'false');
  document.querySelector('#done-menu').hidden = true;
  setText('#confidence-value', `${action.confidence}% confidence`);
  setText('#confidence-reason', action.reason);
  renderActionHistory(action, outcome);
  setText('#footer-title', action.title);
  setText('#metadata-title', action.document);
  setText('#metadata-correspondent', action.correspondent);
  setText('#metadata-type', action.documentType);
  setText('#paper-provider', action.correspondent.toUpperCase());
  setText('#paper-kind', action.paperKind);
  setText('#paper-title', action.paperTitle);
  setText('#paper-description', action.paperDescription);
  setText('#paper-date', action.due);
  setText('#paper-amount', money(action.amount));
  setText('#paper-callout', action.paperCallout);
  document.querySelector('#quick-actions').innerHTML = action.quick.map(([label, kind]) =>
    `<a href="${quickActionHref(kind)}" class="quick-action-link${kind === 'primary' ? ' primary' : ''}"${['phone', 'email'].includes(kind) ? '' : ' target="_blank" rel="noopener noreferrer"'}>${quickActionIcon(kind)}${label}</a>`
  ).join('');
  renderQueue();
}

function applySearchAndFilter() {
  const query = document.querySelector('#queue-search').value.trim().toLowerCase();
  visibleActions = actions.filter((action) => {
    const outcome = resolvedActions.get(action.id);
    const matchesStatus = actionStatusFilter === 'pending'
      ? !outcome
      : actionStatusFilter === 'deferred'
        ? outcome === 'snoozed'
        : Boolean(outcome) && outcome !== 'snoozed';
    if (!matchesStatus) return false;
    const matchesQuery = [action.title, action.document, action.correspondent, action.type]
      .some((value) => value.toLowerCase().includes(query));
    const matchesType = actionTypeFilter === 'all' || action.type === actionTypeFilter;
    return matchesQuery && matchesActionDueWindow(action) && matchesType;
  });
  sortActions();
  renderQueue();
  const groups = documentGroups();
  if (!groups.length) {
    document.querySelector('#workspace .document-workspace').hidden = true;
    return;
  }
  const selectedAction = actions.find((action) => action.id === selectedId);
  const selectedActionVisible = visibleActions.some((action) => action.id === selectedId);
  const selectedDocumentGroup = selectedAction
    ? groups.find((group) => group.key === documentKey(selectedAction))
    : null;
  selectAction(selectedActionVisible
    ? selectedId
    : selectedDocumentGroup?.primary.id || groups[0].primary.id);
}

function sortActions() {
  const sort = document.querySelector('#queue-sort').value;
  visibleActions.sort((a, b) => {
    if (sort === 'risk') return ['Critical', 'High', 'Medium'].indexOf(a.urgency) - ['Critical', 'High', 'Medium'].indexOf(b.urgency);
    if (sort === 'newest') return b.id - a.id;
    if (sort === 'amount') return (b.amount || 0) - (a.amount || 0);
    return a.dueRank - b.dueRank;
  });
}

function showToast(message, undo) {
  toast.replaceChildren(document.createTextNode(message));
  if (undo) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'Undo';
    button.addEventListener('click', () => {
      undo();
      toast.hidden = true;
    });
    toast.append(button);
  }
  toast.hidden = false;
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => { toast.hidden = true; }, 3200);
}

function resolveAndMove(outcome, message) {
  const current = actions.find((action) => action.id === selectedId);
  const currentIndex = visibleActions.findIndex((action) => action.id === selectedId);
  resolvedActions.set(current.id, outcome);
  applySearchAndFilter();
  const next = visibleActions[currentIndex] || visibleActions[currentIndex - 1] || visibleActions[0];
  if (next) selectAction(next.id);
  showToast(message.replace('{title}', current.title), () => {
    resolvedActions.delete(current.id);
    applySearchAndFilter();
    selectAction(current.id);
  });
}

function advance(outcome) {
  const current = actions.find((action) => action.id === selectedId);
  if (actionDirty) {
    showToast('Save action changes before choosing a lifecycle outcome.');
    document.querySelector('#save-action').focus();
    return;
  }
  if (outcome === 'not-action') {
    pendingConfirmationId = current.id;
    document.querySelector('#outcome-confirmation').hidden = false;
    document.querySelector('#cancel-confirmation').focus();
    return;
  }
  const labels = {
    done: 'marked done',
    'wont-do': "marked won't do",
  };
  resolveAndMove(outcome, `${current.title} ${labels[outcome]}. Moving to the next action.`);
}

function showView(view) {
  document.querySelector('#workspace').hidden = view !== 'workspace';
  document.querySelector('#needs-review').hidden = view !== 'needs-review';
  document.querySelectorAll('.view-switch').forEach((button) => {
    button.classList.toggle('active', button.dataset.view === view);
  });
  window.location.hash = view;
}

function updateActionSectionName() {
  const statusLabel = actionStatusFilter === 'resolved'
    ? 'Resolved'
    : actionStatusFilter === 'deferred'
      ? 'Remind later'
      : '';
  const dueLabel = activeFilter === 'overdue' ? 'Overdue' : activeFilter === 'upcoming' ? 'Upcoming' : '';
  const typeLabel = actionTypeFilter === 'all'
    ? ''
    : `${actionTypeFilter.charAt(0)}${actionTypeFilter.slice(1).toLowerCase()}`;
  document.querySelector('#queue-section-name').textContent = [statusLabel, dueLabel, typeLabel].filter(Boolean).join(' · ') || 'Needs attention';
}

function updateReviewSectionName() {
  const section = document.querySelector('#review-section-name');
  if (reviewStatus === 'resolved') {
    section.textContent = 'Resolved reviews';
    return;
  }
  if (reviewStatus === 'deferred') {
    section.textContent = 'Review later';
    return;
  }
  section.textContent = reviewFilter === 'action'
    ? 'Untrusted action classifications'
    : reviewFilter === 'relationship'
      ? 'Uncertain document relationships'
      : 'OWL needs your judgment';
}

document.querySelector('#queue-search').addEventListener('input', applySearchAndFilter);
document.querySelector('#queue-sort').addEventListener('change', applySearchAndFilter);
document.querySelectorAll('[data-filter]').forEach((button) => {
  button.addEventListener('click', () => {
    activeFilter = button.dataset.filter;
    document.querySelectorAll('[data-filter]').forEach((item) => item.classList.toggle('active', item === button));
    updateActionSectionName();
    applySearchAndFilter();
  });
});
document.querySelectorAll('[data-action-status]').forEach((button) => {
  button.addEventListener('click', () => {
    actionStatusFilter = button.dataset.actionStatus;
    document.querySelectorAll('[data-action-status]').forEach((item) => item.classList.toggle('active', item === button));
    updateActionSectionName();
    applySearchAndFilter();
  });
});
document.querySelectorAll('[data-action-type]').forEach((button) => {
  button.addEventListener('click', () => {
    actionTypeFilter = button.dataset.actionType;
    document.querySelectorAll('[data-action-type]').forEach((item) => item.classList.toggle('active', item === button));
    updateActionSectionName();
    applySearchAndFilter();
  });
});
document.querySelector('#toggle-density').addEventListener('click', () => {
  const workspace = document.querySelector('#workspace');
  const collapsed = workspace.classList.toggle('queue-collapsed');
  const button = document.querySelector('#toggle-density');
  button.setAttribute('aria-label', collapsed ? 'Expand action queue' : 'Collapse action queue');
  button.title = collapsed ? 'Expand action queue' : 'Collapse action queue';
});
document.querySelector('#review-search').addEventListener('input', applyReviewSearchAndFilter);
document.querySelector('#review-sort').addEventListener('change', applyReviewSearchAndFilter);
document.querySelectorAll('[data-review-filter]').forEach((button) => {
  button.addEventListener('click', () => {
    reviewFilter = button.dataset.reviewFilter;
    document.querySelectorAll('[data-review-filter]').forEach((item) => item.classList.toggle('active', item === button));
    updateReviewSectionName();
    applyReviewSearchAndFilter();
  });
});
document.querySelectorAll('[data-review-status]').forEach((button) => {
  button.addEventListener('click', () => {
    reviewStatus = button.dataset.reviewStatus;
    document.querySelectorAll('[data-review-status]').forEach((item) => item.classList.toggle('active', item === button));
    updateReviewSectionName();
    applyReviewSearchAndFilter();
    const first = visibleReviews[0];
    if (first) {
      selectReview(first.id);
    } else {
      document.querySelector('#needs-review .document-workspace').hidden = true;
    }
  });
});
document.querySelector('#toggle-review-density').addEventListener('click', () => {
  const workspace = document.querySelector('#needs-review');
  const collapsed = workspace.classList.toggle('queue-collapsed');
  const button = document.querySelector('#toggle-review-density');
  button.setAttribute('aria-label', collapsed ? 'Expand review queue' : 'Collapse review queue');
  button.title = collapsed ? 'Expand review queue' : 'Collapse review queue';
});
document.querySelectorAll('[data-tab]').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('[data-tab]').forEach((item) => {
      const active = item === button;
      item.classList.toggle('active', active);
      item.setAttribute('aria-selected', String(active));
      item.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll('[data-panel]').forEach((panel) => {
      const active = panel.dataset.panel === button.dataset.tab;
      panel.hidden = !active;
      panel.classList.toggle('active', active);
    });
  });
});
document.querySelector('.tabs').addEventListener('keydown', (event) => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const tabs = [...document.querySelectorAll('[data-tab]')];
  const currentIndex = tabs.indexOf(document.activeElement);
  const nextIndex = event.key === 'Home'
    ? 0
    : event.key === 'End'
      ? tabs.length - 1
      : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  tabs[nextIndex].click();
  tabs[nextIndex].focus();
});
document.querySelectorAll('[data-review-tab]').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('[data-review-tab]').forEach((item) => {
      const active = item === button;
      item.classList.toggle('active', active);
      item.setAttribute('aria-selected', String(active));
      item.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll('[data-review-panel]').forEach((panel) => {
      const active = panel.dataset.reviewPanel === button.dataset.reviewTab;
      panel.hidden = !active;
      panel.classList.toggle('active', active);
    });
  });
});
document.querySelector('.review-tabs').addEventListener('keydown', (event) => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const tabs = [...document.querySelectorAll('[data-review-tab]')];
  const currentIndex = tabs.indexOf(document.activeElement);
  const nextIndex = event.key === 'Home'
    ? 0
    : event.key === 'End'
      ? tabs.length - 1
      : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  tabs[nextIndex].click();
  tabs[nextIndex].focus();
});
document.querySelectorAll('[data-review-outcome]').forEach((button) => {
  button.addEventListener('click', () => resolveReview(button.dataset.reviewOutcome));
});
document.querySelector('#reopen-review').addEventListener('click', () => {
  resolvedReviews.delete(selectedReviewId);
  reviewStatus = 'pending';
  document.querySelectorAll('[data-review-status]').forEach((button) => button.classList.toggle('active', button.dataset.reviewStatus === 'pending'));
  updateReviewSectionName();
  applyReviewSearchAndFilter();
  selectReview(selectedReviewId);
  showToast('Review reopened and returned to Pending.');
});
document.querySelector('#review-correction-form').addEventListener('input', () => {
  const item = reviewItems.find((candidate) => candidate.id === selectedReviewId);
  const count = item ? reviewProposals(item).length : 1;
  const proposal = item ? reviewProposals(item)[selectedReviewActionIndex] : null;
  if (proposal) {
    proposal.type = document.querySelector('#review-action-type').value;
    proposal.title = document.querySelector('#review-action-title').value;
    proposal.summary = document.querySelector('#review-action-summary').value;
    proposal.due = document.querySelector('#review-action-due').value;
    proposal.amount = document.querySelector('#review-action-amount').value;
  }
  document.querySelector('[data-review-outcome="accept"]').textContent = count > 1
    ? `Save corrections to ${count} actions & next`
    : 'Save corrections & next';
});
document.querySelector('#reset-review-suggestion').addEventListener('click', () => {
  const item = reviewItems.find((candidate) => candidate.id === selectedReviewId);
  if (!item) return;
  if (item.id === 'review-action-1') {
    item.proposedActions = [
      {
        type: 'PAY',
        title: 'Pay remaining Northline balance',
        summary: 'Pay the remaining $84.20 balance after the documented $40.00 partial payment.',
        due: '2026-10-24',
        amount: '84.20',
      },
      {
        type: 'REVIEW',
        title: 'Review partial payment allocation',
        summary: 'Confirm that the recorded $40.00 payment was applied to the correct Northline Energy account.',
        due: '2026-10-20',
        amount: '',
      },
    ];
    selectedReviewActionIndex = 0;
  }
  renderReviewProposedActions(item, resolvedReviews.get(item.id));
});
document.querySelector('#add-review-action').addEventListener('click', () => {
  const item = reviewItems.find((candidate) => candidate.id === selectedReviewId);
  if (!item || item.category !== 'action') return;
  if (!item.proposedActions) item.proposedActions = reviewProposals(item);
  item.proposedActions.push({
    type: 'TASK',
    title: 'New proposed action',
    summary: '',
    due: '',
    amount: '',
  });
  selectedReviewActionIndex = item.proposedActions.length - 1;
  renderReviewProposedActions(item, resolvedReviews.get(item.id));
  document.querySelector('#review-action-title').focus();
  showToast('New proposed action added to this document review.');
});
document.querySelectorAll('[data-outcome]').forEach((button) => button.addEventListener('click', () => advance(button.dataset.outcome)));
document.querySelector('#reopen-action').addEventListener('click', () => {
  resolvedActions.delete(selectedId);
  actionStatusFilter = 'pending';
  document.querySelectorAll('[data-action-status]').forEach((button) => button.classList.toggle('active', button.dataset.actionStatus === 'pending'));
  updateActionSectionName();
  applySearchAndFilter();
  selectAction(selectedId);
  showToast('Action moved back to Pending.');
});
document.querySelector('#remind-action').addEventListener('click', () => {
  const menu = document.querySelector('#reminder-menu');
  menu.hidden = !menu.hidden;
  document.querySelector('#remind-action').setAttribute('aria-expanded', String(!menu.hidden));
});
document.querySelectorAll('[data-reminder]').forEach((button) => {
  button.addEventListener('click', () => {
    if (actionDirty) {
      document.querySelector('#reminder-menu').hidden = true;
      document.querySelector('#remind-action').setAttribute('aria-expanded', 'false');
      showToast('Save action changes before setting a reminder.');
      document.querySelector('#save-action').focus();
      return;
    }
    document.querySelector('#reminder-menu').hidden = true;
    document.querySelector('#remind-action').setAttribute('aria-expanded', 'false');
    resolveAndMove('snoozed', `Reminder set for ${button.dataset.reminder}. Moving to the next action.`);
  });
});
document.querySelector('#zoom-document').addEventListener('click', (event) => {
  const fit = document.querySelector('#paper').classList.toggle('fit-width');
  event.currentTarget.textContent = fit ? 'Fit page' : 'Fit width';
});
document.querySelectorAll('.view-switch').forEach((button) => button.addEventListener('click', () => showView(button.dataset.view)));
document.querySelectorAll('[data-show-workspace]').forEach((button) => button.addEventListener('click', () => showView('workspace')));
document.querySelectorAll('[data-show-needs-review]').forEach((button) => button.addEventListener('click', () => showView('needs-review')));
document.querySelector('#open-rationale').addEventListener('click', () => { document.querySelector('#rationale').hidden = false; });
document.querySelectorAll('[data-show-review-ledger]').forEach((button) => button.addEventListener('click', () => { document.querySelector('#rationale').hidden = false; }));
document.querySelector('#close-rationale').addEventListener('click', () => { document.querySelector('#rationale').hidden = true; });
document.querySelector('#cancel-confirmation').addEventListener('click', () => {
  document.querySelector('#outcome-confirmation').hidden = true;
  pendingConfirmationId = null;
});
document.querySelector('#confirm-no-action').addEventListener('click', () => {
  const action = actions.find((item) => item.id === pendingConfirmationId);
  document.querySelector('#outcome-confirmation').hidden = true;
  pendingConfirmationId = null;
  if (action) {
    selectedId = action.id;
    resolveAndMove('not-action', `${action.title} marked not an action and saved as classifier feedback.`);
  }
});
document.querySelector('#action-type-input').addEventListener('change', (event) => {
  applyActionFieldSchema(event.target.value);
  setText('#action-save-status', 'Unsaved changes');
});
document.querySelector('#action-edit-form').addEventListener('input', () => {
  actionDirty = true;
  setText('#action-save-status', 'Unsaved changes');
});
document.querySelector('#save-action').addEventListener('click', () => {
  const action = actions.find((item) => item.id === selectedId);
  if (!action) return;
  const type = document.querySelector('#action-type-input').value;
  const schema = actionFieldSchemas[type] || actionFieldSchemas.TASK;
  const dueValue = document.querySelector('#action-due-input').value;
  action.type = type;
  action.title = document.querySelector('#action-title-input').value.trim() || action.title;
  action.summary = document.querySelector('#action-details-input').value.trim();
  action.amount = schema.amount ? Number(document.querySelector('#action-amount-input').value || 0) : null;
  action.reference = schema.reference ? document.querySelector('#action-reference-input').value.trim() : '';
  action.dueValue = dueValue;
  if (dueValue) {
    action.due = new Date(`${dueValue}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }
  actionDirty = false;
  applySearchAndFilter();
  const selected = visibleActions.find((item) => item.id === action.id) || visibleActions[0];
  if (selected) selectAction(selected.id);
  setText('#action-save-status', 'Saved just now');
  showToast('Action changes saved.');
});
document.querySelector('#add-action').addEventListener('click', () => {
  const current = actions.find((action) => action.id === selectedId);
  if (!current) return;
  if (actionDirty) {
    showToast('Save the selected action before adding another action.');
    document.querySelector('#save-action').focus();
    return;
  }
  const action = {
    ...current,
    id: Math.max(...actions.map((candidate) => candidate.id)) + 1,
    paperlessId: documentKey(current),
    type: 'TASK',
    title: 'New action',
    due: 'No due date',
    dueValue: '',
    dueLabel: 'No due date',
    dueRank: 999,
    amount: null,
    urgency: 'Medium',
    confidence: 100,
    reference: '',
    summary: '',
    reason: 'Manually created from this document.',
    quick: [],
    origin: 'Manual',
  };
  actions.push(action);
  actionTypeFilter = 'all';
  activeFilter = 'active';
  document.querySelectorAll('[data-action-type]').forEach((button) => button.classList.toggle('active', button.dataset.actionType === 'all'));
  document.querySelectorAll('[data-filter]').forEach((button) => button.classList.toggle('active', button.dataset.filter === 'active'));
  document.querySelector('#queue-search').value = '';
  updateActionSectionName();
  applySearchAndFilter();
  selectAction(action.id);
  document.querySelector('#action-title-input').focus();
  showToast('New action added inline. Complete its fields, then save.');
});
document.querySelector('#next-document').addEventListener('click', () => {
  if (actionDirty) {
    showToast('Save the selected action before moving to another document.');
    document.querySelector('#save-action').focus();
    return;
  }
  const groups = documentGroups();
  const selectedAction = actions.find((action) => action.id === selectedId);
  const currentIndex = selectedAction
    ? groups.findIndex((group) => group.key === documentKey(selectedAction))
    : -1;
  const next = groups[currentIndex + 1];
  if (!next) {
    showToast('This is the last document in the current queue.');
    return;
  }
  selectAction(next.primary.id);
  list.querySelector(`[data-document-key="${next.key}"]`)?.focus();
});
document.querySelector('#done-menu-toggle').addEventListener('click', () => {
  const menu = document.querySelector('#done-menu');
  const expanded = !menu.hidden;
  document.querySelectorAll('[data-action-menu-panel]').forEach((panel) => { panel.hidden = true; });
  document.querySelectorAll('[data-action-menu]').forEach((trigger) => trigger.setAttribute('aria-expanded', 'false'));
  menu.hidden = expanded;
  document.querySelector('#done-menu-toggle').setAttribute('aria-expanded', String(!expanded));
});
document.querySelector('#done-selected-action').addEventListener('click', () => {
  document.querySelector('#done-menu').hidden = true;
  document.querySelector('#done-menu-toggle').setAttribute('aria-expanded', 'false');
  document.querySelector('#done-action').click();
});
document.querySelector('#done-all-actions').addEventListener('click', () => {
  if (actionDirty) {
    showToast('Save the selected action before completing all actions.');
    document.querySelector('#save-action').focus();
    return;
  }
  const current = actions.find((action) => action.id === selectedId);
  if (!current) return;
  pendingBulkDoneIds = actions
    .filter((action) => documentKey(action) === documentKey(current) && !resolvedActions.has(action.id))
    .map((action) => action.id);
  document.querySelector('#bulk-done-list').innerHTML = pendingBulkDoneIds
    .map((id) => `<li>${actions.find((action) => action.id === id).title}</li>`)
    .join('');
  document.querySelector('#done-menu').hidden = true;
  document.querySelector('#done-menu-toggle').setAttribute('aria-expanded', 'false');
  document.querySelector('#bulk-done-confirmation').hidden = false;
  document.querySelector('#cancel-bulk-done').focus();
});
document.querySelector('#cancel-bulk-done').addEventListener('click', () => {
  pendingBulkDoneIds = [];
  document.querySelector('#bulk-done-confirmation').hidden = true;
});
document.querySelector('#confirm-bulk-done').addEventListener('click', () => {
  const completed = pendingBulkDoneIds.length;
  pendingBulkDoneIds.forEach((id) => resolvedActions.set(id, 'done'));
  pendingBulkDoneIds = [];
  document.querySelector('#bulk-done-confirmation').hidden = true;
  applySearchAndFilter();
  showToast(`${completed} actions marked done. Moving to the next document.`);
});
list.addEventListener('keydown', (event) => {
  if (!['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return;
  event.preventDefault();
  const groups = documentGroups();
  const selectedAction = actions.find((action) => action.id === selectedId);
  const selectedKey = selectedAction ? documentKey(selectedAction) : null;
  const index = Math.max(0, groups.findIndex((group) => group.key === selectedKey));
  if (event.key === 'Enter') {
    list.querySelector(`[data-document-key="${selectedKey}"]`)?.click();
    return;
  }
  const nextIndex = event.key === 'ArrowDown'
    ? Math.min(groups.length - 1, index + 1)
    : Math.max(0, index - 1);
  if (groups[nextIndex]) {
    selectAction(groups[nextIndex].primary.id);
    list.querySelector(`[data-document-key="${groups[nextIndex].key}"]`)?.focus();
  }
});
reviewList.addEventListener('keydown', (event) => {
  if (!['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return;
  event.preventDefault();
  const index = Math.max(0, visibleReviews.findIndex((item) => item.id === selectedReviewId));
  if (event.key === 'Enter') {
    reviewList.querySelector(`[data-review-id="${selectedReviewId}"]`)?.click();
    return;
  }
  const nextIndex = event.key === 'ArrowDown'
    ? Math.min(visibleReviews.length - 1, index + 1)
    : Math.max(0, index - 1);
  if (visibleReviews[nextIndex]) {
    selectReview(visibleReviews[nextIndex].id);
    reviewList.querySelector(`[data-review-id="${visibleReviews[nextIndex].id}"]`)?.focus();
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (!document.querySelector('#bulk-done-confirmation').hidden) {
    document.querySelector('#cancel-bulk-done').click();
  } else if (!document.querySelector('#outcome-confirmation').hidden) {
    document.querySelector('#cancel-confirmation').click();
  } else if (!document.querySelector('#done-menu').hidden) {
    document.querySelector('#done-menu').hidden = true;
    document.querySelector('#done-menu-toggle').setAttribute('aria-expanded', 'false');
    document.querySelector('#done-menu-toggle').focus();
  } else if (!document.querySelector('#reminder-menu').hidden) {
    document.querySelector('#reminder-menu').hidden = true;
    document.querySelector('#remind-action').setAttribute('aria-expanded', 'false');
    document.querySelector('#remind-action').focus();
  } else if (!document.querySelector('#rationale').hidden) {
    document.querySelector('#rationale').hidden = true;
    document.querySelector('#open-rationale').focus();
  }
});

applySearchAndFilter();
selectedId = visibleActions[0]?.id || selectedId;
applyReviewSearchAndFilter();
selectedReviewId = visibleReviews[0]?.id || selectedReviewId;
selectAction(selectedId);
selectReview(selectedReviewId);
if (window.location.hash === '#needs-review') showView('needs-review');
