---
title: "UI Flow Diagrams"
sidebar_label: UI Flows
sidebar_position: 6
---

# OWL UI Flow Diagrams — Current State vs Future State

This document presents visual diagrams of the OWL (Document Intelligence Hub) navigation hierarchy and user flows, comparing the **current state** (with identified problems) against a **proposed future state** (with improvements).

**Legend:**
- 🔴 Current problems — highlighted in red/orange
- 🟢 Future improvements — highlighted in green/blue

---

## 1. High-Level Navigation Hierarchy

### Current State: Flat 12-Item Navigation

The current navigation presents all 12 sections as equal top-level items in a horizontal bar. This overflows on screens narrower than 1400px and provides no visual grouping to help users find related features.

```mermaid
graph TD
    NAV[Top Navigation Bar — 12 items]
    NAV --> A[Overview]
    NAV --> B[Dashboard]
    NAV --> C[Statements]
    NAV --> D[EOB Matching]
    NAV --> E[Action Queue]
    NAV --> F[Triage]
    NAV --> G[Corrections]
    NAV --> H[Insights]
    NAV --> I[History]
    NAV --> J[Orphans & Dupes]
    NAV --> K[Rules Config]
    NAV --> L[Settings]

    classDef problem fill:#fee2e2,stroke:#dc2626,stroke-width:2px
    classDef problemNav fill:#fef3c7,stroke:#d97706,stroke-width:2px
    class NAV problemNav
    class A,B,C,D,E,F,G,H,I,J,K,L problem
```

**Problems:**
- Two "dashboard" pages (Overview and Dashboard) with unclear differentiation
- EOB Matching, Action Queue, and Triage are conceptually related but separated
- Admin features (Corrections, History, Rules, Settings) mixed with primary workflows
- No hierarchy — everything competes for attention equally

---

### Future State: Consolidated 5-Group Navigation

The proposed navigation groups related features into 5 top-level sections with contextual sub-navigation within each section. This reduces cognitive load and fits comfortably on all screen sizes.

```mermaid
graph TD
    NAV[Top Navigation — 5 items]
    NAV --> DASH[Dashboard]
    NAV --> DOCS[Documents]
    NAV --> MED[Medical Claims]
    NAV --> MON[Monitor]
    NAV --> ADMIN[Admin]

    DASH --> DASH1[Unified Overview]
    DASH --> DASH2[Key Metrics]
    DASH --> DASH3[Alerts & Notifications]

    DOCS --> DOCS1[Work Queue]
    DOCS --> DOCS2[Triage]
    DOCS --> DOCS3[Orphans & Duplicates]

    MED --> MED1[EOB Hub]
    MED --> MED2[Unmatched Claims]
    MED --> MED3[Match Review]

    MON --> MON1[Statements]
    MON --> MON2[Insights & Trends]
    MON --> MON3[Processing History]

    ADMIN --> ADMIN1[Rules Configuration]
    ADMIN --> ADMIN2[Corrections]
    ADMIN --> ADMIN3[Settings]

    classDef solution fill:#dcfce7,stroke:#16a34a,stroke-width:2px
    classDef solutionNav fill:#dbeafe,stroke:#2563eb,stroke-width:2px
    classDef solutionSub fill:#f0fdf4,stroke:#22c55e
    class NAV solutionNav
    class DASH,DOCS,MED,MON,ADMIN solution
    class DASH1,DASH2,DASH3,DOCS1,DOCS2,DOCS3,MED1,MED2,MED3,MON1,MON2,MON3,ADMIN1,ADMIN2,ADMIN3 solutionSub
```

**Improvements:**
- 5 top-level items — fits any screen width without overflow
- Logical grouping by user intent (what am I trying to do?)
- Sub-navigation appears contextually within each section
- Clear separation of primary workflows vs administrative features

---

## 2. EOB Matching Flow

### Current State: Fragmented Across 4 Pages

The EOB matching workflow is split across 4 disconnected pages with no clear primary flow. Users must remember which page to visit for each task, and the Manual Match Search page is a dead-end with no back navigation.

```mermaid
flowchart LR
    subgraph current["Current EOB Flow — Fragmented"]
        ENTRY[User enters EOB section]
        DASH["/eob — EOB Dashboard"]
        UNMATCHED["/eob/unmatched — Unmatched List"]
        REVIEW["/eob/matches/:id — Match Review"]
        MANUAL["/triage/manual-search — Manual Search"]

        ENTRY --> DASH
        DASH -.->|"unclear link"| UNMATCHED
        UNMATCHED -.->|"row click"| REVIEW
        DASH -.->|"hidden in triage"| MANUAL
        MANUAL -.->|"❌ NO BACK NAV"| DEAD["Dead End"]
    end

    classDef problem fill:#fee2e2,stroke:#dc2626,stroke-width:2px
    classDef deadEnd fill:#fca5a5,stroke:#991b1b,stroke-width:3px
    class DASH,UNMATCHED,REVIEW,MANUAL problem
    class DEAD deadEnd
```

