"use strict";

const byId = (id) => document.getElementById(id);
const fields = ["title", "correspondent", "type", "date", "storage", "tags"];
let nextActionDraftId = 1;
const documents = [
  {
    id: 1042, short: "Northline Energy · payment confirmation", provider: "Northline Energy",
    title: "Northline Energy — payment confirmation — 2026-10-14", type: "Receipt",
    suggestedType: "Payment confirmation", date: "2026-10-14", storage: "Household / Utilities",
    tags: "Inbox, Utilities", amount: "$84.20", confidence: 96, assessment: "none",
    due: null, reference: "Account •••• 4821", recurring: "Monthly utility payment evidence",
    reason: "A completed payment is evidence, not a new obligation. Keep it available for statement matching.",
    paperTitle: "Thank you for your payment.", paperKind: "PAYMENT CONFIRMATION",
    paperDescription: "Your payment has been received and the account balance is $0.00.",
    callout: "PAYMENT RECEIVED · BALANCE $0.00", correspondentId: 18, revision: 12, status: "pending", stale: false
  },
  {
    id: 1043, short: "Cedar Dental · October invoice", provider: "Cedar Dental",
    title: "Cedar Dental — invoice — 2026-10-15", type: "Invoice",
    suggestedType: "Invoice", date: "2026-10-15", storage: "Household / Health",
    tags: "Inbox, Health", amount: "$128.00", confidence: 94, assessment: "pay",
    due: "October 30, 2026", reference: "Account •••• 4821", recurring: "Likely monthly invoice",
    reason: "An unpaid balance is due October 30. Review the extracted payee, amount, and due date before confirming trusted work.",
    paperTitle: "Your account statement.", paperKind: "INVOICE",
    paperDescription: "Services have been billed. Please arrange payment of the balance shown below by October 30, 2026.",
    callout: "BALANCE DUE · October 30, 2026", correspondentId: 23, revision: 12, status: "pending", stale: false
  },
  {
    id: 1044, short: "Harbor Bank · account notice", provider: "Harbor Bank",
    title: "Harbor Bank — account notice — 2026-10-15", type: "Statement",
    suggestedType: "Statement", date: "2026-10-15", storage: "Household / Banking",
    tags: "Inbox, Banking", amount: "Not stated", confidence: 67, assessment: "review",
    due: "Not reliably extracted", reference: "Account •••• 4821", recurring: "Possible recurring account notice",
    reason: "The notice mentions a response, but the OCR does not reliably identify a deadline. Human review is needed.",
    paperTitle: "An update about your account.", paperKind: "ACCOUNT NOTICE",
    paperDescription: "Please review the enclosed account terms. The scanned response instructions are unclear.",
    callout: "REVIEW REQUIRED · Response instructions unclear", correspondentId: 31, revision: 12, status: "review", stale: false
  }
];
for (const doc of documents) doc.correspondent = doc.provider;
for (const doc of documents) {
  doc.actionStatus = doc.assessment === "pay" ? "proposed" : "none";
  doc.actionDrafts = doc.assessment === "pay" ? [createActionDraft(doc, "suggested")] : [];
  doc.customMetadata = doc.id === 1043
    ? [{ label: "Patient", value: "Morgan Lee" }, { label: "Service date", value: "2026-10-08" }, { label: "Billing category", value: "Dental care" }]
    : [];
  doc.ocr = {
    score: doc.id === 1044 ? 67 : doc.id === 1043 ? 94 : 98,
    language: "English",
    pages: 1,
    orientation: "Correct",
    regions: doc.id === 1044 ? 3 : 0,
    text: `${doc.paperKind}\n\n${doc.paperTitle}\n${doc.paperDescription}\n\n${doc.callout}`
  };
}
const history = [
  { id: 1038, title: "Harbor Bank · September statement", source: "Automated", confidence: 98, sampled: false, reason: "Reference statement; no outstanding obligation.", fingerprint: "1038:r8:v3", time: "Oct 16, 05:42 UTC" },
  { id: 1040, title: "Northline · service information", source: "Human", confidence: 88, sampled: false, reason: "Reviewer confirmed informational notice.", fingerprint: "1040:r9:v3", time: "Oct 16, 05:46 UTC" },
  { id: 1036, title: "Cedar Dental · benefits summary", source: "Automated", confidence: 93, sampled: true, reason: "Benefits explanation, not a bill. Selected for quality audit.", fingerprint: "1036:r6:v3", time: "Oct 16, 05:50 UTC" }
];
const actions = [{ id: 1031, title: "Pay October water bill", provider: "Riverwater Utilities", amount: "$62.10", due: "Oct 24", completed: false }];
let current = documents[0];
let dirty = false;
let actionDirty = false;
let actionDraftSnapshot = "";
let customMetadataSnapshot = "";
let conflictPending = false;
let externalTitle = "";
let scenario = "ready";
let inboxFilter = "all";
let discoveryTimer;
let analysisTimer;
let analysisRunning = false;
let toastTimer;
let previewScope = null;
let auditEntry = null;
let actionReviewDocument = null;
let activeWorkspaceTab = "metadata";
let activeQueueTab = "actions";
const selection = new Set();
const existingTypes = new Set(["Receipt", "Invoice", "Statement"]);

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function createActionDraft(doc, origin = "new") {
  const sequence = nextActionDraftId++;
  return {
    id: `${doc.id}-action-${sequence}`,
    origin,
    type: origin === "suggested" ? "PAY" : "FOLLOW_UP",
    title: origin === "suggested" ? `Pay ${doc.provider} invoice` : `Follow up on ${doc.provider} document`,
    payee: doc.provider,
    amount: origin === "suggested" ? doc.amount : "",
    due: origin === "suggested" ? (doc.due || "") : "",
    reference: doc.reference || "",
    completed: false,
    removed: false
  };
}
function visibleActionDrafts(doc = current) {
  return (doc.actionDrafts || []).filter((draft) => !draft.removed);
}
function activeActionDrafts(doc = current) {
  return visibleActionDrafts(doc).filter((draft) => !draft.completed);
}
function completedActionDrafts(doc = current) {
  return visibleActionDrafts(doc).filter((draft) => draft.completed);
}
function undoDraftCompletion(doc, draft) {
  const actionIndex = actions.findIndex((action) => action.sourceId === doc.id && action.draftId === draft.id);
  if (actionIndex >= 0) actions.splice(actionIndex, 1);
  draft.completed = false;
  commitActionDrafts(doc);
  renderActions();
  if (doc === current) renderIntakeActionState();
  toast(`“${draft.title}” restored as an active action.`);
}
function actionTypeLabel(type) {
  return { PAY: "Pay", FOLLOW_UP: "Follow up", CALL: "Call", REVIEW: "Review" }[type] || type;
}
function markActionDirty() {
  actionDirty = true;
  updateControls();
  if (actionReviewDocument) {
    byId("review-save").disabled = false;
    byId("queue-refresh").disabled = true;
  }
}
function labeledActionField(labelText, value, onInput, options = {}) {
  const label = node("label");
  if (options.wide) label.className = "wide";
  label.append(document.createTextNode(labelText));
  const input = options.select ? document.createElement("select") : document.createElement("input");
  if (options.select) {
    options.select.forEach(([valueOption, text]) => {
      const option = node("option", text);
      option.value = valueOption;
      input.append(option);
    });
  } else {
    input.type = options.type || "text";
    if (options.placeholder) input.placeholder = options.placeholder;
  }
  input.value = value;
  input.disabled = Boolean(options.disabled);
  input.addEventListener(options.select ? "change" : "input", () => onInput(input.value));
  label.append(input);
  return label;
}
function renderActionCards(containerId, doc, context) {
  const container = byId(containerId);
  container.replaceChildren();
  visibleActionDrafts(doc).forEach((draft, index) => {
    const card = node("article", undefined, `action-candidate-card${draft.completed ? " completed" : ""}`);
    const heading = node("div", undefined, "action-candidate-heading");
    heading.append(
      node("span", draft.completed ? "DONE" : draft.type, `pill ${draft.completed ? "success" : "purple"}`),
      node("strong", draft.title),
      node("span", draft.completed ? "Completed action" : `${draft.origin === "suggested" ? "Suggested" : "Added"} action ${index + 1}`, "muted")
    );
    const fieldsContainer = node("div", undefined, "inline-action-fields");
    fieldsContainer.append(
      labeledActionField("Action type", draft.type, (value) => { draft.type = value; heading.querySelector(".pill").textContent = value; markActionDirty(); }, { disabled: draft.completed, select: [["PAY", "Pay"], ["FOLLOW_UP", "Follow up"], ["CALL", "Call"], ["REVIEW", "Review"]] }),
      labeledActionField("Action title", draft.title, (value) => { draft.title = value; heading.querySelector("strong").textContent = value || "Untitled action"; markActionDirty(); }, { disabled: draft.completed, wide: true }),
      labeledActionField("Payee / owner", draft.payee, (value) => { draft.payee = value; markActionDirty(); }, { disabled: draft.completed }),
      labeledActionField("Amount", draft.amount, (value) => { draft.amount = value; markActionDirty(); }, { disabled: draft.completed, placeholder: "Optional" }),
      labeledActionField("Due date", draft.due, (value) => { draft.due = value; markActionDirty(); }, { disabled: draft.completed, placeholder: "Optional" }),
      labeledActionField("Reference", draft.reference, (value) => { draft.reference = value; markActionDirty(); }, { disabled: draft.completed, placeholder: "Optional" })
    );
    const controls = node("div", undefined, "action-card-controls");
    if (draft.completed) {
      const undo = node("button", "Undo completion");
      undo.type = "button";
      undo.addEventListener("click", () => undoDraftCompletion(doc, draft));
      controls.append(undo);
      card.append(heading, fieldsContainer, controls);
      container.append(card);
      return;
    }
    const remove = node("button", draft.origin === "suggested" ? "Not an action" : "Delete action", "danger-text");
    remove.type = "button";
    remove.addEventListener("click", () => {
      draft.removed = true;
      markActionDirty();
      renderActionCards(containerId, doc, context);
      if (context === "intake") renderIntakeActionState();
      else updateQueueActionState();
    });
    const complete = node("button", "Mark done");
    complete.type = "button";
    complete.addEventListener("click", () => {
      if (!actions.some((action) => action.sourceId === doc.id && action.draftId === draft.id)) {
        actions.push({ id: `${doc.id}-${draft.id}`, sourceId: doc.id, draftId: draft.id, type: draft.type, title: draft.title, provider: draft.payee || doc.provider, amount: draft.amount || "No amount", due: draft.due || "No due date", completed: true });
      }
      draft.completed = true;
      commitActionDrafts(doc);
      renderActionCards(containerId, doc, context);
      renderActions();
      renderIntakeActionState();
      toast(`“${draft.title}” recorded as already done. The document remains available for its own filing decision.`);
    });
    controls.append(remove, complete);
    card.append(heading, fieldsContainer, controls);
    container.append(card);
  });
}
function renderCustomMetadata() {
  const container = byId("custom-metadata-fields");
  container.replaceChildren();
  container.hidden = current.customMetadata.length === 0;
  current.customMetadata.forEach((field, index) => {
    const label = node("label");
    label.append(document.createTextNode(field.label));
    const input = document.createElement("input");
    input.value = field.value;
    input.addEventListener("input", () => {
      current.customMetadata[index].value = input.value;
      markDirty();
    });
    label.append(input);
    container.append(label);
  });
}
function renderQueueCustomMetadata(doc) {
  const container = byId("queue-custom-metadata");
  container.replaceChildren();
  doc.customMetadata.forEach((field) => {
    const item = node("div");
    item.append(node("span", field.label), node("strong", field.value));
    container.append(item);
  });
}
function updatePreviewMetadata() {
  const rawDate = byId("field-date").value;
  const formattedDate = rawDate
    ? new Date(`${rawDate}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
    : "No date";
  byId("preview-metadata-date").textContent = formattedDate;
  byId("preview-metadata-correspondent").textContent = byId("field-correspondent").value.trim() || "No correspondent";
  byId("preview-metadata-type").textContent = byId("field-type").value.trim() || "No type";
}
function ocrQuality(score) {
  if (score >= 90) return { level: "good", pill: "success", status: "Good quality" };
  if (score >= 75) return { level: "caution", pill: "warning", status: "Review quality" };
  return { level: "poor", pill: "danger", status: "Poor quality" };
}
function updateOcrIndicator(prefix, doc) {
  const quality = ocrQuality(doc.ocr.score);
  const tab = byId(prefix ? `${prefix}-ocr-tab` : "ocr-tab");
  const count = byId(prefix ? `${prefix}-ocr-quality-count` : "ocr-quality-count");
  const score = byId(prefix ? `${prefix}-ocr-score` : "ocr-score");
  const status = byId(prefix ? `${prefix}-ocr-status` : "ocr-status");
  count.textContent = quality.level === "poor" ? `! ${doc.ocr.score}` : doc.ocr.score;
  count.className = `tab-count ocr-quality-${quality.level}`;
  tab.classList.remove("ocr-quality-good", "ocr-quality-caution", "ocr-quality-poor");
  tab.classList.add(`ocr-quality-${quality.level}`);
  tab.setAttribute("aria-label", `OCR, ${quality.status.toLowerCase()}, score ${doc.ocr.score} out of 100`);
  score.textContent = doc.ocr.score;
  score.className = `ocr-score-${quality.level}`;
  status.textContent = quality.status;
  status.className = `pill ${quality.pill}`;
}
function loadOcrReview(doc = current) {
  updateOcrIndicator("", doc);
  byId("ocr-language").textContent = doc.ocr.language;
  byId("ocr-pages").textContent = `${doc.ocr.pages} ${doc.ocr.pages === 1 ? "page" : "pages"}`;
  byId("ocr-orientation").textContent = doc.ocr.orientation;
  byId("ocr-regions").textContent = `${doc.ocr.regions} detected`;
  byId("ocr-text").value = doc.ocr.text;
}
function toast(message) {
  clearTimeout(toastTimer);
  byId("toast").textContent = message;
  byId("toast").hidden = false;
  toastTimer = setTimeout(() => { byId("toast").hidden = true; }, 6500);
}
function navigate() {
  const route = ["intake", "actions", "history", "processing"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "intake";
  document.querySelectorAll(".screen").forEach((screen) => { screen.hidden = screen.id !== route; });
  document.querySelectorAll("[data-route]").forEach((link) => {
    if (link.dataset.route === route) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  if (route === "history") renderHistory();
  if (route === "actions") renderActions();
  window.scrollTo(0, 0);
  document.title = `OWL · ${ { intake: "Intake review", actions: "Action Queue", history: "No-action history", processing: "Processing freshness" }[route]} — UX proposal`;
}
function pendingDocuments() {
  return documents.filter((doc) => ["pending", "review"].includes(doc.status));
}
function visibleDocuments() {
  const query = byId("queue-search").value.trim().toLowerCase();
  return pendingDocuments().filter((doc) => {
    const matchesQuery = `${doc.short} ${doc.title} ${doc.correspondent}`.toLowerCase().includes(query);
    const matchesFilter = inboxFilter === "all"
      || (inboxFilter === "actions" && doc.assessment === "pay")
      || (inboxFilter === "missing-type" && !doc.type);
    return matchesQuery && matchesFilter;
  });
}
function selectDocument(doc) {
  if ((dirty || actionDirty) && !window.confirm("Discard unsaved metadata or action changes and review another document?")) return false;
  if (actionDirty) current.actionDrafts = JSON.parse(actionDraftSnapshot);
  current = doc;
  conflictPending = false;
  loadDocument();
  renderQueue();
  return true;
}
function renderQueue() {
  const list = byId("document-list");
  list.replaceChildren();
  const visible = visibleDocuments();
  for (const doc of visible) {
    const row = node("div", undefined, `document-row${doc === current ? " selected" : ""}`);
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selection.has(doc.id);
    checkbox.setAttribute("aria-label", `Select ${doc.short}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selection.add(doc.id);
      else selection.delete(doc.id);
      updateBulkControls();
    });
    const thumbnail = node("span", undefined, "document-thumb");
    thumbnail.setAttribute("aria-hidden", "true");
    thumbnail.textContent = doc.type.slice(0, 3).toUpperCase();
    const button = node("button", undefined, "document-select");
    button.type = "button";
    button.setAttribute("aria-pressed", String(doc === current));
    const kind = doc.status === "review" ? "Needs Review" : doc.assessment === "none" ? "Likely no action" : "Action candidate";
    button.append(node("span", kind, `pill ${doc.status === "review" ? "warning" : doc.assessment === "none" ? "success" : "purple"}`), node("strong", doc.short), node("small", `#${doc.id} · ${doc.confidence}% confidence`));
    button.addEventListener("click", () => selectDocument(doc));
    row.append(checkbox, thumbnail, button);
    list.append(row);
  }
  byId("queue-empty").hidden = visible.length > 0;
  const pending = pendingDocuments();
  byId("awaiting-count").textContent = `${pending.length} documents`;
  byId("intake-count").textContent = pending.length;
  document.querySelector('[data-inbox-filter="all"] span').textContent = pending.length;
  document.querySelector('[data-inbox-filter="actions"] span').textContent = pending.filter((doc) => doc.assessment === "pay").length;
  document.querySelector('[data-inbox-filter="missing-type"] span').textContent = pending.filter((doc) => !doc.type).length;
  updateBulkControls();
}
function updateBulkControls() {
  const visible = visibleDocuments();
  const selectedVisible = visible.filter((doc) => selection.has(doc.id)).length;
  byId("select-all").checked = visible.length > 0 && selectedVisible === visible.length;
  byId("select-all").indeterminate = selectedVisible > 0 && selectedVisible < visible.length;
  byId("bulk-review").disabled = selectedVisible === 0;
  byId("bulk-review").textContent = selectedVisible ? `Mark ${selectedVisible} needs review` : "Mark needs review";
}
function tagValues() {
  return byId("field-tags").value.split(",").map((tag) => tag.trim()).filter(Boolean);
}
function setTags(tags) {
  byId("field-tags").value = [...new Set(tags)].join(", ");
  renderTags();
  markDirty();
}
function renderTags() {
  const container = byId("tag-pills");
  container.replaceChildren();
  for (const tag of tagValues()) {
    const pill = node("span", tag, "tag-pill");
    const remove = node("button", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${tag} tag`);
    remove.addEventListener("click", () => setTags(tagValues().filter((value) => value !== tag)));
    pill.append(remove);
    container.append(pill);
  }
}
function updateSuggestionVisibility() {
  const suggestions = {
    title: current.title,
    correspondent: current.provider,
    type: current.suggestedType
  };
  let visibleCount = 0;
  document.querySelectorAll("[data-suggestion]").forEach((element) => {
    const field = element.dataset.suggestion;
    const differs = byId(`field-${field}`).value.trim() !== suggestions[field].trim();
    element.hidden = !["ready", "conflict"].includes(scenario) || !differs;
    if (!element.hidden) visibleCount += 1;
  });
  byId("suggestions-bar").hidden = visibleCount === 0;
}
function loadDocument() {
  fields.forEach((field) => { byId(`field-${field}`).value = current[field]; });
  renderTags();
  customMetadataSnapshot = JSON.stringify(current.customMetadata);
  renderCustomMetadata();
  updatePreviewMetadata();
  loadOcrReview();
  byId("source-heading").textContent = current.short;
  byId("paper-provider").textContent = current.provider.toUpperCase();
  byId("paper-kind").textContent = current.paperKind;
  byId("paper-title").textContent = current.paperTitle;
  byId("paper-description").textContent = current.paperDescription;
  byId("paper-date").textContent = new Date(`${current.date}T12:00:00Z`).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
  byId("paper-amount").textContent = current.amount;
  byId("paper-amount-label").textContent = current.assessment === "pay" ? "Balance due" : current.assessment === "none" ? "Amount received" : "Amount";
  byId("paper-callout").textContent = current.callout;
  byId("source-id").textContent = `Paperless #${current.id}`;
  byId("suggested-title").textContent = current.title;
  byId("suggested-correspondent").textContent = current.provider;
  document.querySelector('[data-suggestion="correspondent"] small').textContent = `Traditional · existing #${current.correspondentId}`;
  byId("suggested-type").textContent = current.suggestedType;
  byId("type-provenance").textContent = existingTypes.has(current.suggestedType) ? "Traditional · existing type" : "Paperless AI · new name";
  byId("assessment-reason").textContent = current.reason;
  byId("assessment-confidence").textContent = `${current.confidence}% confidence`;
  byId("assessment-icon").textContent = current.assessment === "none" ? "✓" : "?";
  const uncertain = current.assessment === "review";
  byId("disposition-heading").textContent = "OWL analysis";
  byId("no-action-title").textContent = uncertain ? "Action details need human interpretation" : "No action details to confirm";
  byId("no-action-message").textContent = uncertain
    ? "The source may require a response, but the deadline and obligation were not reliably extracted."
    : "File the document when its metadata is ready, or keep it in Inbox for another reviewer.";
  actionDraftSnapshot = JSON.stringify(current.actionDrafts);
  actionDirty = false;
  renderIntakeActionState();
  selectWorkspaceTab("metadata");
  updateSuggestionVisibility();
  dirty = false;
  byId("revision-label").textContent = `Loaded Paperless revision r${current.revision}`;
  byId("paper-title").closest(".paper").classList.remove("zoomed");
  byId("zoom-preview").textContent = "Zoom +";
  byId("zoom-preview").setAttribute("aria-pressed", "false");
  updateFreshness();
  updateControls();
}
function updateFreshness() {
  byId("freshness").textContent = current.stale ? "Stale · metadata changed" : `Current · source r${current.revision}`;
  byId("freshness").className = `pill ${current.stale ? "warning" : "success"}`;
  byId("stale-notice").hidden = !current.stale;
  byId("analyze").hidden = !current.stale;
  byId("assessment-time").textContent = current.stale ? "Previous result retained" : current.evaluated ? "Evaluated just now" : "Evaluated 2 minutes ago";
  byId("freshness-metric").textContent = `${documents.filter((doc) => doc.stale).length + 1} stale`;
}
function updateControls() {
  const blocked = scenario === "unauthorized" || !pendingDocuments().includes(current);
  const hasUnsavedChanges = dirty || actionDirty;
  byId("save-metadata").disabled = !hasUnsavedChanges || blocked || analysisRunning;
  byId("save-state").textContent = hasUnsavedChanges ? "Unsaved changes" : "No unsaved changes";
  byId("save-state").className = `pill ${hasUnsavedChanges ? "warning" : "neutral"}`;
  byId("file-document").disabled = blocked || analysisRunning;
  byId("confirm-action").disabled = blocked || activeActionDrafts().length === 0 || analysisRunning;
  byId("needs-review").disabled = blocked || analysisRunning;
  byId("analyze").disabled = dirty || blocked || analysisRunning;
  byId("refresh-review").disabled = hasUnsavedChanges || blocked || analysisRunning;
  byId("analyze").textContent = analysisRunning ? "Analyzing…" : "Re-evaluate assessment";
  byId("analysis-hint").textContent = dirty ? "Save metadata before routing or re-evaluating." : current.stale ? "Targeted reanalysis pending · run now to bypass debounce." : "Evaluated against saved metadata.";
  document.querySelectorAll("[data-apply]").forEach((button) => {
    button.disabled = blocked || !["ready", "conflict"].includes(scenario) || analysisRunning;
  });
  byId("metadata-form").querySelectorAll("input:not([readonly]), select").forEach((field) => { field.disabled = blocked || analysisRunning; });
  byId("tag-pills").querySelectorAll("button").forEach((button) => { button.disabled = blocked || analysisRunning; });
  updateWorkspaceActions();
}
function updateWorkspaceActions() {
  const showActions = activeWorkspaceTab === "actions";
  const showOcr = activeWorkspaceTab === "ocr";
  const actionCount = activeActionDrafts().length;
  const completedCount = completedActionDrafts().length;
  const actionCandidate = actionCount > 0;
  const uncertain = current.assessment === "review";
  byId("confirm-action").hidden = !showActions || !actionCandidate;
  byId("confirm-action").textContent = `Confirm ${actionCount > 1 ? `${actionCount} actions` : "action"} & file`;
  byId("file-document").hidden = uncertain;
  byId("file-document").textContent = showActions && actionCandidate ? "File only" : "Save & file";
  byId("file-document").classList.toggle("primary", !showActions || !actionCandidate);
  byId("routing-help").textContent = showActions
    ? actionCandidate
      ? "Confirm the suggested action and file, or file only and leave it pending in Action Queue."
      : completedCount
        ? "The action is recorded as completed. File the document when its metadata is ready."
      : uncertain
        ? "Keep this document in Inbox until the obligation can be interpreted."
        : "No action was suggested. Filing records a no-action decision and removes Inbox."
    : showOcr
      ? "Review OCR quality before filing. Re-running OCR can change later metadata and action analysis."
      : "Save metadata without leaving Inbox, or finish filing this document.";
}
function renderIntakeActionState() {
  const count = activeActionDrafts().length;
  const completedCount = completedActionDrafts().length;
  byId("intake-action-count").textContent = visibleActionDrafts().length;
  byId("assessment-title").textContent = count
    ? `${count} suggested ${count === 1 ? "action" : "actions"}`
    : completedCount
      ? `${completedCount} completed ${completedCount === 1 ? "action" : "actions"}`
      : current.assessment === "review" ? "Action interpretation needed" : "No suggested actions";
  byId("no-action-state").hidden = visibleActionDrafts().length > 0;
  renderActionCards("action-candidates", current, "intake");
  updateWorkspaceActions();
}
function selectWorkspaceTab(tab) {
  activeWorkspaceTab = tab;
  const showActions = tab === "actions";
  const showOcr = tab === "ocr";
  byId("metadata-tab").setAttribute("aria-selected", String(!showActions && !showOcr));
  byId("actions-tab").setAttribute("aria-selected", String(showActions));
  byId("ocr-tab").setAttribute("aria-selected", String(showOcr));
  byId("metadata-panel").hidden = showActions || showOcr;
  byId("actions-panel").hidden = !showActions;
  byId("ocr-panel").hidden = !showOcr;
  updateWorkspaceActions();
}
function markDirty() {
  dirty = fields.some((field) => byId(`field-${field}`).value !== current[field]) || JSON.stringify(current.customMetadata) !== customMetadataSnapshot;
  updateSuggestionVisibility();
  updatePreviewMetadata();
  updateControls();
}
function validateMetadata() {
  if (!byId("field-title").value.trim()) {
    toast("Add a document title before continuing.");
    byId("field-title").focus();
    return false;
  }
  if (!existingTypes.has(byId("field-type").value.trim())) {
    toast("Choose an existing document type, or approve the new Paperless AI suggestion first.");
    byId("field-type").focus();
    return false;
  }
  return true;
}
function commitMetadata(markAssessmentStale = true) {
  if (!dirty) return;
  fields.forEach((field) => { current[field] = byId(`field-${field}`).value.trim(); });
  current.revision += 1;
  current.stale = markAssessmentStale;
  dirty = false;
  customMetadataSnapshot = JSON.stringify(current.customMetadata);
  byId("revision-label").textContent = `Loaded Paperless revision r${current.revision}`;
  updateFreshness();
  updateControls();
  renderQueue();
}
function commitActionDrafts(doc = current) {
  const count = activeActionDrafts(doc).length;
  doc.actionStatus = count ? "proposed" : completedActionDrafts(doc).length ? "confirmed" : doc.assessment === "pay" ? "dismissed" : "none";
  actionDirty = false;
  if (doc === current) actionDraftSnapshot = JSON.stringify(doc.actionDrafts);
}
function saveChanges() {
  const metadataChanged = dirty;
  commitMetadata(metadataChanged);
  commitActionDrafts();
  updateControls();
  renderActions();
  toast(metadataChanged
    ? "Changes saved. Metadata changed, so the assessment is stale until refreshed."
    : "Action corrections saved. The document remains in Inbox until you choose a disposition.");
}
function showConflict() {
  externalTitle = `${current.provider} — updated in Paperless mobile`;
  byId("conflict-local").textContent = byId("field-title").value;
  byId("conflict-remote").textContent = externalTitle;
  byId("conflict-local-revision").textContent = `Based on revision r${current.revision}`;
  byId("conflict-remote-revision").textContent = `Updated in Paperless mobile · revision r${current.revision + 1}`;
  byId("conflict-dialog").showModal();
}
function resolveConflict(mode) {
  current.title = externalTitle;
  current.revision += 1;
  current.stale = true;
  conflictPending = false;
  scenario = "ready";
  byId("scenario").value = "ready";
  if (mode === "paperless") byId("field-title").value = externalTitle;
  byId("conflict-dialog").close();
  byId("revision-label").textContent = `Loaded Paperless revision r${current.revision}`;
  markDirty();
  updateFreshness();
  if (mode === "owl") saveChanges();
  else {
    toast(mode === "paperless" ? "Current Paperless title kept. Other draft edits preserved; re-evaluate after saving." : "Source refreshed. Your draft is preserved for editing; save when ready.");
    byId("field-title").focus();
  }
}
function routeDocument(outcome, options = {}) {
  const doc = current;
  const nextPendingDocument = pendingDocuments().find((candidate) => candidate !== doc) || doc;
  if (outcome !== "review") {
    if (doc.activeHistoryEntry) doc.activeHistoryEntry.reopened = false;
    doc.activeHistoryEntry = null;
  }
  if (outcome === "filed") {
    if (options.dismissAction) doc.actionStatus = "dismissed";
    const recordsNoAction = ["none", "dismissed"].includes(doc.actionStatus) || options.dismissAction;
    if (recordsNoAction) {
      const entry = { id: doc.id, title: doc.title, source: "Human", confidence: doc.confidence, sampled: false, reason: doc.assessment === "pay" ? "Reviewer dismissed the PAY suggestion: no action needed." : "Reviewer confirmed no action needed.", fingerprint: `${doc.id}:r${doc.revision}:v3`, time: "Just now · demo" };
      history.unshift(entry);
      doc.activeHistoryEntry = entry;
    }
  }
  if (outcome === "action") {
    activeActionDrafts(doc).forEach((draft) => {
      if (actions.some((action) => action.sourceId === doc.id && action.draftId === draft.id)) return;
      actions.push({ id: `${doc.id}-${draft.id}`, sourceId: doc.id, draftId: draft.id, type: draft.type, title: draft.title, provider: draft.payee || doc.provider, amount: draft.amount || "No amount", due: draft.due || "No due date", completed: false });
    });
    doc.actionStatus = "confirmed";
  }
  doc.status = outcome;
  if (outcome !== "review") doc.tags = doc.tags.split(",").filter((tag) => tag.trim().toLowerCase() !== "inbox").join(",").trim();
  selection.delete(doc.id);
  if (outcome !== "review") current = pendingDocuments()[0] || doc;
  else if (options.advance) current = nextPendingDocument;
  loadDocument();
  renderQueue();
  const empty = pendingDocuments().length === 0;
  byId("intake-layout").hidden = empty;
  byId("inbox-empty").hidden = !empty;
  renderActions();
  renderHistory();
  toast(outcome === "filed"
    ? doc.actionStatus === "proposed"
      ? "Document filed and Inbox tag removed. The PAY suggestion remains in Action Queue for later review."
      : doc.actionStatus === "confirmed"
        ? "Document filed and Inbox tag removed. The completed action remains visible in action history."
        : "Filed to no-action history. Inbox tag removed; matching remains enabled. No Mission Control task created."
    : outcome === "review"
      ? doc.assessment === "pay"
        ? options.advance
          ? "Deferred as Needs Review. The document remains in Inbox and the next document is ready."
          : "Kept in Inbox as Needs Review. The PAY suggestion remains unconfirmed and no trusted work was created."
        : "Kept in Needs Review. Not sent to Mission Control."
      : "PAY action confirmed and document filed. Eligible for Mission Control; not marked paid.");
  if (!empty && outcome !== "review") byId("source-heading").scrollIntoView({ block: "nearest" });
}
function loadActionReview() {
  const retainedReview = actionReviewDocument && visibleActionDrafts(actionReviewDocument).length
    ? actionReviewDocument
    : null;
  const reviewDoc = current.actionStatus === "proposed"
    ? current
    : retainedReview || documents.find((doc) => doc.actionStatus === "proposed");
  actionReviewDocument = reviewDoc || null;
  if (!reviewDoc) {
    byId("pending-action-review").hidden = true;
    return;
  }
  byId("pending-action-review").hidden = false;
  byId("review-action-title").textContent = `${reviewDoc.provider} document actions`;
  const activeCount = activeActionDrafts(reviewDoc).length;
  byId("review-action-subtitle").textContent = `Paperless #${reviewDoc.id} · ${reviewDoc.status === "filed" ? "filed from Inbox · " : ""}${activeCount ? "proposed actions" : "completed actions"}`;
  byId("review-confidence").textContent = `${reviewDoc.confidence}% confidence`;
  byId("review-reason").textContent = reviewDoc.reason;
  byId("queue-metadata-title").textContent = reviewDoc.title;
  byId("queue-metadata-correspondent").textContent = reviewDoc.correspondent;
  byId("queue-metadata-type").textContent = reviewDoc.type;
  byId("queue-metadata-date").textContent = new Date(`${reviewDoc.date}T12:00:00Z`).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
  byId("queue-preview-date").textContent = new Date(`${reviewDoc.date}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  byId("queue-preview-correspondent").textContent = reviewDoc.correspondent;
  byId("queue-preview-type").textContent = reviewDoc.type;
  renderQueueCustomMetadata(reviewDoc);
  updateOcrIndicator("queue", reviewDoc);
  byId("queue-ocr-text").value = reviewDoc.ocr.text;
  byId("review-source-kind").textContent = reviewDoc.paperKind;
  byId("review-source-title").textContent = reviewDoc.paperTitle;
  byId("review-source-description").textContent = reviewDoc.paperDescription;
  byId("review-source-callout").textContent = reviewDoc.callout;
  selectQueueTab("actions");
  updateQueueActionState();
}
function updateQueueActionState() {
  if (!actionReviewDocument) return;
  const count = activeActionDrafts(actionReviewDocument).length;
  const completedCount = completedActionDrafts(actionReviewDocument).length;
  byId("queue-action-count").textContent = visibleActionDrafts(actionReviewDocument).length;
  byId("review-save").disabled = !actionDirty;
  byId("queue-refresh").disabled = actionDirty;
  byId("review-confirm").disabled = count === 0;
  byId("review-confirm").textContent = `Confirm ${count > 1 ? `${count} actions` : "action"}`;
  byId("review-no-action").hidden = activeQueueTab !== "actions" || (count === 0 && completedCount > 0);
  renderActionCards("queue-action-candidates", actionReviewDocument, "queue");
}
function selectQueueTab(tab) {
  activeQueueTab = tab;
  const showActions = tab === "actions";
  const showOcr = tab === "ocr";
  byId("queue-metadata-tab").setAttribute("aria-selected", String(!showActions && !showOcr));
  byId("queue-actions-tab").setAttribute("aria-selected", String(showActions));
  byId("queue-ocr-tab").setAttribute("aria-selected", String(showOcr));
  byId("queue-metadata-panel").hidden = showActions || showOcr;
  byId("queue-actions-panel").hidden = !showActions;
  byId("queue-ocr-panel").hidden = !showOcr;
  byId("review-no-action").hidden = !showActions || Boolean(actionReviewDocument && activeActionDrafts(actionReviewDocument).length === 0 && completedActionDrafts(actionReviewDocument).length);
  byId("review-save").hidden = !showActions;
  byId("review-confirm").hidden = !showActions;
}
function renderActions() {
  byId("trusted-actions").replaceChildren();
  for (const action of actions) {
    const item = node("article", undefined, "action-item");
    item.append(node("span", action.completed ? "Completed" : `${actionTypeLabel(action.type || "PAY").toUpperCase()} · confirmed`, `pill ${action.completed ? "success" : "warning"}`), node("h3", action.title), node("p", `${action.amount} · due ${action.due} · Paperless #${action.sourceId || action.id}`));
    item.append(node("div", action.completed ? "Explicit user completion · settlement recorded" : "Human-confirmed · eligible for Mission Control sync", "inline-meta"));
    const complete = node("button", action.completed ? "Undo completion" : "Mark completed", action.completed ? "" : "primary");
    complete.type = "button";
    complete.addEventListener("click", () => {
      if (action.completed) {
        const source = documents.find((doc) => doc.id === action.sourceId);
        const draft = source?.actionDrafts.find((candidate) => candidate.id === action.draftId);
        if (source && draft) {
          undoDraftCompletion(source, draft);
          return;
        }
        action.completed = false;
      } else {
        action.completed = true;
      }
      renderActions();
      toast(action.completed
        ? "Explicit completion recorded. Intake tags and metadata remain independent."
        : "Completion undone. The action is active again.");
    });
    item.append(complete);
    byId("trusted-actions").append(item);
  }
  const confirmedCount = actions.filter((action) => !action.completed).length;
  const proposedCount = documents.reduce((count, doc) => count + (doc.actionStatus === "proposed" ? activeActionDrafts(doc).length : 0), 0);
  byId("action-count").textContent = confirmedCount + proposedCount;
  byId("confirmed-count").textContent = `${confirmedCount} active`;
  loadActionReview();
}
function isSuperseded(entry) {
  const doc = documents.find((candidate) => candidate.id === entry.id);
  return Boolean(doc && doc.activeHistoryEntry !== entry);
}
function allowReviewRemoval(entry) {
  if (current.id !== entry.id || !dirty) return true;
  if (!window.confirm("This review has unsaved metadata. Discard the draft before closing the review?")) return false;
  loadDocument();
  return true;
}
function renderHistory() {
  const source = byId("history-source").value;
  const confidence = byId("history-confidence").value;
  const entries = history.filter((entry) =>
    (source === "all" || entry.source === source) &&
    (confidence === "all" || (confidence === "high" ? entry.confidence >= 90 : entry.confidence < 90)) &&
    (!byId("history-sampled").checked || entry.sampled));
  byId("history-rows").replaceChildren();
  entries.forEach((entry) => {
    const row = node("tr");
    const title = node("td");
    title.append(node("strong", entry.title), node("p", entry.reason), node("small", `${entry.time} · source ${entry.fingerprint} · analyzer v3 / prompt v5`));
    const sourceCell = node("td");
    sourceCell.append(node("span", entry.source, `pill ${entry.source === "Human" ? "purple" : "neutral"}`));
    const review = node("td");
    if (entry.sampled) review.append(node("span", "Quality sample", "pill warning"));
    const inspect = node("button", entry.auditConfirmed ? "Audit confirmed · inspect" : entry.sampled ? "Audit sample" : "Inspect decision");
    inspect.type = "button";
    inspect.addEventListener("click", () => {
      auditEntry = entry;
      byId("audit-heading").textContent = entry.title;
      byId("audit-reason").textContent = entry.reason;
      byId("audit-provenance").textContent = `${entry.source} · ${entry.confidence}% confidence · ${entry.time} · fingerprint ${entry.fingerprint} · analyzer v3 / prompt v5`;
      byId("audit-superseded").hidden = !isSuperseded(entry);
      byId("audit-reopen").disabled = Boolean(entry.reopened) || isSuperseded(entry);
      byId("audit-confirm").disabled = isSuperseded(entry);
      byId("audit-dialog").showModal();
    });
    review.append(inspect);
    const button = node("button", entry.reopened ? "Reopened · undo" : "Reopen");
    button.type = "button";
    button.disabled = isSuperseded(entry);
    button.addEventListener("click", () => {
      if (isSuperseded(entry) || (entry.reopened && !allowReviewRemoval(entry))) return;
      entry.reopened = !entry.reopened;
      if (entry.reopened) ensureReviewDocument(entry);
      else {
        const existing = documents.find((doc) => doc.id === entry.id);
        if (existing) existing.status = "filed";
      }
      syncIntake();
      renderQueue();
      renderHistory();
      renderActions();
      toast(entry.reopened ? "Decision reopened in intake Needs Review. Original decision preserved; no Mission Control task created." : "Reopen undone. Original no-action decision retained.");
    });
    review.append(button);
    if (entry.reopened) review.append(node("small", "Needs Review · original decision preserved"));
    if (isSuperseded(entry)) review.append(node("small", "Superseded · historical record only"));
    row.append(title, sourceCell, node("td", `${entry.confidence}%`), review);
    byId("history-rows").append(row);
  });
  byId("history-count").textContent = `${entries.length} decisions`;
  byId("history-empty").hidden = entries.length > 0;
}
function ensureReviewDocument(entry) {
  const existing = documents.find((doc) => doc.id === entry.id);
  if (existing) { existing.status = "review"; existing.activeHistoryEntry = entry; return; }
  const provider = entry.title.split(" · ")[0];
  documents.push({
    id: entry.id, short: entry.title, title: entry.title, provider,
    correspondent: provider, correspondentId: 18, type: "Statement", suggestedType: "Statement",
    date: "2026-10-15", storage: "Household / Utilities", tags: "Audit sample",
    amount: "Not applicable", confidence: entry.confidence, assessment: "none",
    reason: entry.reason, paperTitle: "For your records.", paperKind: "REFERENCE DOCUMENT",
    paperDescription: "This document is not a request for payment. Retain it for your records.",
    callout: "REFERENCE · Human audit requested", revision: 12, status: "review", stale: false,
    activeHistoryEntry: entry
  });
}
function syncIntake() {
  const pending = pendingDocuments();
  if (!pending.includes(current) && pending.length && !dirty) {
    current = pending[0];
    loadDocument();
  }
  byId("intake-layout").hidden = !pending.length || scenario === "unauthorized" || scenario === "empty";
  byId("inbox-empty").hidden = (pending.length > 0 && scenario !== "empty") || scenario === "unauthorized";
  updateControls();
}
function setScenario(value) {
  scenario = value;
  byId("access-error").hidden = value !== "unauthorized";
  const isEmpty = value === "empty" || pendingDocuments().length === 0;
  byId("intake-layout").hidden = value === "unauthorized" || isEmpty;
  byId("inbox-empty").hidden = !isEmpty || value === "unauthorized";
  const messages = {
    loading: "Loading native Paperless suggestions… Metadata already loaded remains editable. Choose Ready to review to finish this simulated load.",
    unavailable: "Paperless AI is unavailable. No AI suggestions were returned; direct metadata editing and Paperless web/mobile remain supported. Refresh to retry this demo.",
    malformed: "Paperless returned malformed suggestions. They were not applied. Refresh to retry or continue with manual metadata edits."
  };
  byId("suggestion-status").hidden = !messages[value];
  byId("suggestion-status").textContent = messages[value] || "";
  updateSuggestionVisibility();
  conflictPending = value === "conflict";
  if (value === "conflict") {
    byId("field-title").value = `${current.provider} — reviewed title`;
    markDirty();
    toast("Conflict staged: edit metadata and press Save to compare against a newer Paperless revision.");
  }
  updateControls();
}
function addEvent(title, detail, status, tone) {
  const event = node("div", undefined, "event");
  const time = node("time", new Date().toISOString().slice(11, 19));
  const content = node("div");
  content.append(node("strong", title), node("p", detail));
  event.append(time, node("span", status, `pill ${tone}`), content);
  byId("event-list").prepend(event);
}

byId("metadata-form").addEventListener("input", markDirty);
byId("metadata-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if ((!dirty && !actionDirty) || analysisRunning) return;
  if (!validateMetadata()) return;
  if (conflictPending) showConflict();
  else saveChanges();
});
document.querySelectorAll("[data-apply]").forEach((button) => {
  button.addEventListener("click", () => {
    const field = button.dataset.apply;
    if (field === "type" && !existingTypes.has(current.suggestedType)) { byId("taxonomy-dialog").showModal(); return; }
    byId(`field-${field}`).value = field === "title" ? current.title : field === "type" ? current.suggestedType : current.provider;
    markDirty();
    toast(`Paperless ${field === "correspondent" || field === "type" ? "taxonomy" : "AI"} suggestion staged. Save metadata to apply it.`);
  });
});
byId("create-taxonomy").addEventListener("click", () => {
  existingTypes.add(current.suggestedType);
  byId("field-type").value = current.suggestedType;
  byId("type-provenance").textContent = "Paperless AI · creation approved in demo";
  byId("taxonomy-dialog").close();
  markDirty();
  toast("New type approved in simulation. Metadata is not saved yet.");
});
byId("cancel-taxonomy").addEventListener("click", () => byId("taxonomy-dialog").close());
byId("keep-paperless").addEventListener("click", () => resolveConflict("paperless"));
byId("re-edit").addEventListener("click", () => resolveConflict("edit"));
byId("apply-owl").addEventListener("click", () => resolveConflict("owl"));
byId("file-document").addEventListener("click", () => {
  if (!validateMetadata()) return;
  commitMetadata(false);
  commitActionDrafts();
  routeDocument("filed");
});
byId("needs-review").addEventListener("click", () => {
  if (!validateMetadata()) return;
  commitMetadata(false);
  commitActionDrafts();
  routeDocument("review", { advance: true });
});
byId("confirm-action").addEventListener("click", () => {
  if (!validateMetadata()) return;
  commitMetadata(false);
  commitActionDrafts();
  routeDocument("action");
});
byId("new-action").addEventListener("click", () => {
  current.actionDrafts.push(createActionDraft(current));
  markActionDirty();
  renderIntakeActionState();
});
byId("metadata-tab").addEventListener("click", () => selectWorkspaceTab("metadata"));
byId("actions-tab").addEventListener("click", () => selectWorkspaceTab("actions"));
byId("ocr-tab").addEventListener("click", () => selectWorkspaceTab("ocr"));
[byId("metadata-tab"), byId("actions-tab"), byId("ocr-tab")].forEach((tab, index, tabs) => {
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    const nextTab = tabs[nextIndex];
    selectWorkspaceTab(nextTab === byId("actions-tab") ? "actions" : nextTab === byId("ocr-tab") ? "ocr" : "metadata");
    nextTab.focus();
  });
});
byId("review-no-action").addEventListener("click", () => {
  if (!actionReviewDocument) return;
  if (!window.confirm("File this document and record that no action is needed?")) return;
  actionReviewDocument.actionDrafts.forEach((draft) => { draft.removed = true; });
  current = actionReviewDocument;
  commitActionDrafts(current);
  routeDocument("filed", { dismissAction: true });
});
byId("review-needs-review").addEventListener("click", () => {
  if (!actionReviewDocument) return;
  const deferred = actionReviewDocument;
  commitActionDrafts(actionReviewDocument);
  const next = documents.find((doc) => doc !== deferred && doc.actionStatus === "proposed");
  if (next) {
    current = next;
    actionReviewDocument = next;
  }
  renderActions();
  toast(next ? "Action corrections saved. The next proposed document is ready." : "Action corrections saved. No other proposed documents are waiting.");
});
byId("review-save").addEventListener("click", () => {
  if (!actionReviewDocument) return;
  commitActionDrafts(actionReviewDocument);
  renderActions();
  toast("Action corrections saved without confirming the work.");
});
byId("review-confirm").addEventListener("click", () => {
  if (!actionReviewDocument) return;
  current = actionReviewDocument;
  commitActionDrafts(current);
  routeDocument("action");
});
byId("queue-new-action").addEventListener("click", () => {
  if (!actionReviewDocument) return;
  actionReviewDocument.actionDrafts.push(createActionDraft(actionReviewDocument));
  actionDirty = true;
  updateQueueActionState();
});
byId("queue-metadata-tab").addEventListener("click", () => selectQueueTab("metadata"));
byId("queue-actions-tab").addEventListener("click", () => selectQueueTab("actions"));
byId("queue-ocr-tab").addEventListener("click", () => selectQueueTab("ocr"));
[byId("queue-metadata-tab"), byId("queue-actions-tab"), byId("queue-ocr-tab")].forEach((tab, index, tabs) => {
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    const nextTab = tabs[nextIndex];
    selectQueueTab(nextTab === byId("queue-actions-tab") ? "actions" : nextTab === byId("queue-ocr-tab") ? "ocr" : "metadata");
    nextTab.focus();
  });
});
byId("analyze").addEventListener("click", () => {
  const doc = current;
  analysisRunning = true;
  updateControls();
  byId("analysis-hint").textContent = "Running affected stages only · simulated";
  analysisTimer = setTimeout(() => {
    doc.stale = false;
    doc.evaluated = true;
    analysisRunning = false;
    updateFreshness();
    updateControls();
    addEvent(`#${doc.id} · Targeted analysis completed`, "Saved metadata fingerprint evaluated · prior confirmed decisions preserved", "Current", "success");
    toast(`Document #${doc.id} re-evaluated against saved metadata. You can now choose a disposition.`);
  }, 1100);
});
byId("refresh-review").addEventListener("click", () => {
  byId("scenario").value = "ready";
  setScenario("ready");
  current.stale = false;
  current.evaluated = true;
  updateFreshness();
  toast("Paperless suggestions and OWL action analysis refreshed for this document.");
});
byId("queue-refresh").addEventListener("click", () => {
  if (!actionReviewDocument || actionDirty) return;
  actionReviewDocument.stale = false;
  actionReviewDocument.evaluated = true;
  byId("review-confidence").textContent = `${actionReviewDocument.confidence}% confidence · refreshed`;
  toast("Paperless source data and action analysis refreshed.");
});
byId("rerun-ocr").addEventListener("click", () => {
  byId("rerun-ocr").disabled = true;
  byId("ocr-status").textContent = "OCR running…";
  setTimeout(() => {
    current.ocr.score = Math.min(100, current.ocr.score + 2);
    current.ocr.regions = Math.max(0, current.ocr.regions - 1);
    loadOcrReview();
    byId("rerun-ocr").disabled = false;
    toast("OCR rerun completed. Metadata and action analysis should be refreshed if the text changed.");
  }, 900);
});
byId("scenario").addEventListener("change", (event) => setScenario(event.target.value));
byId("queue-search").addEventListener("input", renderQueue);
document.querySelectorAll("[data-inbox-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    inboxFilter = button.dataset.inboxFilter;
    document.querySelectorAll("[data-inbox-filter]").forEach((item) => item.classList.toggle("active", item === button));
    renderQueue();
  });
});
byId("tag-input").addEventListener("keydown", (event) => {
  if (!["Enter", ","].includes(event.key)) return;
  event.preventDefault();
  const tag = event.currentTarget.value.trim().replace(/,$/, "");
  if (!tag) return;
  setTags([...tagValues(), tag]);
  event.currentTarget.value = "";
});
byId("tag-input").addEventListener("blur", (event) => {
  const tag = event.currentTarget.value.trim();
  if (!tag) return;
  setTags([...tagValues(), tag]);
  event.currentTarget.value = "";
});
byId("select-all").addEventListener("change", (event) => {
  visibleDocuments().forEach((doc) => { if (event.target.checked) selection.add(doc.id); else selection.delete(doc.id); });
  renderQueue();
});
byId("bulk-review").addEventListener("click", () => {
  visibleDocuments().filter((doc) => selection.has(doc.id)).forEach((doc) => { doc.status = "review"; selection.delete(doc.id); });
  renderQueue();
  renderActions();
  toast("Selected documents marked Needs Review. Metadata and drafts unchanged; no tasks created.");
});
byId("zoom-preview").addEventListener("click", () => {
  const zoomed = document.querySelector(".paper").classList.toggle("zoomed");
  byId("zoom-preview").setAttribute("aria-pressed", String(zoomed));
  byId("zoom-preview").textContent = zoomed ? "Fit page" : "Zoom +";
});
byId("paperless-link").addEventListener("click", () => toast(`Production handoff: open Paperless document #${current.id} in its web UI. This mockup has no real Paperless connection.`));
byId("review-paperless-link").addEventListener("click", () => toast(`Production handoff: open Paperless document #${actionReviewDocument?.id ?? current.id} in its web UI. This mockup has no real Paperless connection.`));
byId("prototype-guide").addEventListener("click", () => byId("prototype-guide-dialog").showModal());
byId("source-status-info").addEventListener("click", () => byId("source-status-dialog").showModal());
["history-source", "history-confidence", "history-sampled"].forEach((id) => byId(id).addEventListener("change", renderHistory));
function finishAudit(reopen) {
  if (!auditEntry) return;
  if (isSuperseded(auditEntry)) {
    toast("This historical decision is read-only because a newer disposition superseded it.");
    return;
  }
  if (!reopen && !allowReviewRemoval(auditEntry)) return;
  auditEntry.reopened = reopen;
  auditEntry.auditConfirmed = !reopen;
  if (reopen) ensureReviewDocument(auditEntry);
  else {
    const existing = documents.find((doc) => doc.id === auditEntry.id);
    if (existing) existing.status = "filed";
  }
  byId("audit-dialog").close();
  syncIntake();
  renderHistory();
  renderQueue();
  renderActions();
  toast(reopen ? "Audit sent to Needs Review. Original decision and provenance preserved; no trusted task created." : "Human audit confirmed no action. Original automated decision remains in the audit trail.");
}
byId("audit-reopen").addEventListener("click", () => finishAudit(true));
byId("audit-confirm").addEventListener("click", () => finishAudit(false));
byId("sample-rate").addEventListener("input", () => { byId("sample-output").value = `${byId("sample-rate").value}%`; });
byId("backfill-scope").addEventListener("change", () => {
  const scope = byId("backfill-scope").value;
  ["date", "ids", "view"].forEach((value) => {
    byId(`${value}-scope`).hidden = value !== scope;
    byId(`${value}-scope`).querySelectorAll("input, select").forEach((input) => { input.disabled = value !== scope; });
  });
});
byId("backfill-form").addEventListener("input", () => {
  previewScope = null;
  byId("backfill-preview").hidden = true;
  byId("run-backfill").hidden = true;
});
byId("backfill-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const scope = byId("backfill-scope").value;
  if ((scope === "date" && byId("backfill-from").value > byId("backfill-through").value) ||
      (scope === "ids" && Number(byId("backfill-first").value) > Number(byId("backfill-last").value))) {
    toast("The start of the range must not be after its end.");
    return;
  }
  previewScope = scope === "date" ? `${byId("backfill-from").value} through ${byId("backfill-through").value}` : scope === "ids" ? `IDs ${byId("backfill-first").value}–${byId("backfill-last").value}` : `saved view “${byId("backfill-view").value}”`;
  const candidates = scope === "ids" ? Math.min(100, Number(byId("backfill-last").value) - Number(byId("backfill-first").value) + 1) : scope === "view" ? 4 : 8;
  byId("backfill-preview").textContent = `Demo preview · ${previewScope}: ${candidates} candidate documents, up to ${Math.min(3, candidates)} stale analyses. Unchanged fingerprints skipped; confirmed decisions preserved. Counts are illustrative.`;
  byId("backfill-preview").hidden = false;
  byId("run-backfill").hidden = false;
});
byId("run-backfill").addEventListener("click", () => {
  if (!previewScope) return;
  byId("run-backfill").hidden = true;
  addEvent("Scoped analysis requested", `Bounded scope: ${previewScope} · demo only · existing lifecycle state retained`, "Queued", "purple");
  toast("Scoped analysis queued in simulation. No real document requests were sent.");
  previewScope = null;
});
byId("discover").addEventListener("click", () => {
  byId("discover").disabled = true;
  byId("discover").textContent = "Discovering…";
  discoveryTimer = setTimeout(() => {
    byId("cursor-value").textContent = "Oct 16 · 06:00";
    byId("discover").disabled = false;
    byId("discover").textContent = "Discover changed documents";
    addEvent("Incremental discovery completed", "2 changed documents · 1 unchanged projection skipped · 1 targeted rerun queued · illustrative counts", "Discovered", "success");
    toast("Discovery simulated. Cursor advanced with overlap; historical documents were not all reprocessed.");
  }, 900);
});
byId("reset-demo").addEventListener("click", () => {
  if (!window.confirm("Reset all simulated decisions and edits?")) return;
  clearTimeout(discoveryTimer);
  clearTimeout(analysisTimer);
  location.reload();
});
window.addEventListener("hashchange", navigate);
window.addEventListener("keydown", (event) => {
  if (!["ArrowUp", "ArrowDown"].includes(event.key) || byId("intake").hidden) return;
  const interactiveTarget = event.target.closest("input, select, textarea, button, a, dialog");
  if (interactiveTarget && !event.target.closest(".document-select")) return;
  const visible = visibleDocuments();
  if (!visible.length) return;
  event.preventDefault();
  const currentIndex = Math.max(0, visible.indexOf(current));
  const nextIndex = event.key === "ArrowDown"
    ? Math.min(visible.length - 1, currentIndex + 1)
    : Math.max(0, currentIndex - 1);
  if (visible[nextIndex] !== current && selectDocument(visible[nextIndex])) {
    document.querySelector(".document-row.selected .document-select")?.focus();
  }
});
loadDocument();
renderQueue();
renderActions();
renderHistory();
navigate();
