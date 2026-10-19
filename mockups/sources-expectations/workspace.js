const sourceItems = [...document.querySelectorAll("[data-source]")];
const sourceSearch = document.querySelector("#source-search");
const sourceName = document.querySelector("#source-name");
const sourceMeta = document.querySelector("#source-meta");
const sourceModeButtons = [...document.querySelectorAll("[data-source-mode]")];
const correspondentList = document.querySelector("#correspondent-list");
const signalList = document.querySelector("#signal-list");
const directoryList = document.querySelector("#directory-list");
const sourceViews = [...document.querySelectorAll(".source-view")];
const signalWorkspace = document.querySelector("#signal-workspace");
const directoryWorkspace = document.querySelector("#directory-workspace");
const railSummary = document.querySelector("#rail-summary");
const railListLabel = document.querySelector("#rail-list-label");
const railFooter = document.querySelector("#rail-footer");
const filterTrigger = document.querySelector("#source-filter-trigger");
const filterPopover = document.querySelector("#source-filter-popover");

sourceItems.forEach((item) => {
  item.addEventListener("click", () => {
    sourceItems.forEach((candidate) => candidate.classList.remove("active"));
    item.classList.add("active");
    if (sourceName) sourceName.textContent = item.dataset.name;
    if (sourceMeta) sourceMeta.textContent = item.dataset.meta;
  });
});

sourceSearch?.addEventListener("input", () => {
  const query = sourceSearch.value.trim().toLowerCase();
  const visibleItems = !signalList.hidden
    ? [...signalList.querySelectorAll(".source-item")]
    : !directoryList.hidden
      ? [...directoryList.querySelectorAll(".source-item")]
      : sourceItems;
  visibleItems.forEach((item) => {
    item.hidden = Boolean(query) && !item.textContent.toLowerCase().includes(query);
  });
});

sourceModeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const showSignals = button.dataset.sourceMode === "signals";
    const showDirectory = button.dataset.sourceMode === "directory";
    const showReview = button.dataset.sourceMode === "review";
    sourceModeButtons.forEach((candidate) => candidate.classList.toggle("active", candidate === button));
    correspondentList.hidden = !showReview;
    signalList.hidden = !showSignals;
    directoryList.hidden = !showDirectory;
    sourceViews.forEach((view) => { view.hidden = !showReview; });
    signalWorkspace.hidden = !showSignals;
    directoryWorkspace.hidden = !showDirectory;
    filterTrigger.hidden = showSignals;
    sourceSearch.placeholder = showSignals
      ? "Find account or obligation"
      : showDirectory
        ? "Find any correspondent"
        : "Find correspondent or series";
    railSummary.innerHTML = showSignals
      ? "<strong>4 Tyrion signals need reconciliation</strong>"
      : showDirectory
        ? "<strong>247 Paperless correspondents</strong>"
        : "<strong>12 correspondents need review</strong>";
    railListLabel.textContent = showSignals
      ? "Unresolved external signals"
      : showDirectory
        ? "All correspondents"
        : "Correspondents needing review";
    railFooter.textContent = showSignals
      ? "Tyrion signals are advisory. Resolve them to a Paperless-anchored document source or record that no document applies."
      : showDirectory
        ? "Most correspondents require no ongoing work. Dispositions keep one-off sources out of the review queue until evidence changes."
        : "The review queue contains only new or materially changed correspondents, not the complete Paperless directory.";
  });
});

filterTrigger?.addEventListener("click", () => {
  const open = filterPopover.hidden;
  filterPopover.hidden = !open;
  filterTrigger.setAttribute("aria-expanded", String(open));
});

filterPopover?.querySelector(".filter-apply")?.addEventListener("click", () => {
  filterPopover.hidden = true;
  filterTrigger.setAttribute("aria-expanded", "false");
});

document.querySelector("#clear-source-filters")?.addEventListener("click", () => {
  filterPopover.querySelectorAll("input").forEach((input) => { input.checked = false; });
  filterTrigger.querySelector("span").textContent = "0";
});

document.addEventListener("click", (event) => {
  if (!filterPopover || filterPopover.hidden) return;
  if (filterPopover.contains(event.target) || filterTrigger.contains(event.target)) return;
  filterPopover.hidden = true;
  filterTrigger.setAttribute("aria-expanded", "false");
});

document.querySelectorAll("[data-chip-group]").forEach((group) => {
  group.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    group.querySelectorAll("button").forEach((candidate) => candidate.classList.remove("active"));
    button.classList.add("active");
  });
});

document.querySelectorAll("[data-choice-group]").forEach((group) => {
  group.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    group.querySelectorAll("button").forEach((candidate) => candidate.classList.remove("active"));
    button.classList.add("active");
  });
});

const coverageRows = [...document.querySelectorAll("[data-coverage-kind]")];
document.querySelector("#coverage-filters")?.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  document.querySelectorAll("#coverage-filters button").forEach((candidate) => candidate.classList.remove("active"));
  button.classList.add("active");
  const kind = button.dataset.kind;
  coverageRows.forEach((row) => {
    row.hidden = kind !== "all" && row.dataset.coverageKind !== kind;
  });
});

const coverageSearch = document.querySelector("#coverage-search");
coverageSearch?.addEventListener("input", () => {
  const query = coverageSearch.value.trim().toLowerCase();
  coverageRows.forEach((row) => {
    row.hidden = Boolean(query) && !row.textContent.toLowerCase().includes(query);
  });
});
