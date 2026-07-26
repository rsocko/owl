---
title: "Reconciliation UI Flows"
sidebar_label: UI Flows
sidebar_position: 2
---

# Reconciliation Engine — UI Flow Diagrams

## 1. Navigation Structure

How the Reconciliation Engine fits into OWL's navigation:

```mermaid
graph TD
    subgraph nav["OWL Navigation"]
        A["🦉 OWL Dashboard"]
        B["📋 Action Queue"]
        C["📊 Statements"]
        D["🏥 Medical Matching"]
        E["💰 Reconciliation ← NEW"]
        F["🔔 Alerts & Insights"]
        G["🔧 Triage & Correction"]
        H["⚙️ Settings"]
    end

    E --> E1["Dashboard (all recipes)"]
    E --> E2["Receipt ↔ Bill"]
    E --> E3["Order Lifecycle"]
    E --> E4["Insurance & Policies"]
    E --> E5["Contracts"]
    E --> E6["Recipe Config"]

    D -->|"migrates into"| E

    style E fill:#3498db,stroke:#2980b9,color:#fff
    style D fill:#fef3c7,stroke:#f59e0b
```

:::info Navigation Evolution
Initially, "Medical Matching" remains as-is. In Phase 2+, it becomes a sub-view under "Reconciliation" (as the `eob_bill` recipe). The transition is gradual — users see the same UI, just nested differently.
:::

---

## 2. Generalized Matching Pipeline

Every reconciliation scenario follows this pipeline:

```mermaid
flowchart TD
    A([📄 New Document Arrives]) --> B{Classifier Pipeline}

    B -->|"Medical EOB/Bill"| C1[EOB ↔ Bill Recipe]
    B -->|"Receipt/Confirmation"| C2[Receipt ↔ Bill Recipe]
    B -->|"Order/Invoice/Shipping"| C3[Order Lifecycle Recipe]
    B -->|"Policy/Premium"| C4[Insurance Recipe]
    B -->|"Unclassified"| C5[Manual Triage Queue]

    C1 --> D[Extract Structured Fields]
    C2 --> D
    C3 --> D
    C4 --> D

    D --> E[Score Against Candidates]
    E --> F{Confidence Level?}

    F -->|"≥ 85% (auto-confirm)"| G[✅ Auto-Link Documents]
    F -->|"70-84% (review zone)"| H[👁️ Queue for Review]
    F -->|"< 70% (no match)"| I[📋 Mark Unmatched]

    G --> J[Update Lifecycle State]
    H --> K{User Decision}
    K -->|Confirm| J
    K -->|Reject| L[Remove Match]
    K -->|Relink| M[Search for Better Match]
    M --> E

    J --> N[Notify / Alert]
    N --> O([Match Complete])

    style A fill:#e8f4fd,stroke:#3498db
    style G fill:#d1fae5,stroke:#10b981
    style H fill:#fef3c7,stroke:#f59e0b
    style I fill:#fef2f2,stroke:#ef4444
    style O fill:#d1fae5,stroke:#10b981
```

---

## 3. Receipt ↔ Bill Matching Flow

### User Flow: Bill Arrives First

```mermaid
sequenceDiagram
    participant U as User
    participant P as Paperless-ngx
    participant E as Reconciliation Engine
    participant UI as OWL Dashboard

    U->>P: Upload bill (scan/email)
    P->>P: OCR processing
    E->>P: Poll for new documents
    P-->>E: New bill detected

    E->>E: Classify as "bill_invoice"
    E->>E: Extract: amount=$89.99, provider=Comcast, due=Jul 15

    E->>UI: Show in "Awaiting Payment" list
    UI-->>U: 📋 New bill: Comcast $89.99 due Jul 15

    Note over U: User pays bill online

    U->>P: Upload payment receipt
    P->>P: OCR processing
    E->>P: Poll for new documents
    P-->>E: New receipt detected

    E->>E: Classify as "payment_receipt"
    E->>E: Extract: amount=$89.99, merchant=Comcast, date=Jul 12

    E->>E: Score receipt against unmatched bills
    Note over E: Amount: 100% ✓<br/>Provider: 92% ✓<br/>Date: 85% ✓<br/>Total: 91%

    alt Score ≥ 85% (auto-confirm)
        E->>P: Create document link
        E->>UI: ✅ Auto-matched: Comcast receipt → bill
        UI-->>U: 🎉 Bill confirmed paid!
    else Score 70-84% (needs review)
        E->>UI: ⚠️ Possible match needs review
        UI-->>U: 👁️ Review: Comcast receipt → bill?
        U->>UI: Confirm match
        UI->>E: User confirmed
        E->>P: Create document link
    end
```

### User Flow: Double Payment Detection

