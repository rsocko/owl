"use strict";

const byId = (id) => document.getElementById(id);
const fields = ["title", "correspondent", "type", "date", "storage", "tags"];
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
for (const doc of documents) doc.actionStatus = doc.assessment === "pay" ? "proposed" : "none";
const history = [
  { id: 1038, title: "Harbor Bank · September statement", source: "Automated", confidence: 98, sampled: false, reason: "Reference statement; no outstanding obligation.", fingerprint: "1038:r8:v3", time: "Oct 16, 05:42 UTC" },
  { id: 1040, title: "Northline · service information", source: "Human", confidence: 88, sampled: false, reason: "Reviewer confirmed informational notice.", fingerprint: "1040:r9:v3", time: "Oct 16, 05:46 UTC" },
  { id: 1036, title: "Cedar Dental · benefits summary", source: "Automated", confidence: 93, sampled: true, reason: "Benefits explanation, not a bill. Selected for quality audit.", fingerprint: "1036:r6:v3", time: "Oct 16, 05:50 UTC" }
];
const actions = [{ id: 1031, title: "Pay October water bill", provider: "Riverwater Utilities", amount: "$62.10", due: "Oct 24", completed: false }];
let current = documents[0];
let dirty = false;
let conflictPending = false;
let externalTitle = "";
let scenario = "ready";
let discoveryTimer;
let analysisTimer;
let analysisRunning = false;
let toastTimer;
let previewScope = null;
let auditEntry = null;
let actionReviewDocument = null;
const selection = new Set();
const existingTypes = new Set(["Receipt", "Invoice", "Statement"]);

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
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
  return pendingDocuments().filter((doc) => `${doc.short} ${doc.title} ${doc.correspondent}`.toLowerCase().includes(query));
}
function selectDocument(doc) {
  if (dirty && !window.confirm("Discard unsaved metadata changes and review another document?")) return false;
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
  byId("awaiting-count").textContent = `${pendingDocuments().length} documents`;
  byId("intake-count").textContent = pendingDocuments().length;
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
  byId("metadata-heading").textContent = "Document metadata";
  byId("assessment-title").textContent = current.assessment === "pay" ? "PAY · provisional action" : current.assessment === "review" ? "Needs human review" : "No action needed";
  byId("assessment-reason").textContent = current.reason;
  byId("assessment-confidence").textContent = `${current.confidence}% confidence`;
  byId("assessment-icon").textContent = current.assessment === "none" ? "✓" : "?";
  const actionCandidate = current.assessment === "pay";
  const uncertain = current.assessment === "review";
  byId("disposition-heading").textContent = actionCandidate ? "Proposed action found" : uncertain ? "Needs a closer look" : "Ready to file";
  byId("routing-help").textContent = actionCandidate
    ? "File the source now, or validate the suggested action without leaving Intake."
    : uncertain
      ? "Keep this document in review without creating trusted work."
      : "Saves metadata, removes Inbox, and records no action. Matching remains enabled.";
  byId("action-intake-workspace").hidden = !actionCandidate;
  byId("standard-decision-footer").hidden = actionCandidate;
  byId("standard-file-document").hidden = uncertain;
  byId("standard-needs-review").textContent = uncertain ? "Keep in Needs Review" : "Needs review";
  byId("intake-action-payee").textContent = current.provider;
  byId("intake-action-amount").textContent = current.amount;
  byId("intake-action-due").textContent = current.due || "Not extracted";
  byId("intake-action-reference").textContent = current.reference;
  selectIntakeDecisionTab("file");
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
  byId("save-metadata").disabled = !dirty || blocked || analysisRunning;
  byId("save-state").textContent = dirty ? "Unsaved changes" : "No unsaved changes";
  byId("save-state").className = `pill ${dirty ? "warning" : "neutral"}`;
  byId("file-document").disabled = blocked || analysisRunning;
  byId("file-only").disabled = blocked || analysisRunning;
  byId("standard-file-document").disabled = blocked || analysisRunning;
  byId("confirm-action").disabled = blocked || current.assessment !== "pay" || analysisRunning;
  byId("needs-review").disabled = blocked || analysisRunning;
  byId("standard-needs-review").disabled = blocked || analysisRunning;
  byId("analyze").disabled = dirty || blocked || analysisRunning;
  byId("analyze").textContent = analysisRunning ? "Analyzing…" : "Re-evaluate assessment";
  byId("analysis-hint").textContent = dirty ? "Save metadata before routing or re-evaluating." : current.stale ? "Targeted reanalysis pending · run now to bypass debounce." : "Evaluated against saved metadata.";
  document.querySelectorAll("[data-apply]").forEach((button) => {
    button.disabled = blocked || !["ready", "conflict"].includes(scenario) || analysisRunning;
  });
  byId("metadata-form").querySelectorAll("input:not([readonly]), select").forEach((field) => { field.disabled = blocked || analysisRunning; });
  byId("tag-pills").querySelectorAll("button").forEach((button) => { button.disabled = blocked || analysisRunning; });
}
function selectIntakeDecisionTab(tab) {
  const showAction = tab === "action";
  byId("file-tab").setAttribute("aria-selected", String(!showAction));
  byId("action-tab").setAttribute("aria-selected", String(showAction));
  byId("file-panel").hidden = showAction;
  byId("action-panel").hidden = !showAction;
}
function markDirty() {
  dirty = fields.some((field) => byId(`field-${field}`).value !== current[field]);
  updateSuggestionVisibility();
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
  byId("revision-label").textContent = `Loaded Paperless revision r${current.revision}`;
  updateFreshness();
  updateControls();
  renderQueue();
}
function saveMetadata() {
  commitMetadata(true);
  toast("Metadata saved. Assessment is stale; use Re-evaluate assessment to simulate the targeted rerun. No task created.");
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
  if (mode === "owl") saveMetadata();
  else {
    toast(mode === "paperless" ? "Current Paperless title kept. Other draft edits preserved; re-evaluate after saving." : "Source refreshed. Your draft is preserved for editing; save when ready.");
    byId("field-title").focus();
  }
}
function routeDocument(outcome, options = {}) {
  const doc = current;
  if (outcome !== "review") {
    if (doc.activeHistoryEntry) doc.activeHistoryEntry.reopened = false;
    doc.activeHistoryEntry = null;
  }
  if (outcome === "filed") {
    if (options.dismissAction) doc.actionStatus = "dismissed";
    const recordsNoAction = doc.assessment !== "pay" || options.dismissAction;
    if (recordsNoAction) {
      const entry = { id: doc.id, title: doc.title, source: "Human", confidence: doc.confidence, sampled: false, reason: doc.assessment === "pay" ? "Reviewer dismissed the PAY suggestion: no action needed." : "Reviewer confirmed no action needed.", fingerprint: `${doc.id}:r${doc.revision}:v3`, time: "Just now · demo" };
      history.unshift(entry);
      doc.activeHistoryEntry = entry;
    }
  }
  if (outcome === "action" && !actions.some((action) => action.id === doc.id)) {
    actions.push({ id: doc.id, title: `Pay ${doc.provider} invoice`, provider: doc.provider, amount: doc.amount, due: "Oct 30", completed: false });
    doc.actionStatus = "confirmed";
  }
  doc.status = outcome;
  if (outcome !== "review") doc.tags = doc.tags.split(",").filter((tag) => tag.trim().toLowerCase() !== "inbox").join(",").trim();
  selection.delete(doc.id);
  if (outcome !== "review") current = pendingDocuments()[0] || doc;
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
      : "Filed to no-action history. Inbox tag removed; matching remains enabled. No Mission Control task created."
    : outcome === "review"
      ? "Kept in Needs Review. Not sent to Mission Control."
      : "PAY action confirmed and document filed. Eligible for Mission Control; not marked paid.");
  if (!empty && outcome !== "review") byId("source-heading").scrollIntoView({ block: "nearest" });
}
function loadActionReview() {
  const reviewDoc = current.actionStatus === "proposed"
    ? current
    : documents.find((doc) => doc.actionStatus === "proposed");
  actionReviewDocument = reviewDoc || null;
  if (!reviewDoc) {
    byId("pending-action-review").hidden = true;
    return;
  }
  byId("pending-action-review").hidden = false;
  byId("review-action-title").textContent = `Pay ${reviewDoc.provider} invoice`;
  byId("review-action-subtitle").textContent = `Paperless #${reviewDoc.id} · ${reviewDoc.status === "filed" ? "filed from Inbox · " : ""}proposed PAY action`;
  byId("review-confidence").textContent = `${reviewDoc.confidence}% confidence`;
  byId("review-payee").textContent = reviewDoc.provider;
  byId("review-amount").textContent = reviewDoc.amount;
  byId("review-due").textContent = reviewDoc.due || "Not extracted";
  byId("review-reference").textContent = reviewDoc.reference;
  byId("review-reason").textContent = reviewDoc.reason;
  byId("review-source-kind").textContent = reviewDoc.paperKind;
  byId("review-source-title").textContent = reviewDoc.paperTitle;
  byId("review-source-description").textContent = reviewDoc.paperDescription;
  byId("review-source-callout").textContent = reviewDoc.callout;
}
function renderActions() {
  byId("trusted-actions").replaceChildren();
  for (const action of actions) {
    const item = node("article", undefined, "action-item");
    item.append(node("span", action.completed ? "Completed" : "PAY · confirmed", `pill ${action.completed ? "success" : "warning"}`), node("h3", action.title), node("p", `${action.amount} · due ${action.due} · Paperless #${action.id}`));
    item.append(node("div", action.completed ? "Explicit user completion · settlement recorded" : "Human-confirmed · eligible for Mission Control sync", "inline-meta"));
    const complete = node("button", action.completed ? "Completed" : "Mark completed", action.completed ? "" : "primary");
    complete.type = "button";
    complete.disabled = action.completed;
    complete.addEventListener("click", () => {
      action.completed = true;
      renderActions();
      toast("Explicit completion recorded. Intake tags and metadata remain independent.");
    });
    item.append(complete);
    byId("trusted-actions").append(item);
  }
  const confirmedCount = actions.filter((action) => !action.completed).length;
  const proposedCount = documents.filter((doc) => doc.actionStatus === "proposed").length;
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
  if (!dirty || analysisRunning) return;
  if (!validateMetadata()) return;
  if (conflictPending) showConflict();
  else saveMetadata();
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
  routeDocument("filed");
});
byId("file-only").addEventListener("click", () => {
  if (!validateMetadata()) return;
  commitMetadata(false);
  routeDocument("filed");
});
byId("standard-file-document").addEventListener("click", () => {
  if (!validateMetadata()) return;
  commitMetadata(false);
  routeDocument("filed");
});
byId("needs-review").addEventListener("click", () => {
  if (!validateMetadata()) return;
  commitMetadata(false);
  routeDocument("review");
});
byId("standard-needs-review").addEventListener("click", () => {
  if (!validateMetadata()) return;
  commitMetadata(false);
  routeDocument("review");
});
byId("confirm-action").addEventListener("click", () => {
  if (!validateMetadata()) return;
  commitMetadata(false);
  routeDocument("action");
});
byId("file-tab").addEventListener("click", () => selectIntakeDecisionTab("file"));
byId("action-tab").addEventListener("click", () => selectIntakeDecisionTab("action"));
byId("review-no-action").addEventListener("click", () => {
  if (!actionReviewDocument) return;
  if (!window.confirm("File this document and record that no action is needed?")) return;
  current = actionReviewDocument;
  routeDocument("filed", { dismissAction: true });
});
byId("review-needs-review").addEventListener("click", () => {
  if (!actionReviewDocument) return;
  current = actionReviewDocument;
  routeDocument("review");
});
byId("review-confirm").addEventListener("click", () => {
  if (!actionReviewDocument) return;
  current = actionReviewDocument;
  routeDocument("action");
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
byId("refresh-suggestions").addEventListener("click", () => {
  byId("scenario").value = "ready";
  setScenario("ready");
  toast("Native suggestions refreshed in simulation. Your unsaved metadata is preserved.");
});
byId("scenario").addEventListener("change", (event) => setScenario(event.target.value));
byId("queue-search").addEventListener("input", renderQueue);
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
