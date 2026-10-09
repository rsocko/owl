export function renderHtml() {
    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OWL System Map</title>
  <style>
    :root {
      color-scheme: dark;
      --canvas: var(--background-color-default, #090b10);
      --panel: color-mix(in srgb, var(--canvas) 84%, white 6%);
      --panel-strong: color-mix(in srgb, var(--canvas) 73%, white 11%);
      --text: var(--text-color-default, #eef1f7);
      --muted: var(--text-color-muted, #929bab);
      --blue: var(--true-color-blue, #73a7ff);
      --green: #62d99b;
      --amber: #f4bc67;
      --purple: #aa8cff;
      --red: #ef8383;
      --line: color-mix(in srgb, var(--text) 13%, transparent);
    }
    * { box-sizing: border-box; }
    html, body { width: 100%; height: 100%; margin: 0; overflow: hidden; }
    body {
      color: var(--text);
      background:
        radial-gradient(circle at 20% 8%, color-mix(in srgb, var(--blue) 10%, transparent), transparent 32%),
        radial-gradient(circle at 84% 88%, color-mix(in srgb, var(--purple) 8%, transparent), transparent 30%),
        var(--canvas);
      font: var(--text-body-medium, 14px)/var(--leading-body-medium, 1.45) var(--font-sans, system-ui, sans-serif);
    }
    button, input, select { font: inherit; }
    button {
      min-height: 38px; border: 0; color: var(--text); background: transparent; cursor: pointer;
      transition: background-color 150ms, color 150ms, transform 150ms, opacity 150ms;
    }
    button:active { transform: scale(.97); }
    button:focus-visible, input:focus-visible, select:focus-visible {
      outline: 2px solid var(--color-focus-outline, var(--blue)); outline-offset: 2px;
    }
    h1, h2, h3, p { margin: 0; }
    .app { height: 100%; display: grid; grid-template-rows: auto 1fr; }
    .topbar {
      min-height: 76px; display: grid; grid-template-columns: minmax(220px, 1fr) auto minmax(280px, 1fr);
      align-items: center; gap: 14px; padding: 12px 16px; border-bottom: 1px solid var(--line);
      background: color-mix(in srgb, var(--canvas) 88%, transparent); backdrop-filter: blur(18px); z-index: 3;
    }
    .brand { display: flex; align-items: center; gap: 11px; min-width: 0; }
    .brand-mark {
      width: 40px; height: 40px; display: grid; place-items: center; flex: none;
      border-radius: 13px; color: #fff; font-size: 19px;
      background: linear-gradient(145deg, var(--blue), var(--purple));
      box-shadow: 0 9px 26px color-mix(in srgb, var(--blue) 24%, transparent);
    }
    h1 { font-size: var(--text-title-small, 16px); font-weight: var(--font-weight-semibold, 650); }
    .subtitle { color: var(--muted); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .segmented {
      display: flex; padding: 4px; border-radius: 14px; background: var(--panel);
      box-shadow: 0 0 0 1px rgba(255,255,255,.06);
    }
    .segmented button { min-height: 34px; padding: 0 13px; border-radius: 10px; color: var(--muted); }
    .segmented button.active { color: var(--text); background: var(--panel-strong); }
    .tools { display: flex; justify-content: flex-end; align-items: center; gap: 7px; }
    .search, .status-filter {
      height: 38px; border: 0; color: var(--text); background: var(--panel); border-radius: 11px;
      box-shadow: 0 0 0 1px rgba(255,255,255,.07);
    }
    .search { width: min(210px, 19vw); padding: 0 12px; }
    .status-filter { max-width: 120px; padding: 0 9px; }
    .tool, .icon { border-radius: 11px; background: var(--panel); box-shadow: 0 0 0 1px rgba(255,255,255,.06); }
    .tool { padding: 0 11px; color: var(--muted); }
    .tool.active { color: var(--blue); background: color-mix(in srgb, var(--blue) 12%, var(--panel)); }
    .icon { width: 38px; padding: 0; display: grid; place-items: center; font-size: 17px; }
    .workspace { min-height: 0; display: grid; grid-template-columns: 1fr 380px; }
    .graph-shell { min-width: 0; min-height: 0; position: relative; overflow: hidden; }
    .graph-shell::before {
      content: ""; position: absolute; inset: 0; pointer-events: none; opacity: .24;
      background-image: radial-gradient(circle, color-mix(in srgb, var(--text) 28%, transparent) 1px, transparent 1px);
      background-size: 24px 24px;
    }
    svg { width: 100%; height: 100%; display: block; touch-action: none; cursor: grab; }
    svg.panning { cursor: grabbing; }
    .edge { fill: none; stroke: color-mix(in srgb, var(--text) 20%, transparent); stroke-width: 1.4; vector-effect: non-scaling-stroke; }
    .edge.relation { stroke: color-mix(in srgb, var(--blue) 58%, transparent); stroke-dasharray: 6 5; }
    .edge.cross-system { stroke: color-mix(in srgb, var(--purple) 65%, transparent); stroke-dasharray: 3 5; }
    .edge.dim { opacity: .08; }
    .edge-label { fill: var(--muted); font-size: 9px; text-anchor: middle; pointer-events: none; }
    .node { cursor: pointer; transition: opacity 150ms; }
    .node.dim { opacity: .18; }
    .node-card {
      fill: color-mix(in srgb, var(--canvas) 76%, white 8%); stroke: rgba(255,255,255,.1);
      stroke-width: 1; rx: 15; filter: drop-shadow(0 8px 14px rgba(0,0,0,.2));
    }
    .node:hover .node-card { fill: color-mix(in srgb, var(--canvas) 65%, white 14%); }
    .node.selected .node-card { stroke: var(--blue); stroke-width: 2; }
    .node.planned .node-card, .node.proposed .node-card { stroke-dasharray: 5 4; }
    .node-title { fill: var(--text); font-size: 12px; font-weight: 650; pointer-events: none; }
    .node-kind { fill: var(--muted); font-size: 9px; letter-spacing: .07em; text-transform: uppercase; pointer-events: none; }
    .node-status { stroke-width: 2; fill: var(--canvas); }
    .expand-ring { fill: var(--panel-strong); stroke: rgba(255,255,255,.13); }
    .expand-glyph { fill: var(--text); font-size: 12px; text-anchor: middle; pointer-events: none; }
    .empty { position: absolute; inset: 0; display: none; place-items: center; color: var(--muted); pointer-events: none; }
    .hint {
      position: absolute; left: 16px; bottom: 16px; padding: 9px 12px; border-radius: 11px;
      color: var(--muted); background: color-mix(in srgb, var(--canvas) 82%, transparent);
      box-shadow: 0 0 0 1px rgba(255,255,255,.06); backdrop-filter: blur(12px); font-size: 11px;
    }
    .inspector {
      min-width: 0; overflow: auto; padding: 21px; border-left: 1px solid var(--line);
      background: color-mix(in srgb, var(--canvas) 88%, transparent);
    }
    .placeholder { min-height: 100%; display: grid; align-content: center; gap: 12px; color: var(--muted); text-align: center; }
    .placeholder-icon {
      width: 54px; height: 54px; display: grid; place-items: center; margin: 0 auto; border-radius: 18px;
      color: var(--blue); background: color-mix(in srgb, var(--blue) 12%, transparent);
    }
    .eyebrow { color: var(--blue); font-size: 10px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
    .card-title { margin-top: 6px; font-size: var(--text-title-large, 25px); line-height: 1.15; text-wrap: balance; }
    .card-summary { margin-top: 10px; color: color-mix(in srgb, var(--text) 78%, transparent); text-wrap: pretty; }
    .meta { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 16px; }
    .pill { padding: 5px 8px; border-radius: 999px; color: var(--muted); background: var(--panel); font-size: 11px; }
    .pill.status { color: var(--status-color); background: color-mix(in srgb, var(--status-color) 12%, transparent); }
    .section { margin-top: 23px; padding-top: 19px; border-top: 1px solid var(--line); }
    .section h3 { margin-bottom: 9px; color: var(--muted); font-size: 10px; letter-spacing: .11em; text-transform: uppercase; }
    .section p { color: color-mix(in srgb, var(--text) 74%, transparent); text-wrap: pretty; }
    .path {
      display: block; margin-top: 7px; padding: 9px 10px; border-radius: 10px; overflow-wrap: anywhere;
      color: color-mix(in srgb, var(--blue) 88%, white 3%); background: var(--panel);
      font: 11px/1.35 var(--font-mono, Consolas, monospace);
    }
    .stores, .related { display: grid; gap: 8px; }
    .store-card { padding: 11px; border-radius: 12px; background: var(--panel); box-shadow: 0 0 0 1px rgba(255,255,255,.05); }
    .store-head { display: flex; justify-content: space-between; gap: 10px; font-weight: 650; }
    .store-role { color: var(--purple); font-size: 10px; text-transform: uppercase; letter-spacing: .07em; }
    .store-card p { margin-top: 5px; font-size: 11px; }
    .store-location { display: block; margin-top: 7px; color: var(--muted); font: 10px/1.35 var(--font-mono, Consolas, monospace); overflow-wrap: anywhere; }
    .related button { min-height: 44px; padding: 8px 10px; border-radius: 11px; background: var(--panel); text-align: left; }
    .related button span { display: block; margin-top: 2px; color: var(--muted); font-size: 10px; }
    .legend { display: flex; flex-wrap: wrap; gap: 10px 14px; color: var(--muted); font-size: 10px; }
    .legend span::before { content: ""; display: inline-block; width: 7px; height: 7px; margin-right: 5px; border-radius: 50%; background: var(--dot); }
    @media (max-width: 1050px) {
      .topbar { grid-template-columns: 1fr auto; }
      .brand .subtitle, .tools .tool { display: none; }
      .segmented { grid-row: 2; grid-column: 1 / -1; justify-self: center; }
      .workspace { grid-template-columns: 1fr 320px; }
    }
  </style>
</head>
<body>
  <main class="app">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">◉</div>
        <div><h1>OWL System Map</h1><div class="subtitle" id="subtitle">Current and planned product capabilities</div></div>
      </div>
      <nav class="segmented" aria-label="Map lens">
        <button data-lens="feature" class="active">Capabilities</button>
        <button data-lens="architecture">Architecture</button>
        <button data-lens="entity">Entities & stores</button>
      </nav>
      <div class="tools">
        <input class="search" id="search" type="search" placeholder="Search map…" aria-label="Search map">
        <select class="status-filter" id="status" aria-label="Filter by status">
          <option value="">All states</option>
          <option value="active">Active</option>
          <option value="partial">Partial</option>
          <option value="planned">Planned</option>
          <option value="proposed">Proposed</option>
        </select>
        <button class="tool" id="layout">Hierarchy</button>
        <button class="tool active" id="relations">Relationships</button>
        <button class="icon" id="fit" title="Fit map" aria-label="Fit map">⌗</button>
        <button class="icon" id="expand" title="Expand all" aria-label="Expand all">＋</button>
      </div>
    </header>
    <div class="workspace">
      <section class="graph-shell">
        <svg id="graph" role="img" aria-label="Interactive OWL system map"><g id="viewport"></g></svg>
        <div class="empty" id="empty">No nodes match these filters.</div>
        <div class="hint">Select a node for definition and ownership · scroll to zoom · drag to rearrange</div>
      </section>
      <aside class="inspector" id="inspector" aria-live="polite"></aside>
    </div>
  </main>
  <script>
    const SVG_NS = "http://www.w3.org/2000/svg";
    const NODE_W = 192;
    const NODE_H = 66;
    const params = new URLSearchParams(location.search);
    const state = {
      graph: null,
      lens: params.get("lens") || "feature",
      selected: params.get("focus"),
      expanded: new Set(),
      mode: "hierarchy",
      showRelations: true,
      query: "",
      status: "",
      positions: new Map(),
      transform: { x: 42, y: 42, scale: 1 },
      dragging: null,
      panning: null,
      suppressClick: false,
    };
    const svg = document.getElementById("graph");
    const viewport = document.getElementById("viewport");
    const inspector = document.getElementById("inspector");
    const statusColors = { active: "#62d99b", partial: "#f4bc67", planned: "#aa8cff", proposed: "#ef8383" };
    const kindColors = {
      root: "#73a7ff", area: "#aa8cff", capability: "#62d99b", layer: "#73a7ff",
      component: "#62d99b", entity: "#f4bc67", store: "#aa8cff", external: "#ef8383",
    };
    const lensCopy = {
      feature: "Current and planned product capabilities",
      architecture: "Runtime boundaries, components, and integrations",
      entity: "Domain definitions, ownership, and physical storage",
    };
    const escapeHtml = (value = "") => String(value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[char]));
    const nodeById = (id) => state.graph?.nodes.find((node) => node.id === id);
    const lensNodes = () => state.graph.nodes.filter((node) => node.lens === state.lens);
    const childrenOf = (id) => lensNodes().filter((node) => node.parent === id);
    const relatedEdges = (id) => state.graph.edges.filter((edge) => edge.source === id || edge.target === id);
    const createSvg = (tag, attrs = {}) => {
      const element = document.createElementNS(SVG_NS, tag);
      for (const [name, value] of Object.entries(attrs)) element.setAttribute(name, value);
      return element;
    };

    function matches(node) {
      const haystack = [
        node.label, node.summary, node.details, ...(node.tags || []),
        ...(node.stores || []).flatMap((store) => [store.system, store.role, store.location, store.details]),
      ].join(" ").toLowerCase();
      return (!state.query || haystack.includes(state.query)) && (!state.status || node.status === state.status);
    }

    function visibleNodes() {
      if (state.mode === "network") return lensNodes();
      const result = [];
      const visit = (node) => {
        result.push(node);
        if (state.expanded.has(node.id)) childrenOf(node.id).forEach(visit);
      };
      lensNodes().filter((node) => !node.parent).forEach(visit);
      return result;
    }

    function layout(nodes) {
      if (state.mode === "network") {
        const center = nodes.find((node) => !node.parent) ?? nodes[0];
        if (!center) return;
        state.positions.set(center.id, { x: -NODE_W / 2, y: -NODE_H / 2 });
        nodes.filter((node) => node.id !== center.id).forEach((node, index, list) => {
          const angle = -Math.PI / 2 + index / Math.max(1, list.length) * Math.PI * 2;
          const radius = 300 + (index % 3) * 95;
          state.positions.set(node.id, {
            x: Math.cos(angle) * radius - NODE_W / 2,
            y: Math.sin(angle) * radius * .76 - NODE_H / 2,
          });
        });
        return;
      }
      const visibleIds = new Set(nodes.map((node) => node.id));
      const depth = new Map();
      const setDepth = (node, value) => {
        depth.set(node.id, value);
        nodes.filter((candidate) => candidate.parent === node.id).forEach((child) => setDepth(child, value + 1));
      };
      nodes.filter((node) => !node.parent || !visibleIds.has(node.parent)).forEach((node) => setDepth(node, 0));
      const levels = new Map();
      for (const node of nodes) {
        const level = depth.get(node.id) ?? 0;
        if (!levels.has(level)) levels.set(level, []);
        levels.get(level).push(node);
      }
      for (const [level, items] of levels) {
        items.forEach((node, index) => {
          if (!state.positions.has(node.id)) state.positions.set(node.id, { x: level * 246, y: index * 92 });
        });
      }
    }

    function pathBetween(source, target) {
      if (state.mode === "network") {
        const sx = source.x + NODE_W / 2, sy = source.y + NODE_H / 2;
        const tx = target.x + NODE_W / 2, ty = target.y + NODE_H / 2;
        return "M " + sx + " " + sy + " Q " + ((sx + tx) / 2 + 14) + " " + ((sy + ty) / 2 - 14) + ", " + tx + " " + ty;
      }
      const sx = source.x + NODE_W, sy = source.y + NODE_H / 2;
      const tx = target.x, ty = target.y + NODE_H / 2;
      const bend = Math.max(48, Math.abs(tx - sx) * .45);
      return "M " + sx + " " + sy + " C " + (sx + bend) + " " + sy + ", " + (tx - bend) + " " + ty + ", " + tx + " " + ty;
    }

    function render() {
      if (!state.graph) return;
      viewport.replaceChildren();
      viewport.setAttribute("transform", "translate(" + state.transform.x + " " + state.transform.y + ") scale(" + state.transform.scale + ")");
      document.querySelectorAll("[data-lens]").forEach((button) => button.classList.toggle("active", button.dataset.lens === state.lens));
      document.getElementById("subtitle").textContent = lensCopy[state.lens];
      document.getElementById("layout").textContent = state.mode === "hierarchy" ? "Hierarchy" : "Network";
      document.getElementById("layout").classList.toggle("active", state.mode === "network");
      document.getElementById("relations").classList.toggle("active", state.showRelations);

      const nodes = visibleNodes();
      layout(nodes);
      const visibleIds = new Set(nodes.map((node) => node.id));
      const matchesSet = new Set(nodes.filter(matches).map((node) => node.id));
      const selectedRelated = new Set(state.selected ? relatedEdges(state.selected).flatMap((edge) => [edge.source, edge.target]) : []);
      if (state.selected) {
        const selected = nodeById(state.selected);
        if (selected?.parent) selectedRelated.add(selected.parent);
        childrenOf(state.selected).forEach((node) => selectedRelated.add(node.id));
      }
      const hierarchyEdges = nodes
        .filter((node) => node.parent && visibleIds.has(node.parent))
        .map((node) => ({ source: node.parent, target: node.id, type: "contains", label: "" }));
      const relationEdges = state.graph.edges.filter((edge) =>
        state.showRelations && visibleIds.has(edge.source) && visibleIds.has(edge.target)
        && nodeById(edge.target)?.parent !== edge.source
      );
      for (const edge of [...hierarchyEdges, ...relationEdges]) {
        const source = state.positions.get(edge.source), target = state.positions.get(edge.target);
        if (!source || !target) continue;
        const connected = !state.selected || edge.source === state.selected || edge.target === state.selected;
        const sourceNode = nodeById(edge.source), targetNode = nodeById(edge.target);
        const crossSystem = sourceNode?.system && targetNode?.system && sourceNode.system !== targetNode.system;
        viewport.appendChild(createSvg("path", {
          d: pathBetween(source, target),
          class: "edge " + (edge.type === "contains" ? "" : "relation ") + (crossSystem ? "cross-system " : "") + (connected ? "" : "dim"),
        }));
        if (edge.label && connected) {
          const label = createSvg("text", {
            x: (source.x + target.x + NODE_W) / 2,
            y: (source.y + target.y + NODE_H) / 2 - 7,
            class: "edge-label",
          });
          label.textContent = edge.label;
          viewport.appendChild(label);
        }
      }

      for (const node of nodes) {
        const position = state.positions.get(node.id);
        const dim = !matchesSet.has(node.id) || (state.selected && !selectedRelated.has(node.id) && state.selected !== node.id);
        const group = createSvg("g", {
          transform: "translate(" + position.x + " " + position.y + ")",
          class: "node " + (node.id === state.selected ? "selected " : "") + (dim ? "dim " : "") + (node.status || ""),
          tabindex: "0", role: "button", "aria-label": node.label,
        });
        group.dataset.nodeId = node.id;
        group.appendChild(createSvg("rect", { width: NODE_W, height: NODE_H, class: "node-card" }));
        group.appendChild(createSvg("rect", { x: 0, y: 12, width: 4, height: 42, rx: 2, fill: kindColors[node.kind] || "#73a7ff" }));
        const title = createSvg("text", { x: 16, y: 28, class: "node-title" });
        title.textContent = node.label.length > 25 ? node.label.slice(0, 24) + "…" : node.label;
        group.appendChild(title);
        const kind = createSvg("text", { x: 16, y: 48, class: "node-kind" });
        kind.textContent = node.kind + (node.system ? " · " + node.system : "") + (node.status ? " · " + node.status : "");
        group.appendChild(kind);
        group.appendChild(createSvg("circle", {
          cx: NODE_W - 14, cy: 14, r: 5, class: "node-status",
          stroke: statusColors[node.status] || "#929bab",
        }));
        if (state.mode === "hierarchy" && childrenOf(node.id).length) {
          group.appendChild(createSvg("circle", { cx: NODE_W, cy: NODE_H / 2, r: 10, class: "expand-ring", "data-expand": node.id }));
          const glyph = createSvg("text", { x: NODE_W, y: NODE_H / 2 + 4, class: "expand-glyph" });
          glyph.textContent = state.expanded.has(node.id) ? "−" : "+";
          group.appendChild(glyph);
        }
        viewport.appendChild(group);
      }
      document.getElementById("empty").style.display = nodes.some(matches) ? "none" : "grid";
      renderInspector();
    }

    function renderInspector() {
      const node = nodeById(state.selected);
      if (!node) {
        inspector.innerHTML = '<div class="placeholder"><div class="placeholder-icon">◉</div><h2>Select a node</h2><p>Inspect its current state, definition, source paths, physical stores, ownership boundaries, and relationships.</p><div class="section legend"><span style="--dot:#62d99b">Active</span><span style="--dot:#f4bc67">Partial</span><span style="--dot:#aa8cff">Planned</span><span style="--dot:#ef8383">Proposed</span></div></div>';
        return;
      }
      const stores = (node.stores || []).map((store) =>
        '<div class="store-card"><div class="store-head"><span>' + escapeHtml(store.system) + '</span><span class="store-role">' + escapeHtml(store.role) + '</span></div>' +
        '<p>' + escapeHtml(store.details || "") + '</p><code class="store-location">' + escapeHtml(store.location || "Logical ownership only") + '</code></div>'
      ).join("");
      const relations = relatedEdges(node.id).map((edge) => {
        const otherId = edge.source === node.id ? edge.target : edge.source;
        const other = nodeById(otherId);
        if (!other) return "";
        const direction = edge.source === node.id ? edge.type : "receives " + edge.type;
        return '<button data-related="' + escapeHtml(other.id) + '">' + escapeHtml(other.label) +
          '<span>' + escapeHtml(direction + (edge.label ? " · " + edge.label : "")) + '</span></button>';
      }).join("");
      inspector.innerHTML =
        '<div class="eyebrow">' + escapeHtml(node.lens + " / " + node.kind) + '</div>' +
        '<h2 class="card-title">' + escapeHtml(node.label) + '</h2>' +
        '<p class="card-summary">' + escapeHtml(node.summary) + '</p>' +
        '<div class="meta"><span class="pill status" style="--status-color:' + (statusColors[node.status] || "#929bab") + '">' + escapeHtml(node.status || "documented") + '</span>' +
        (node.system ? '<span class="pill">' + escapeHtml(node.system) + '</span>' : '') +
        (node.tags || []).map((tag) => '<span class="pill">' + escapeHtml(tag) + '</span>').join("") + '</div>' +
        '<div class="section"><h3>Definition and role</h3><p>' + escapeHtml(node.details || "") + '</p></div>' +
        (stores ? '<div class="section"><h3>Ownership and storage</h3><div class="stores">' + stores + '</div></div>' : '') +
        (node.paths?.length ? '<div class="section"><h3>Defined or implemented at</h3>' + node.paths.map((path) => '<code class="path">' + escapeHtml(path) + '</code>').join("") + '</div>' : '') +
        (relations ? '<div class="section"><h3>Relationships</h3><div class="related">' + relations + '</div></div>' : '');
      inspector.querySelectorAll("[data-related]").forEach((button) =>
        button.addEventListener("click", () => selectNode(button.dataset.related))
      );
    }

    function selectNode(id) {
      const node = nodeById(id);
      if (!node) return;
      if (node.lens !== state.lens) state.lens = node.lens;
      let current = node;
      while (current?.parent) {
        state.expanded.add(current.parent);
        current = nodeById(current.parent);
      }
      state.selected = id;
      render();
    }

    function fitGraph() {
      const nodes = visibleNodes();
      if (!nodes.length) return;
      layout(nodes);
      const points = nodes.map((node) => state.positions.get(node.id));
      const minX = Math.min(...points.map((point) => point.x));
      const minY = Math.min(...points.map((point) => point.y));
      const maxX = Math.max(...points.map((point) => point.x + NODE_W));
      const maxY = Math.max(...points.map((point) => point.y + NODE_H));
      const bounds = svg.getBoundingClientRect();
      const scale = Math.min(1.2, Math.max(.28, Math.min((bounds.width - 80) / (maxX - minX), (bounds.height - 80) / (maxY - minY))));
      state.transform = {
        x: (bounds.width - (maxX - minX) * scale) / 2 - minX * scale,
        y: (bounds.height - (maxY - minY) * scale) / 2 - minY * scale,
        scale,
      };
      render();
    }

    function initializeLens() {
      lensNodes().filter((node) => !node.parent || node.kind === "area" || node.kind === "layer").forEach((node) => state.expanded.add(node.id));
      state.positions.clear();
      state.transform = { x: 42, y: 42, scale: 1 };
      render();
      requestAnimationFrame(fitGraph);
    }

    async function loadGraph() {
      const response = await fetch("/api/graph", { cache: "no-store" });
      if (!response.ok) throw new Error("Could not load OWL map data");
      state.graph = await response.json();
      if (!nodeById(state.selected)) state.selected = null;
      initializeLens();
    }

    document.querySelectorAll("[data-lens]").forEach((button) => button.addEventListener("click", () => {
      state.lens = button.dataset.lens;
      state.selected = null;
      state.expanded.clear();
      initializeLens();
    }));
    document.getElementById("search").addEventListener("input", (event) => {
      state.query = event.target.value.trim().toLowerCase();
      render();
    });
    document.getElementById("status").addEventListener("change", (event) => {
      state.status = event.target.value;
      render();
    });
    document.getElementById("layout").addEventListener("click", () => {
      state.mode = state.mode === "hierarchy" ? "network" : "hierarchy";
      state.positions.clear();
      render();
      requestAnimationFrame(fitGraph);
    });
    document.getElementById("relations").addEventListener("click", () => {
      state.showRelations = !state.showRelations;
      render();
    });
    document.getElementById("fit").addEventListener("click", fitGraph);
    document.getElementById("expand").addEventListener("click", () => {
      const parents = lensNodes().filter((node) => childrenOf(node.id).length);
      const allExpanded = parents.every((node) => state.expanded.has(node.id));
      parents.forEach((node) => allExpanded ? state.expanded.delete(node.id) : state.expanded.add(node.id));
      state.positions.clear();
      render();
      requestAnimationFrame(fitGraph);
    });
    viewport.addEventListener("click", (event) => {
      if (state.suppressClick) return;
      const expandId = event.target.dataset.expand;
      const group = event.target.closest(".node");
      const id = expandId || group?.dataset.nodeId;
      if (!id) return;
      selectNode(id);
      if (expandId) {
        state.expanded.has(id) ? state.expanded.delete(id) : state.expanded.add(id);
        state.positions.clear();
        render();
      }
    });
    viewport.addEventListener("keydown", (event) => {
      const group = event.target.closest(".node");
      if (group && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        selectNode(group.dataset.nodeId);
      }
    });
    svg.addEventListener("wheel", (event) => {
      event.preventDefault();
      const rect = svg.getBoundingClientRect();
      const px = event.clientX - rect.left, py = event.clientY - rect.top;
      const oldScale = state.transform.scale;
      const nextScale = Math.min(2.3, Math.max(.25, oldScale * Math.exp(-event.deltaY * .001)));
      const worldX = (px - state.transform.x) / oldScale, worldY = (py - state.transform.y) / oldScale;
      state.transform.x = px - worldX * nextScale;
      state.transform.y = py - worldY * nextScale;
      state.transform.scale = nextScale;
      render();
    }, { passive: false });
    svg.addEventListener("pointerdown", (event) => {
      if (event.target.dataset.expand) return;
      const group = event.target.closest(".node");
      svg.setPointerCapture(event.pointerId);
      if (group) {
        const position = state.positions.get(group.dataset.nodeId);
        state.dragging = { id: group.dataset.nodeId, x: event.clientX, y: event.clientY, sx: position.x, sy: position.y };
      } else {
        state.panning = { x: event.clientX, y: event.clientY, sx: state.transform.x, sy: state.transform.y };
        svg.classList.add("panning");
      }
    });
    svg.addEventListener("pointermove", (event) => {
      if (state.dragging) {
        if (Math.abs(event.clientX - state.dragging.x) > 4 || Math.abs(event.clientY - state.dragging.y) > 4) state.dragging.moved = true;
        state.positions.set(state.dragging.id, {
          x: state.dragging.sx + (event.clientX - state.dragging.x) / state.transform.scale,
          y: state.dragging.sy + (event.clientY - state.dragging.y) / state.transform.scale,
        });
        render();
      } else if (state.panning) {
        state.transform.x = state.panning.sx + event.clientX - state.panning.x;
        state.transform.y = state.panning.sy + event.clientY - state.panning.y;
        render();
      }
    });
    svg.addEventListener("pointerup", () => {
      if (state.dragging?.moved) {
        state.suppressClick = true;
        setTimeout(() => { state.suppressClick = false; }, 0);
      }
      state.dragging = null;
      state.panning = null;
      svg.classList.remove("panning");
    });
    const events = new EventSource("/events");
    events.addEventListener("focus", (event) => selectNode(JSON.parse(event.data).nodeId));
    loadGraph().catch((error) => { inspector.textContent = error.message; });
  </script>
</body>
</html>`;
}
