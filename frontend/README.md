# Document Intelligence Hub — Frontend

React + TypeScript app built with Vite, replacing the previous vanilla HTML/CSS/JS
hub UI (see issue [#857](https://github.com/rsocko/ideation/issues/857)).

## Stack

- Vite + React + TypeScript
- React Router (`HashRouter`) for client-side navigation
- No UI framework — a small shared design system lives in `src/styles/` (design
  tokens, layout, shared component classes) and `src/components/ui.tsx` (Badge,
  Card, StatCard, Button, Tabs, FilterPills, ConfidenceBar, Modal, SidePanel,
  DataTable, etc.)
- `src/lib/api.ts` is a thin fetch wrapper with a typed `endpoints` map for every
  FastAPI route exposed by the hub backend.

## Design

The visual language matches the `mockups/` designs: a light theme by default
(`#f5f6fa` background, white cards, `#3498db` accent) with a horizontal top nav,
plus an optional dark theme (toggle button in the top nav, persisted to
`localStorage`) that matches the legacy hub's dark palette.

## Development

```
npm install
npm run dev
```

The dev server proxies `/api` and `/health` to `http://localhost:8001` (see
`vite.config.ts`), so run the FastAPI backend (`doc-hub-serve`) alongside it.

## Build

```
npm run build
```

`vite.config.ts` emits the build directly into
`../src/doc_intelligence_hub/api/static/`, which FastAPI serves at `/` (see
`api/app.py`). The app uses a `HashRouter`, so no server-side catch-all route
is needed for deep links — every route lives under the `#/...` fragment.

## Pages

Overview, Statements (+ series detail), EOB Matching (dashboard, match review,
unmatched), Action Queue, Needs Review, Manual Match Search, Rules Config,
Insights, History, Orphans & Dupes, and Settings (merges the old `/admin` UI).
Some pages note API gaps in code comments where a dedicated backend endpoint
doesn't exist yet (e.g. statement series detail, duplicate document search).