```mermaid
sequenceDiagram
    participant U as User
    participant E as Reconciliation Engine
    participant UI as OWL Dashboard

    Note over E: Bill #1042 already matched to Receipt #1087<br/>Status: Confirmed Paid

    U->>E: New receipt uploaded (same provider, same amount)
    E->>E: Score against all bills

    E->>E: Best match: Bill #1042 (score: 89%)
    E->>E: ⚠️ Bill #1042 already has a receipt!

    E->>UI: 🚨 DOUBLE PAYMENT ALERT
    UI-->>U: ⚠️ Possible double payment detected!<br/>Comcast $89.99 paid on Jul 12 AND Jul 14

    U->>UI: Investigate
    UI-->>U: Show comparison:<br/>Receipt 1: Jul 12, Conf# CNF-9923847<br/>Receipt 2: Jul 14, Conf# CNF-9924102

    alt Actually double payment
        U->>UI: Flag for refund
        E->>E: Update lifecycle → "Double Payment"
    else Different billing periods
        U->>UI: Link to next month's bill
        E->>E: Re-link receipt to correct bill
    end
```

---

## 4. Order Lifecycle Flow (3-Way Matching)

```mermaid
sequenceDiagram
    participant U as User
    participant E as Reconciliation Engine
    participant UI as OWL Dashboard
    participant MC as Mission Control

    U->>E: Order confirmation uploaded
    E->>E: Classify: order_confirmation
    E->>E: Extract: order#=ORD-2026-4521, merchant=Amazon, total=$142.67

    E->>UI: New order tracked: Amazon ORD-2026-4521

    Note over U: Days pass...

    U->>E: Invoice/charge notification uploaded
    E->>E: Classify: invoice
    E->>E: Extract: order#=ORD-2026-4521, total=$142.67

    E->>E: Score: order# exact match (40%) + merchant match + amount match
    Note over E: Score: 97% → Auto-link

    E->>E: Validate: invoice $142.67 = order $142.67 ✅
    E->>UI: ✅ Invoice matches order (amount verified)

    Note over U: More days pass...

    U->>E: Shipping notice uploaded
    E->>E: Classify: shipping_notice
    E->>E: Extract: order#=ORD-2026-4521, tracking=1Z999AA10123456784

    E->>E: Score: order# match → link to group
    E->>MC: Reference tracking# 1Z999AA10123456784
    MC-->>E: Delivery status: In Transit, ETA Jul 28

    E->>UI: 📦 Order shipped! Tracking linked.
    E->>E: Lifecycle: Ordered → Invoiced → Shipped

    Note over U: Package delivered

    MC-->>E: Delivery confirmed Jul 28
    E->>E: Lifecycle: Shipped → Delivered → Complete
    E->>UI: ✅ Order complete: all documents matched
```

---

## 5. Reconciliation Dashboard Flow

```mermaid
flowchart TD
    A([User opens Reconciliation]) --> B[Dashboard View]

    B --> C[Recipe Summary Cards]
    C --> C1["Receipt ↔ Bill<br/>12 matched · 3 pending · 1 alert"]
    C --> C2["Order Lifecycle<br/>5 tracked · 2 in transit"]
    C --> C3["EOB ↔ Bill<br/>28 matched · 1 needs review"]
    C --> C4["Insurance<br/>4 policies · all current"]

    B --> D[Global Review Queue]
    D --> D1["3 items need review across all recipes"]

    B --> E[Recent Activity Feed]
    E --> E1["Auto-matched Comcast receipt (2 min ago)"]
    E --> E2["New bill awaiting payment: Electric $156 (1 hr ago)"]
    E --> E3["⚠️ Double payment alert: Gym $49.99 (3 hrs ago)"]

    D1 -->|Click| F[Review Item Detail]
    F --> F1[Side-by-side comparison]
    F --> F2[Confidence breakdown]
    F --> F3[Action buttons: Confirm / Reject / Relink]

    C1 -->|Click| G[Recipe Detail View]
    G --> G1[All matches for this recipe]
    G --> G2[Lifecycle state filters]
    G --> G3[Unmatched documents list]

    style B fill:#e8f4fd,stroke:#3498db
    style D fill:#fef3c7,stroke:#f59e0b
```

---

## 6. Recipe Configuration Flow

