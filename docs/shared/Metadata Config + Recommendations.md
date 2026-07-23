## Your Current Metadata (Complete)

### Document Types (17) — Well-structured!

|Type|Docs|Assessment|
|---|---|---|
|**Statement**|4,130|✅ Core — Statement Tracker target|
|**Receipt**|1,365|✅ Good — informational, no action|
|**Invoice / Bill**|636|✅ Core — Action Queue "PAY" target|
|**Paystub**|610|✅ Good — informational/tax|
|**EOB**|459|✅ Core — EOB Matching target|
|**Record**|446|⚠️ Vague — what distinguishes from others?|
|**Check**|376|✅ OK|
|**Contract**|219|✅ Good — REVIEW/renewal action|
|**Manual**|150|✅ Good — reference only|
|**Letter**|146|⚠️ Could trigger RESPOND action|
|**Medical Record**|133|✅ Good|
|**ID**|48|✅ Good|
|**Credit Report**|41|✅ Good — REVIEW action|
|**Other**|21|⚠️ Catch-all|
|**Work Related**|19|⚠️ Overlaps with tags|
|**Business Card**|14|✅ Fine|
|**Charitable**|4|⚠️ Low usage, overlaps with Charity tag|

### Tags (149) — Hierarchical but with some issues

**Workflow/Status tags (critical for automation):**

- `Inbox` (236 docs) — unprocessed
- `TODO` (146 docs) — needs action
- `Paid` (113 docs) — action completed
- `Review` (0 docs) — unused
- `Submitted` (37 docs)
- `Reimbursed` (5 docs)
- `pre-process` (5 docs)
- `Workflow:Trigger` (0 docs)
- `Archive-Cleanup` (89 docs)

**Entity/Category tags (good hierarchy):**

- Family: `Tracy`, `Laney`, `Quinn`, `Ryan`, `Avery`
- Cars: 15+ vehicle-specific tags
- Houses: 4 properties + service types
- Utilities: Electric, Gas, Water, Phone
- Insurance: Medical, Car, Home, Dental, Vision, Dogs, Life
- Shopping: Clothes, Electronics, House, Other, Subscriptions

**Aggregate/rollup tags:**

- `Financial` (2,197) | `Insurance` (1,631) | `Taxes` (725) | `Tax Forms` (571) | `Utility` (562) | `Shopping` (214)

---

## Analysis & Recommendations

### 1. Bill vs. Statement — Already Separated! ✅

You already have `Invoice / Bill` (636) and `Statement` (4,130) as distinct types. This is the right call:

- **Invoice/Bill** → Action Queue generates PAY actions
- **Statement** → Statement Tracker monitors for patterns/gaps

### 2. Conceptual Overlaps

|Issue|Details|Recommendation|
|---|---|---|
|`Record` (446)|Unclear purpose — what isn't a "record"?|Review these docs. Likely split into Statement/Receipt/other or rename to "Government Record"|
|`Charitable` type (4) vs `Charity` tag (76)|Redundant|Keep the tag, delete the type (tag has 76 docs, type has 4)|
|`Work Related` type (19) vs `Job Related` tag (3) + `Work Expenses` tag (250)|Overlap|Consolidate — use tags for work categorization, not a doc type|
|`Other` type (21)|Catch-all|Fine as escape hatch, but review periodically|

### 3. Statement Tracker Multi-Account Problem

You have **355 correspondents** — many with multiple account relationships:

- `Bank of America` (992 docs) — likely checking + credit card + savings
- `Fidelity` (336) — brokerage + 401k + HSA?
- `Chase` (251) — credit card + checking?

**Current logic** groups by `(correspondent, normalized_title)` which handles this IF titles are consistent (e.g., "Credit Card" vs "Checking Account"). Your workflows confirm this — you already have rename workflows that normalize titles:

- "Checking Account Statement" → title: `Checking Account`
- "Credit Card Statement" → title: `Credit Card`

**Gap:** Tags like `Bank Account` (732), `Credit Card` (863), `Retirement Account` (339) **already differentiate account type**! The Statement Tracker should use these tags as a grouping key alongside correspondent.

### 4. What I'd Recommend Changing

**Don't change (working well):**

- Your 5 core doc types: Statement, Invoice/Bill, EOB, Receipt, Paystub
- Your hierarchical tag system (Car:_, House:_, Insurance:*, etc.)
- Your workflow status tags (Inbox, TODO, Paid)

**Suggested additions:**

- Add doc type: **Notice** (for government/regulatory letters requiring response) — currently lumped in "Letter"
- Add tag: **`Processed`** or **`AI-Reviewed`** — for Action Queue to mark docs it's already analyzed (avoids re-processing)

**Suggested cleanup:**

- Merge `Charitable` type → just use `Charity` tag
- Review `Record` type — probably split or rename
- Consider `Work Related` type → just tags

**For Action Queue intelligence:**  
The type→action mapping should be:

Plain text

```
Invoice/Bill  → PAY (high confidence)
EOB           → REVIEW + cross-match
Letter        → RESPOND (medium confidence, analyze content)
Contract      → REVIEW (renewal dates)
Credit Report → REVIEW
Statement     → FILE (no action unless anomaly)
Receipt       → ARCHIVE (no action)
```

### 5. Multi-Account Differentiation

Rather than adding a new custom field, **your existing tags already solve this**:

- `Bank Account` + `Bank of America` = checking statements
- `Credit Card` + `Bank of America` = CC statements
- `Retirement Account` + `Fidelity` = 401k statements

I recommend updating the Statement Tracker to group by `(correspondent, account-type-tag)` where account-type-tags are: `Bank Account`, `Credit Card`, `Retirement Account`, `Investment Account`, `Mortgage`, `Loan`, `HELOC`.

---

Would you like me to:

1. Update the Action Queue to use Document Types for smarter action classification?
2. Update the Statement Tracker grouping to incorporate account-type tags?
3. Create the proposed metadata changes (add Notice type, add Processed tag) via API?
4. All of the above?