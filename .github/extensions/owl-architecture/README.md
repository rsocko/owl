# OWL System Map Canvas

Project-scoped Copilot canvas for assessing OWL through three connected views:

- **Capabilities** — implemented, partial, planned, and proposed product behavior.
- **Architecture** — runtime layers, components, external systems, ownership, and data flow.
- **Entities & stores** — domain definitions and their physical or authoritative storage across OWL, Paperless-ngx, Mission Control, Tyrion, and Monarch.

Each node includes a definition, status, implementation or design paths, ownership notes, and typed relationships. The entity view makes split ownership explicit: for example, Paperless owns a Correspondent's identity while OWL owns its Correspondent Profile; Paperless owns source Statement documents while OWL owns Statement Series and Document Expectations; Monarch would own financial transactions while OWL and Tyrion retain their separate interpretation domains.

The assessment is dated in `graph-data.mjs`. Code and tests take precedence over older planning documents where they conflict. In particular:

- Correspondent intelligence is marked **partial** because substantial schema, APIs, UI, and tests exist despite older roadmap text describing it as future work.
- Tyrion signals are marked **partial** because OWL has candidate and generation contracts, but no active end-to-end document/finance reconciliation transport exists.
- OCR and generalized reconciliation are marked **planned** or **proposed** because they are design artifacts without production modules.

The canvas is read-only by design. Planned nodes describe open work; editing the graph does not create issues, Mission Control tasks, or authoritative architecture changes.