```mermaid
flowchart TD
    A([Admin opens Recipe Config]) --> B[Recipe List]

    B --> B1["eob_bill — Active ✅"]
    B --> B2["receipt_bill — Active ✅"]
    B --> B3["order_lifecycle — Active ✅"]
    B --> B4["insurance_premium — Disabled ⏸️"]

    B2 -->|Click| C[Recipe Editor]

    C --> D[Scoring Weights Panel]
    D --> D1["Amount: 35% ←slider→"]
    D --> D2["Provider: 25% ←slider→"]
    D --> D3["Date: 20% ←slider→"]
    D --> D4["Reference: 15% ←slider→"]
    D --> D5["Account: 5% ←slider→"]

    C --> E[Thresholds Panel]
    E --> E1["Auto-confirm: 85% ←slider→"]
    E --> E2["Review zone: 70% ←slider→"]
    E --> E3["Match window: 90 days"]

    C --> F[Lifecycle Editor]
    F --> F1["Visual state diagram (read-only)"]
    F --> F2["State list with colors"]

    C --> G[Test Panel]
    G --> G1["Pick two documents → preview score"]
    G --> G2["Show factor breakdown"]

    C --> H{Save Changes?}
    H -->|Save| I[Update recipe config]
    H -->|Cancel| B

    style C fill:#e8f4fd,stroke:#3498db
    style G fill:#d1fae5,stroke:#10b981
```

---

## 7. Lifecycle State Diagrams

### Receipt ↔ Bill Lifecycle

```mermaid
stateDiagram-v2
    [*] --> awaiting_payment : Bill classified
    awaiting_payment --> payment_detected : Receipt uploaded & scored
    awaiting_payment --> overdue : Past due date (no receipt)
    payment_detected --> confirmed_paid : Score ≥ 85% OR user confirms
    payment_detected --> needs_review : Score 70-84%
    needs_review --> confirmed_paid : User confirms
    needs_review --> rejected : User rejects
    overdue --> payment_detected : Late receipt arrives
    confirmed_paid --> double_payment : Second receipt for same bill
    double_payment --> refund_pending : User requests refund
    refund_pending --> resolved : Refund received

    confirmed_paid --> [*]
    rejected --> [*]
    resolved --> [*]
```

### Order Lifecycle

```mermaid
stateDiagram-v2
    [*] --> ordered : Order confirmation classified
    ordered --> invoiced : Invoice matched to order
    ordered --> shipped : Shipping notice first (no invoice yet)
    invoiced --> shipped : Shipping notice arrives
    shipped --> invoiced : Late invoice arrives
    shipped --> delivered : Delivery confirmed
    invoiced --> delivered : Delivery confirmed (no shipping doc)
    delivered --> complete : All 3 docs matched & verified
    delivered --> amount_mismatch : Invoice ≠ Order amount

    amount_mismatch --> disputed : User opens dispute
    complete --> return_initiated : Return started
    return_initiated --> refunded : Refund confirmed

    complete --> [*]
    refunded --> [*]
    disputed --> [*]
```

---

## 8. Review Queue Interaction

```mermaid
flowchart LR
    A[Review Queue] --> B[Match Card]

    B --> C["Left: Document A<br/>(Bill)"]
    B --> D["Right: Document B<br/>(Receipt)"]
    B --> E["Center: Score Bar<br/>78% — Needs Review"]

    B --> F{User Action}
    F -->|"✓ Confirm (Y)"| G[Link in Paperless<br/>→ Move to Confirmed]
    F -->|"✗ Reject (N)"| H[Discard match<br/>→ Back to Unmatched]
    F -->|"🔗 Relink (R)"| I[Search modal<br/>→ Pick different doc]
    F -->|"⏭ Skip (S)"| J[Next item<br/>→ Keep in queue]

    G --> K[Next Review Item]
    H --> K
    I --> K
    J --> K

    style E fill:#fef3c7,stroke:#f59e0b
    style G fill:#d1fae5,stroke:#10b981
    style H fill:#fef2f2,stroke:#ef4444
```

---

## 9. Integration Points

```mermaid
graph LR
    subgraph owl["OWL Modules"]
        REC["Reconciliation Engine"]
        ST["Statement Tracking"]
        AQ["Action Queue"]
        AI["Alerts & Insights"]
        TC["Triage & Correction"]
    end

    subgraph external["External"]
        PL["Paperless-ngx"]
        MC["Mission Control"]
        MO["Monarch (future)"]
    end

    REC -->|"Recurring bill patterns"| ST
    REC -->|"'PAY' actions for due bills"| AQ
    REC -->|"Match alerts, double-pay"| AI
    REC -->|"Uncertain matches"| TC

    REC -->|"Doc links, tags"| PL
    REC -->|"Package tracking ref"| MC
    REC -.->|"Transaction data (future)"| MO

    style REC fill:#3498db,stroke:#2980b9,color:#fff
```

---

## 10. Mobile / Notification Flow

```mermaid
sequenceDiagram
    participant E as Engine
    participant N as Notification System
    participant U as User (Mobile)

    E->>E: New match auto-confirmed
    E->>N: Emit: "Comcast bill confirmed paid ✅"
    N->>U: Push notification

    E->>E: Double payment detected!
    E->>N: Emit: "⚠️ Possible double payment: Gym $49.99"
    N->>U: Push notification (HIGH priority)
    U->>N: Open notification
    N->>U: Deep link to review view
    U->>E: Confirm / Dismiss
```
