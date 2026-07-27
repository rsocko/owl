---
title: "Plugin Module Architecture"
sidebar_label: Plugin Module Architecture
sidebar_position: 3
status: proposed
created: 2026-07-27
---

# Design Document: Plugin Module Architecture

## Executive Summary

OWL currently has 5 top-level modules (Action Queue, Statements, EOB Matching, Triage, Analysis) that are manually wired into the FastAPI application via explicit imports in `app.py`. Adding a new document type requires touching multiple files: creating the module directory, writing a router, importing it in `app.py`, registering routes, initializing the database at startup, and adding navigation entries to the frontend.

This design proposes a **lightweight plugin protocol** that lets new modules self-register, reducing the cost of adding a new document type from "modify 5+ files in the core" to "add a directory that conforms to a contract."

:::info When to Implement
This is a **future consideration**, not an immediate need. The current manual wiring works well for 5 modules. Implement this when:
- A 6th+ module is being added and the boilerplate becomes painful
- The Reconciliation Engine's recipe system demonstrates the pattern works at the matching layer
- Multiple contributors are adding modules and merge conflicts in `app.py` become frequent
:::

---

## Problem Statement

### Current Module Wiring

Adding a new module today requires changes in at least 5 locations:

```
1. src/doc_intelligence_hub/modules/<name>/         # Business logic
2. src/doc_intelligence_hub/api/routers/<name>.py    # API router
3. src/doc_intelligence_hub/api/app.py               # Import + mount router
4. src/doc_intelligence_hub/api/app.py               # lifespan() DB init
5. frontend/src/components/TopNav.tsx                 # Navigation entry
```

This isn't terrible for a small team with few modules, but it creates:
- **Coupling**: `app.py` must know about every module's internals (DB init functions, router objects)
- **Merge friction**: All module additions touch the same files
- **No enforcement**: Nothing validates that a module implements the required interface

### What Already Works

The **Analysis Engine** already solved this problem for *rules*:
- `@register_rule` decorator for auto-discovery
- YAML + DB layered configuration
- Runtime enable/disable without code changes

The **Reconciliation Engine** design proposes a similar pattern for *matching recipes*:
- `RecipeBase` protocol with classify/extract/score hooks
- Recipe Registry for auto-discovery
- Per-recipe configuration

The gap is at the **module** level — the container that holds routers, DB schemas, and lifecycle hooks.

---

## Proposed Design

### Module Protocol

```python
# src/doc_intelligence_hub/core/module_protocol.py

from typing import Protocol, runtime_checkable
from fastapi import APIRouter

@runtime_checkable
class DocumentModule(Protocol):
    """Contract that every OWL module must implement."""

    @property
    def module_id(self) -> str:
        """Unique identifier (e.g., 'action_queue', 'eob_matching')."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name for UI and logs."""
        ...

    @property
    def router(self) -> APIRouter:
        """FastAPI router with all module endpoints."""
        ...

    def init_db(self) -> None:
        """Initialize module's database tables. Called during app startup."""
        ...

    def health_check(self) -> dict:
        """Return module health status for /health endpoint."""
        ...
```

### Optional Extensions

```python
class SchedulableModule(DocumentModule, Protocol):
    """Module that registers scheduled jobs."""

    def register_schedules(self, scheduler: HubScheduler) -> None:
        """Register any recurring jobs with the app scheduler."""
        ...

class NavigableModule(DocumentModule, Protocol):
    """Module that contributes navigation entries to the frontend."""

    @property
    def nav_entries(self) -> list[dict]:
        """Return navigation items for the TopNav.
        
        Each dict: {"to": "/path", "label": "Display Name", "group": "Category"}
        """
        ...
```

### Auto-Discovery