**Problems:**
- No clear entry point or flow direction
- Manual Match Search lives under `/triage` despite being an EOB feature
- No breadcrumbs or back navigation between pages
- Users lose context when moving between views

---

### Future State: Unified EOB Hub with Sub-Views

The proposed design creates a single EOB Hub page with panel-based sub-views. Users stay in one context and can navigate between views without losing their place.

```mermaid
flowchart LR
    subgraph future["Future EOB Flow — Unified Hub"]
        HUB["EOB Hub<br/>(single page)"]
        
        subgraph views["Sub-Views (tabs/panels)"]
            OVERVIEW["Overview Tab<br/>Metrics + Status"]
            LIST["Unmatched Tab<br/>Filterable list"]
            REVIEW["Review Panel<br/>Side-by-side comparison"]
            SEARCH["Search Modal<br/>Manual match lookup"]
        end

        HUB --> OVERVIEW
        HUB --> LIST
        LIST -->|"select claim"| REVIEW
        REVIEW -->|"can't find match"| SEARCH
        SEARCH -->|"close modal"| REVIEW
        REVIEW -->|"done / skip"| LIST
    end

    classDef solution fill:#dcfce7,stroke:#16a34a,stroke-width:2px
    classDef solutionHub fill:#bbf7d0,stroke:#15803d,stroke-width:3px
    class HUB solutionHub
    class OVERVIEW,LIST,REVIEW,SEARCH solution
```

**Improvements:**
- Single page context — no disorienting full-page navigations
- Clear linear flow: Overview → List → Review → (optionally) Search
- Manual Search is a modal overlay, not a separate dead-end page
- Consistent back navigation (close panel returns to list)
- Breadcrumb trail always visible: Medical Claims > EOB Hub > [current view]

---

## 3. Document Triage Flow

### Current State: Two Overlapping Queues

Action Queue and Triage Queue serve similar purposes (review documents and take action) but exist as separate pages with different UIs and no shared context. Users must check both to ensure nothing is missed.

```mermaid
flowchart TD
    subgraph current["Current — Two Separate Queues"]
        USER[User needs to process documents]
        
        subgraph aq["Action Queue (/queue)"]
            AQ_LIST["Document List<br/>(two-panel layout)"]
            AQ_DETAIL["Detail Panel<br/>(inline)"]
            AQ_LIST --> AQ_DETAIL
        end
        
        subgraph tq["Triage Queue (/triage)"]
            TQ_LIST["Document List<br/>(list + detail)"]
            TQ_DETAIL["Detail Panel<br/>(5 content types!)"]
            TQ_LIST --> TQ_DETAIL
            TQ_DETAIL --> TQ_MANUAL["/triage/manual-search<br/>❌ Dead end"]
        end

        USER -->|"Which one do I check?"| aq
        USER -->|"Or this one?"| tq
    end

    classDef problem fill:#fee2e2,stroke:#dc2626,stroke-width:2px
    classDef deadEnd fill:#fca5a5,stroke:#991b1b,stroke-width:3px
    classDef confusion fill:#fef3c7,stroke:#d97706,stroke-width:2px
    class AQ_LIST,AQ_DETAIL,TQ_LIST,TQ_DETAIL problem
    class TQ_MANUAL deadEnd
    class USER confusion
```

**Problems:**
- Users don't know which queue to check — overlapping purposes
- Triage detail panel renders 5 different content types without clear context switching
- Action Queue and Triage Queue have different UI patterns for similar tasks
- Manual Search is accessible only from Triage, not from Action Queue

---

### Future State: Unified Work Queue with Filters

A single unified Work Queue replaces both pages. Users filter by document type or action needed, and the detail panel adapts its layout based on the selected item's type.

```mermaid
flowchart TD
    subgraph future["Future — Unified Work Queue"]
        QUEUE["Work Queue<br/>(single entry point)"]
        
        subgraph filters["Filter Bar"]
            F1["All Items"]
            F2["Needs Review"]
            F3["Needs Matching"]
            F4["Anomalies"]
            F5["Duplicates"]
        end

        subgraph detail["Contextual Detail Panel"]
            D1["EOB Review Layout"]
            D2["Document Review Layout"]
            D3["Anomaly Review Layout"]
            D4["Duplicate Resolution Layout"]
        end

        QUEUE --> filters
        filters -->|"select item"| detail
        detail -->|"action complete"| QUEUE
        detail -->|"need manual match"| MODAL["Search Modal<br/>(overlay, not navigation)"]
        MODAL -->|"close"| detail
    end

    classDef solution fill:#dcfce7,stroke:#16a34a,stroke-width:2px
    classDef solutionHub fill:#bbf7d0,stroke:#15803d,stroke-width:3px
    classDef solutionFilter fill:#dbeafe,stroke:#2563eb
    class QUEUE solutionHub
    class F1,F2,F3,F4,F5 solutionFilter
    class D1,D2,D3,D4,MODAL solution
```

