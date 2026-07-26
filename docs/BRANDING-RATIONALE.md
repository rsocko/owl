# OWL — Branding Rationale

> **Decision: OWL** — `service-005.example.invalid`
> Tagline: "Organize. Watch. Learn."

## Why OWL

### The Backronym Maps to the Feature Set

| Letter | Meaning | Features |
|--------|---------|----------|
| **O** — Organize | Triage, classify, structure | Action Queue (PAY/RESPOND/FILE/SIGN), EOB↔Bill matching, document classification |
| **W** — Watch | Monitor, alert, detect gaps | Statement gap detection, overdue alerts, missing document monitoring, unified alert inbox |
| **L** — Learn | Pattern recognition, scoring | Recurring document patterns, urgency scoring, 5-factor weighted matching, OCR quality assessment |

### The Metaphor Holds Under Scrutiny

- **Sees in the dark** → Finds what's hidden (missing statements, orphaned EOBs, billing errors)
- **Silent hunter** → Background scheduled processing, precise extraction
- **Always alert** → Monitoring/alerting system running on schedule (APScheduler, daily/2x-daily)
- **Patient and precise** → Tiered OCR remediation, weighted scoring algorithms
- **Wisdom** → Athena's companion; turning document chaos into actionable intelligence

### Practical Strengths

- **3 characters** — maximally tight subdomain (`service-005.example.invalid`)
- **Universal recognition** — no explanation needed; the qualities are instantly understood
- **Accommodates growth** — every new feature maps naturally (gap detection → owl watching; EOB matching → owl seeing patterns; OCR quality → owl's sharp eyes)
- **Works as both API identity and app brand** — OWL has its own rich React/TypeScript UI with 10+ pages; the brand is user-facing, not just internal

## Brand Design Language

The OWL metaphor informs the visual identity:

- **Deep navy palette** (`#1A1B2E` → `#2D2F54`) — night sky, owl's domain; reduces eye strain for document-heavy review work
- **Amber/gold accents** (`#F5A623`, `#FFD666`) — owl eyes, warmth of wisdom, attention-drawing for alerts
- **Space Grotesk typography** — geometric, modern, slightly technical
- **Dark theme default** — reinforces the "sees in the dark" metaphor; practical for data-dense dashboards
- **Amber "glow" for alerts** — natural: owl eyes catching something in the dark
- **Moonlight/silver for muted text** — cool, calm, secondary information recedes

## Runner-Up: Iris

`service-003.example.invalid` — "See · Deliver · Connect"

### What Iris Offered

- **Messenger goddess** — maps to alerting/notification delivery
- **Eye (iris)** — maps to OCR, document scanning, "seeing" content
- **Rainbow bridge** — connecting raw documents to structured knowledge; bridging Paperless to the user
- **Violet/indigo palette** — visually striking, prismatic spectrum accents

### Why OWL Won

- Iris connotes *passing through* (messenger). OWL connotes *commanding knowledge* (wisdom + action). The service doesn't just relay — it triages, matches, decides, and alerts.
- The vision/eye connection covers only the OCR layer. The service's primary value is organization and alerting.
- OWL clicks on instinct. Iris requires explanation of the layered meaning.
- 3 chars vs 4 chars.
- The "See · Deliver · Connect" tagline describes pipeline mechanics. "Organize. Watch. Learn." describes user value.

### Iris Remains Valid For

If a future service is primarily about message delivery, bridging systems, or visual processing, Iris is a strong candidate. The brand kit (`branding-iris.html`) is preserved for potential reuse.

## Other Names Considered

| Name | Concept | Why Not |
|------|---------|---------|
| Athena | Wisdom, strategy | Too long for subdomain; OWL captures the same essence in 3 chars |
| Vigil | Watchfulness | Maps to monitoring but misses "organize" and "learn" |
| Sentry | Guardian | Conflicts with Sentry.io; 6 chars |
| Rook | Chess strategy + crow | Less immediately evocative; double-meaning isn't well-known |
| Keen | Sharp perception | Too thin as a noun; no weight |
| Scribe/Codex/Tome | Document-adjacent | Describe what's processed, not what the service *does* |

## Context in the Service Ecosystem

- **OWL** — Document Intelligence Hub (organize, monitor, match documents)
- **Mission Control** — Unified frontend dashboard (consumes OWL's API among others)
- **Paperless-ngx** — Document storage/ingestion (upstream of OWL)

OWL is a standalone app with its own rich UI AND a headless API consumed by Mission Control. The brand appears in:
- The app's own top nav, favicon, and login screen
- Docker container names and compose labels
- Log output and alert source attribution ("Alert from: OWL")
- Portainer/dashboard service listings
- Internal documentation and architecture diagrams

## On Owl Puns and Sound Effects ("Hoo" / "Who")

### Verdict: Skip it. The brand is stronger without.

The temptation exists to work owl sounds into the UI -- "Hoo's watching your documents?" as a tagline, "Who" as a search placeholder, "Hoot" as a notification sound name, etc.

**Why it doesn't work here:**

- **Tone mismatch.** OWL's strength is *quiet competence* -- the owl that sees everything without making noise. The brand personality is calm, precise, watchful. Puns inject whimsy that undercuts the "I trust this system with my finances" feeling.
- **Wears thin fast.** A pun is funny once. You'll see this UI daily. By week two, "Hoo wants to review 3 documents?" goes from cute to grating.
- **Audience of one.** In a team product, playful copy builds culture. In a solo homelab tool, it's just you cringing at your past self.
- **The name already does the work.** "OWL" and "Organize. Watch. Learn." carry all the personality needed. Adding sound puns is gilding the lily.

**Where owl personality CAN show up (subtly):**

- **Empty states** -- A minimal owl silhouette with "Nothing to watch" or "All clear" is fine. It's visual, not punny.
- **Error/404 page** -- A confused owl illustration is charming without being a pun.
- **Favicon/icon** -- Owl eyes or silhouette as the app icon. Strong, silent, recognizable.
- **Alert severity names** (internal only) -- If you wanted "hoot" as an alert level name in code/config, that's developer-facing and harmless. But don't surface it in UI copy.
- **Commit messages / changelogs** -- Occasional owl emoji or light reference in developer-facing text is fine. It's ephemeral.

**The guiding principle:** The owl metaphor should be *felt* (dark palette, amber alert glow, watchful icon) rather than *stated* (puns, sound effects, forced wordplay). Show, don't tell.

## Decision Date

July 2026. Revisit only if another bird-themed service name creates ecosystem confusion.