```python
# src/doc_intelligence_hub/core/module_loader.py

def discover_modules() -> list[DocumentModule]:
    """Scan modules/ directory for packages exposing a DocumentModule."""
    modules = []
    modules_dir = Path(__file__).parent.parent / "modules"
    
    for pkg_dir in sorted(modules_dir.iterdir()):
        if not pkg_dir.is_dir() or pkg_dir.name.startswith("_"):
            continue
        
        mod = importlib.import_module(f"doc_intelligence_hub.modules.{pkg_dir.name}")
        
        # Convention: module exposes a `plugin` attribute implementing DocumentModule
        if hasattr(mod, "plugin") and isinstance(mod.plugin, DocumentModule):
            modules.append(mod.plugin)
    
    return modules
```

### Simplified app.py

```python
# In create_app():
modules = discover_modules()

for mod in modules:
    app.include_router(mod.router)
    logger.info("Registered module: %s", mod.display_name)

# In lifespan():
for mod in modules:
    try:
        mod.init_db()
    except Exception as exc:
        logger.warning("Could not initialize %s DB: %s", mod.module_id, exc)
```

---

## Migration Strategy

### Phase 1: Define Protocol (Do Now — No Breaking Changes)

1. Create `module_protocol.py` with the `DocumentModule` protocol
2. **Do not change `app.py`** — existing manual wiring continues to work
3. Add protocol conformance to one existing module as proof-of-concept (Action Queue is the best candidate — it's the most self-contained)

**Effort: S (2–4 hours)**

### Phase 2: Migrate Existing Modules (Do When Adding 6th Module)

1. Add `plugin` attribute to each module's `__init__.py`
2. Switch `app.py` from manual imports to `discover_modules()`
3. Keep manual fallback for any module that doesn't conform yet

**Effort: M (1–2 days)**

### Phase 3: Frontend Auto-Discovery (Do Later)

1. Add `/api/modules` endpoint that returns registered modules + nav entries
2. TopNav fetches module list dynamically instead of hardcoding `NAV_ENTRIES`
3. Enables modules to be enabled/disabled at runtime without frontend rebuilds

**Effort: M (1–2 days)**

---

## What This Does NOT Cover

| Concern | Why Excluded | Where It Lives |
|---------|-------------|----------------|
| Analysis rule plugins | Already solved by `@register_rule` + YAML + DB | `modules/analysis/rule_registry.py` |
| Matching recipe plugins | Covered by Reconciliation Engine design | `docs/modules/reconciliation/design.md` |
| External/third-party plugins | Overkill for a single-user homelab app | Not planned |
| Hot-reloading modules | Restart is fine for this scale | Not planned |
| Module dependency graph | Modules are independent by design | Architecture principle |

---

## Decision Criteria

**Implement Phase 1 when:**
- ✅ Free — it's just a protocol definition, no migration required

**Implement Phase 2 when ANY of:**
- Adding a 6th+ module
- Merge conflicts in `app.py` become a recurring pain point
- The Reconciliation Engine ships and validates the recipe pattern

**Implement Phase 3 when:**
- Modules are being enabled/disabled per-deployment
- Nav entries change frequently enough to warrant dynamic loading

---

## Relationship to Other Designs

- **Reconciliation Engine** (`docs/modules/reconciliation/design.md`): Plugin architecture for *matching recipes* within the reconciliation module. This design covers the *module container* level above it.
- **Analysis Rule Registry** (`modules/analysis/rule_registry.py`): Plugin architecture for *analysis rules*. Proven pattern that this design draws from.
- **Audit Finding P5** (`docs/design/active/audit-findings.md`, line 69): Original observation that prompted this design.

---

## References

- [Audit Findings — Priority 5: Observations](./../../design/active/audit-findings.md#priority-5-observations--future-considerations)
- [Architecture Overview — Module Organization](./../../architecture/index.md#module-organization)
- [Feature Roadmap — Infrastructure](./../../feature-roadmap.md#infrastructure--platform)
- [Reconciliation Engine Design — Recipe Registry](./../../modules/reconciliation/design.md)