**Improvements:**
- One place to check for all pending work
- Filters replace separate pages — faster to switch context
- Detail panel type is driven by the selected item, not the page URL
- Keyboard shortcuts work uniformly across all item types
- Clear badge counts on each filter tab show pending work

---

## 4. Statement Tracking Flow

### Current State: Linear but Disconnected

Statement tracking works as a standalone page with tabs (Providers, Missing), but there's no connection to the actions a user would take after identifying gaps (e.g., notifying a provider, creating a follow-up task).

```mermaid
stateDiagram-v2
    [*] --> StatementsPage: Navigate to /statements

    state StatementsPage {
        [*] --> ProvidersTab
        ProvidersTab --> MissingTab: Switch tab
        MissingTab --> ProvidersTab: Switch tab
        ProvidersTab --> ProviderDetail: Click provider row
        ProviderDetail --> ProvidersTab: ❌ No clear back
    }

    StatementsPage --> [*]: Leave page (no workflow continuation)

    note right of StatementsPage
        Problem: Discovery ends here.
        No path to take action on gaps.
    end note
```

**Problems:**
- Statement discovery is isolated — user identifies a gap but has no in-app path to act on it
- No connection to notifications, follow-ups, or queue items
- Provider detail page lacks consistent back navigation
- No timeline or trend view for tracking statement receipt patterns

---

### Future State: Statement Tracking with Action Integration

The future flow connects statement discovery to actionable outcomes. Users can see trends, identify gaps, and take action (notify provider, create task) without leaving the workflow.

```mermaid
flowchart LR
    subgraph future["Future Statement Tracking Flow"]
        ENTRY["Monitor > Statements"]
        
        subgraph discovery["Discovery Phase"]
            OVERVIEW["Provider Overview<br/>Receipt status grid"]
            TRENDS["Trends View<br/>Historical patterns"]
            GAPS["Gap Detection<br/>Missing statements"]
        end
        
        subgraph action["Action Phase"]
            NOTIFY["Notify Provider<br/>(inline action)"]
            TASK["Create Follow-up<br/>(adds to Work Queue)"]
            RECOMMEND["Run Recommendations<br/>(AI-suggested actions)"]
        end

        ENTRY --> OVERVIEW
        OVERVIEW --> TRENDS
        OVERVIEW --> GAPS
        GAPS --> NOTIFY
        GAPS --> TASK
        OVERVIEW --> RECOMMEND
        RECOMMEND --> TASK
        TASK -->|"appears in"| WQ["Work Queue"]
    end

    classDef solution fill:#dcfce7,stroke:#16a34a,stroke-width:2px
    classDef solutionHub fill:#bbf7d0,stroke:#15803d,stroke-width:3px
    classDef action fill:#dbeafe,stroke:#2563eb,stroke-width:2px
    class ENTRY solutionHub
    class OVERVIEW,TRENDS,GAPS solution
    class NOTIFY,TASK,RECOMMEND,WQ action
```

**Improvements:**
- Discovery flows naturally into action — no dead ends
- Trend view helps users anticipate gaps before they become problems
- Actions create items in the unified Work Queue (cross-feature integration)
- AI-powered recommendations surface actionable insights
- Breadcrumb navigation: Monitor > Statements > [Provider] maintains context

---

## Summary: Navigation Reduction

| Aspect | Current | Future |
|--------|---------|--------|
| Top-level nav items | 12 | 5 |
| Minimum screen width | ~1400px | ~900px |
| Pages for EOB workflow | 4 (scattered) | 1 (with sub-views) |
| Document queues | 2 (overlapping) | 1 (unified + filters) |
| Dead-end pages | 2+ | 0 |
| Back navigation coverage | ~40% of pages | 100% (breadcrumbs) |

---

## Implementation Priority

```mermaid
graph LR
    subgraph p1["Phase 1: Quick Wins"]
        A["Merge Overview + Dashboard"]
        B["Add breadcrumbs globally"]
        C["Fix Manual Search back nav"]
    end
    
    subgraph p2["Phase 2: Core Restructure"]
        D["Unify Action Queue + Triage"]
        E["Create EOB Hub"]
        F["Implement 5-group nav"]
    end
    
    subgraph p3["Phase 3: Enhancements"]
        G["Statement action integration"]
        H["AI recommendations"]
        I["Cross-feature Work Queue"]
    end

    p1 --> p2 --> p3

    classDef phase1 fill:#fef9c3,stroke:#ca8a04
    classDef phase2 fill:#dbeafe,stroke:#2563eb
    classDef phase3 fill:#f3e8ff,stroke:#9333ea
    class A,B,C phase1
    class D,E,F phase2
    class G,H,I phase3
```

Each phase can be shipped independently, with Phase 1 providing immediate UX relief while larger restructuring is planned.
