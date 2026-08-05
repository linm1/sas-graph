// Webview client script. Plain non-module <script> tag — vis-network UMD
// exposes a global `vis`. GRAPH_DATA is inlined into the page by the
// extension host before this script runs (see webviewPanel.js).
// Ported from .scratch/prototype-vis-network/graph-core.js, adapted for the
// VS Code webview host (acquireVsCodeApi, postMessage instead of console.log).
"use strict";

(function () {
  const vscode = acquireVsCodeApi();

  const nodes = new vis.DataSet(GRAPH_DATA.nodes);
  const edges = new vis.DataSet(GRAPH_DATA.edges);
  const nodeById = new Map(GRAPH_DATA.nodes.map((n) => [n.id, n]));

  const container = document.getElementById("network");
  const data = { nodes, edges };
  const options = {
    nodes: { shape: "dot", scaling: { min: 6, max: 30 }, font: { size: 11, color: "#ddd" } },
    edges: { width: 0.5, color: { color: "#555", highlight: "#ccc" }, smooth: { type: "continuous" } },
    physics: {
      solver: "forceAtlas2Based",
      forceAtlas2Based: {
        gravitationalConstant: -60, centralGravity: 0.005,
        springLength: 120, springConstant: 0.08, damping: 0.4, avoidOverlap: 0.8,
      },
      stabilization: { iterations: 200, fit: true },
    },
    interaction: { hover: true, tooltipDelay: 100, hideEdgesOnDrag: true },
  };

  const network = new vis.Network(container, data, options);

  // graphify pattern: freeze physics once stabilized, re-heat only on drag
  network.once("stabilizationIterationsDone", () => {
    network.setOptions({ physics: false });
    updateStatus();
  });

  network.on("dragStart", (params) => {
    if (params.nodes.length) network.setOptions({ physics: { enabled: true, solver: "forceAtlas2Based" } });
  });
  network.on("dragEnd", (params) => {
    if (params.nodes.length) setTimeout(() => network.setOptions({ physics: false }), 800);
  });

  // Ticket 04: two required status messages, sourced from ticket 03's
  // exposed state.hitIds/state.pairStatus (no second computeHitSet or
  // computeHopDistances call — both were already computed by the most
  // recent applyVisualState()). Only evaluated when a hit set is active; a
  // regular connected search/filter hit falls through to the default
  // node/edge-count message unchanged.
  //
  // A pair with a no-lineage endpoint is not "disconnected" (there was
  // never a lineage graph for it to be part of) — it's the OTHER message.
  // Without this exclusion, a filter-only hit set that happens to include
  // isolated-by-design nodes (e.g. library=sdtm pulling in library:sdtm,
  // setup:setup.sas, macrovar:root@1 — all legitimately outside the lineage
  // subgraph per libraryFilter.js's "no derivable library stays visible"
  // rule) would report a "not connected" message on nearly every ordinary
  // filter, burying the actually-useful default node/edge-count message.
  // Thin adapter over visualState.js's pure deriveStatusMessage — supplies
  // GRAPH_DATA/state/nodeById, the message-selection logic itself (including
  // the no-lineage vs. disconnected precedence) lives there so it's unit
  // testable without a DOM (see visualState.test.js).
  function statusMessage() {
    const hitIds = state.hitIds;
    if (hitIds === null) return null; // idle: no hit set, default message applies

    const labelOf = (id) => (nodeById.has(id) ? nodeById.get(id).label : id);
    return window.deriveStatusMessage(
      [...hitIds],
      state.pairStatus,
      GRAPH_DATA.edges,
      window.PROPAGATING_TYPES,
      state.ceiling,
      labelOf
    );
  }

  function updateStatus() {
    const prefix = typeof RUN_STATUS === "string" && RUN_STATUS ? `${RUN_STATUS} — ` : "";
    const special = statusMessage();
    document.getElementById("status").textContent = special
      ? `${prefix}${special}`
      : `${prefix}${GRAPH_DATA.nodes.length} nodes, ${GRAPH_DATA.edges.length} edges — stabilized`;
  }

  // ---------------------------------------------------------------------
  // Shared visual-state machine (mandatory refactor seam, ticket 04).
  //
  // state = { selected, searchMatches, cursorNode, filters, hover, pairStatus, hitIds, ceiling }
  //   selected      : node id opened via click (detail panel target), or null
  //   searchMatches : Set<nodeId> | null — null means "no active search"
  //   cursorNode    : node id highlighted by editor cursor sync (ticket 06), or null
  //   filters       : { library: string|null, type: string|null } (ticket 08)
  //   hover         : node id currently hovered, or null (transient)
  //   pairStatus    : Map<string,"reachable"|"disconnected"> | null — last
  //                   computeHopDistances result's pairStatus (ticket 02),
  //                   exposed for ticket 04's status messaging without a
  //                   second hop computation. null on the idle path.
  //   hitIds        : Set<nodeId> | null — last computeHitSet result,
  //                   exposed for ticket 04's status messaging without a
  //                   second computeHitSet call. null on the idle path.
  //   ceiling       : number — live depth-ceiling slider value (ticket 04),
  //                   passed as computeHopDistances's `ceiling` arg. Starts
  //                   at DEFAULT_HOP_CEILING, matching the slider's default.
  //
  // applyVisualState is the ONLY place that computes final per-node/per-edge
  // opacity and calls nodes.update/edges.update. Every interaction (hover,
  // search input, cursor move, filter change) writes into one field of
  // `state` and calls applyVisualState() — none of them invent their own
  // dimming logic.
  //
  // Precedence (ticket 03 + unify-cursor-select-into-hit-set):
  //   1. selected/cursorNode is a HIT-SET ORIGIN, not a post-hoc highlight —
  //      it feeds computeHitSet (as clickHitId, above) and wins over any
  //      active search/filter, so a click/cursor-sync degenerates through
  //      computeHopDistances's single-hit rule: full bounded lineage both
  //      directions, banded exactly like a one-result search would be. This
  //      is what makes click/cursor visible again after ticket 03's
  //      precedence change made a plain highlight-on-flat-1.0-baseline a
  //      no-op (regression found and closed by that ticket).
  //   2. hover stays OUT of the hit-set — it remains the old transient
  //      "trace this neighborhood" layer (story 15): incident edges raise to
  //      0.9 (never demoting an existing 1.0/hop0 edge) and the hovered node
  //      goes fully opaque, LAYERED ON TOP of whatever baseline step 1
  //      produced, never replacing it (this is what fixes the old
  //      hover-erases-context defect, story 16).
  //   3. Every non-incident edge and non-hovered node keeps its baseline
  //      from step 1 unchanged.
  // ---------------------------------------------------------------------
  // DEFAULT_HOP_CEILING (ticket 03) is now only the slider's/state's initial
  // value (ticket 04) — matches the spec's measured recommendation
  // (wayfinder/spec-traversal-legible-filter-results.md). The
  // computeHopDistances call site below uses the live state.ceiling, not
  // this constant.
  const DEFAULT_HOP_CEILING = 5;

  const state = {
    selected: null,
    searchMatches: null,
    cursorNode: null,
    filters: { library: null, type: null },
    hover: null,
    pairStatus: null,
    hitIds: null,
    ceiling: DEFAULT_HOP_CEILING,
  };

  function nodeMatchesFilters(n) {
    return window.filtersAllow(n, state.filters);
  }

  // applyVisualState is the ONLY place in this file that writes
  // edges.update(...)/nodes.update(...) with color/opacity — this is
  // load-bearing per the spec, not a style preference. All the actual band
  // and precedence math lives in visualState.js (window.edgeBandOpacity,
  // window.applyHoverToEdge, etc.) and hopDistance.js
  // (window.computeHopDistances) — this function only orchestrates: compute
  // hit set -> compute hop distances -> band -> layer hover on top -> write.
  function applyVisualState() {
    // Click/cursor is a third hit-set origin (unify-cursor-select-into-hit-set):
    // wins over search/filter, degenerates through computeHopDistances's
    // existing single-hit rule (full bounded lineage, both directions).
    const clickHitId = state.selected || state.cursorNode || null;
    const hitIds = window.computeHitSet(GRAPH_DATA.nodes, state.searchMatches, state.filters, window.filtersAllow, clickHitId);

    let hopResult = null;
    if (hitIds !== null) {
      hopResult = window.computeHopDistances(GRAPH_DATA.nodes, GRAPH_DATA.edges, hitIds, state.ceiling);
    }
    // Exposed for ticket 04's status messaging (disconnected/no-lineage),
    // without a second computeHopDistances call or a second computeHitSet
    // call. Cleared on the idle path so a stale message doesn't survive
    // after search/filter is cleared.
    state.pairStatus = hopResult ? hopResult.pairStatus : null;
    state.hitIds = hitIds;

    // --- edge baseline: band table when a hit set is active, flat 0.5 (the
    // pre-existing idle behavior) only when neither search nor filter is
    // active. This narrowing — from unconditional to this one specific idle
    // case — is the entire defect fix (spec / ticket 03).
    const edgeBaseline = new Map(GRAPH_DATA.edges.map((e) => {
      if (!hopResult) return [e.id, window.IDLE_OPACITY];
      const distA = hopResult.distances.get(e.from);
      const distB = hopResult.distances.get(e.to);
      return [e.id, window.edgeBandOpacity(distA, distB)];
    }));

    // --- node baseline: lasso (hit + bridge revealed, true non-match dims)
    // when a hit set is active; the pre-existing filter-only dim/opaque
    // binary when idle (filters may still be set even with no search — the
    // idle case here is specifically "no hit set at all", i.e. neither
    // search nor filter active, per computeHitSet's contract).
    const nodeBaseline = new Map(GRAPH_DATA.nodes.map((n) => {
      if (hopResult) return [n.id, window.nodeLassoOpacity(n.id, hitIds, hopResult.bridgeNodeIds, hopResult.distances)];
      return [n.id, nodeMatchesFilters(n) ? 1 : 0.1];
    }));

    // --- hover layers ON TOP of the baseline above, it does not replace it
    // (spec "Precedence" section) — non-incident edges and non-hovered nodes
    // keep their baseline exactly as computed above. selected/cursorNode are
    // no longer folded in here: they already drove the baseline itself via
    // clickHitId above, so re-including them would be redundant, not wrong
    // (their node is already hop0 opacity 1.0; their incident edges are
    // already hop0/hop1 band values at or above what this layer would set).
    const highlightId = state.hover || null;
    const incidentEdgeIds = highlightId && nodeById.has(highlightId)
      ? new Set(network.getConnectedEdges(highlightId))
      : new Set();

    nodes.update(GRAPH_DATA.nodes.map((n) => ({
      id: n.id,
      opacity: window.applyHoverToNode(nodeBaseline.get(n.id), n.id === highlightId),
    })));
    edges.update(GRAPH_DATA.edges.map((e) => ({
      id: e.id,
      color: { opacity: window.applyHoverToEdge(edgeBaseline.get(e.id), incidentEdgeIds.has(e.id)) },
    })));
  }

  network.on("hoverNode", (params) => {
    state.hover = params.node;
    applyVisualState();
  });
  network.on("blurNode", () => {
    state.hover = null;
    applyVisualState();
  });

  // Node click: open detail panel, post jump-to-line to the extension host,
  // and write into the shared state as the "selected" highlight.
  const info = document.getElementById("info");
  network.on("click", (params) => {
    if (!params.nodes.length) {
      info.classList.remove("open");
      state.selected = null;
      state.cursorNode = null;
      applyVisualState();
      return;
    }
    const nodeId = params.nodes[0];
    const n = nodes.get(nodeId);
    renderDetailPanel(n);
    state.selected = nodeId;
    state.cursorNode = null; // click supersedes a stale cursor highlight
    applyVisualState();
    if (n.file && n.line_start != null) {
      vscode.postMessage({ type: "jumpToLine", file: n.file, line: n.line_start });
    }
  });

  const SKIP_FIELDS = new Set(["id", "type", "label", "source", "call_source", "definition_source"]);

  function esc(s) {
    return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  function renderExtraFields(raw) {
    const rows = Object.keys(raw).filter((k) => !SKIP_FIELDS.has(k) && raw[k] != null && raw[k] !== "");
    if (!rows.length) return "";
    return "<dl>" + rows.map((k) => {
      const v = raw[k];
      const body = typeof v === "object" ? `<pre>${esc(JSON.stringify(v, null, 2))}</pre>` : esc(v);
      return `<dt>${esc(k)}</dt><dd>${body}</dd>`;
    }).join("") + "</dl>";
  }

  function renderSource(label, src) {
    if (!src) return "";
    return `<dt>${esc(label)}</dt><dd>${esc(src.file || "-")}:${src.line_start ?? "-"}` +
      (src.original_text ? `<pre>${esc(src.original_text)}</pre>` : "") + "</dd>";
  }

  function renderFindings(findings) {
    if (!findings || !findings.length) return "";
    return findings.map((f) => `<div class="finding">` +
      `<dt>finding (${esc(f.status || "")})</dt><dd>${esc(f.message || "")}</dd>` +
      (f.suggested_action ? `<dt>suggested_action</dt><dd>${esc(f.suggested_action)}</dd>` : "") +
      `</div>`).join("");
  }

  function renderDetailPanel(n) {
    const raw = n.raw || {};
    info.classList.add("open");
    info.innerHTML =
      `<span class="type" style="background:${n.color}">${esc(n.group)}</span>` +
      `<b>${esc(n.label)}</b>` +
      `<dl>${renderSource("source", raw.source)}${renderSource("call_source", raw.call_source)}${renderSource("definition_source", raw.definition_source)}</dl>` +
      renderExtraFields(raw) +
      renderFindings(n.findings);
  }

  document.getElementById("search").addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    if (!q) {
      state.searchMatches = null;
      applyVisualState();
      updateStatus();
      return;
    }
    const hits = GRAPH_DATA.nodes.filter((n) => window.matchesSearch(q, n)).map((n) => n.id);
    state.searchMatches = new Set(hits);
    applyVisualState();
    updateStatus();
    if (hits.length === 1) network.focus(hits[0], { scale: 1.5, animation: true });
  });

  // Extension-host -> webview messages (cursor sync, ticket 06).
  window.addEventListener("message", (event) => {
    const message = event.data;
    if (message && message.type === "cursorNode") {
      state.cursorNode = message.nodeId || null;
      state.selected = null; // cursor move supersedes a stale click selection
      applyVisualState();
    }
  });

  // ---------------------------------------------------------------------
  // Library / step-type filters (ticket 08). Filtered-out nodes are DIMMED,
  // not hidden — chosen for consistency with search's existing dim-not-hide
  // treatment (specify-search-behavior) so the graph's shape stays visible
  // as context, same rationale as search. Edges to dimmed nodes are left in
  // place (not removed from the DataSet), only opacity changes, so no edge
  // ever references a missing node. Combines with search/hover/selection
  // through the same shared applyVisualState() seam — filters are always
  // applied (via nodeMatchesFilters) regardless of which other state is
  // active.
  //
  // Library derivation is the ticket-08 placeholder heuristic from
  // src/libraryFilter.js (id-prefix on Dataset/UnknownDataset only) — see
  // that module's doc comment. Still open per Further Notes #7, not a
  // decided data path.
  // ---------------------------------------------------------------------
  const typeFilterEl = document.getElementById("typeFilter");
  const libraryFilterEl = document.getElementById("libraryFilter");

  const distinctTypes = [...new Set(GRAPH_DATA.nodes.map((n) => n.group))].sort();
  for (const t of distinctTypes) {
    const opt = document.createElement("option");
    opt.value = t;
    opt.textContent = t;
    typeFilterEl.appendChild(opt);
  }

  const distinctLibraries = [...new Set(
    GRAPH_DATA.nodes.map((n) => window.deriveLibrary(n)).filter((lib) => lib !== null)
  )].sort();
  for (const lib of distinctLibraries) {
    const opt = document.createElement("option");
    opt.value = lib;
    opt.textContent = lib;
    libraryFilterEl.appendChild(opt);
  }

  typeFilterEl.addEventListener("change", (e) => {
    state.filters = { ...state.filters, type: e.target.value || null };
    applyVisualState();
    updateStatus();
    updateFilterBadge();
  });
  libraryFilterEl.addEventListener("change", (e) => {
    state.filters = { ...state.filters, library: e.target.value || null };
    applyVisualState();
    updateStatus();
    updateFilterBadge();
  });

  // Depth-ceiling slider (ticket 04). Markup lives in webviewPanel.js's
  // buildHtml, alongside #search/#typeFilter/#libraryFilter — this only
  // attaches behavior. Reuses the same recompute path search/filter changes
  // already trigger (applyVisualState + updateStatus), rather than inventing
  // a second recompute trigger. Widening the ceiling enough to reconnect a
  // previously-disconnected pair clears the disconnected message for free,
  // since updateStatus() re-derives it from the freshly recomputed
  // state.pairStatus on every call.
  const hopCeilingEl = document.getElementById("hopCeiling");
  const hopValueEl = document.getElementById("hopValue");
  hopCeilingEl.addEventListener("input", (e) => {
    state.ceiling = Number(e.target.value);
    if (hopValueEl) hopValueEl.textContent = `${state.ceiling} hops`;
    applyVisualState();
    updateStatus();
  });

  // Filter popover (narrow-sidebar rework): type/library/hop controls moved
  // out of the inline pill into a popover anchored under #filterToggle, so
  // the pill itself only ever has to fit #search + one icon button.
  const filterToggle = document.getElementById("filterToggle");
  const filterPopover = document.getElementById("filterPopover");
  const filterBadge = document.getElementById("filterBadge");
  filterToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    filterPopover.classList.toggle("open");
    filterToggle.classList.toggle("active", filterPopover.classList.contains("open"));
  });
  document.addEventListener("click", (e) => {
    if (!filterPopover.contains(e.target) && e.target !== filterToggle) {
      filterPopover.classList.remove("open");
      filterToggle.classList.remove("active");
    }
  });
  function updateFilterBadge() {
    const active = document.getElementById("typeFilter").value || document.getElementById("libraryFilter").value;
    filterBadge.classList.toggle("show", !!active);
  }
  updateFilterBadge();

  applyVisualState();
})();
