---
title: "Document Naming Guidance"
sidebar_label: Naming Guidance
sidebar_position: 3
---

## Native AI Suggestions (v2.7+)

Paperless-ngx now has **built-in LLM-powered suggestions** for titles, tags, correspondents, and document types. You configure an OpenAI-compatible endpoint (OpenAI, Ollama, LM Studio, etc.) and get one-click suggestions in the UI when reviewing documents.

**Pros:** Seamless UI integration, no extra services, easy setup.  
**Cons:** Per-document (not batch), limited prompt customization, no automatic renaming on ingest without additional automation.

---

## Third-Party Options

|Tool|Strength|
|---|---|
|**[Paperless-AI](https://github.com/clusterzx/paperless-ai)**|Full classification + title extraction, runs locally, integrates via API|
|**[paperless-gpt](https://github.com/pashpash/paperless-gpt)**|Custom prompts, batch processing, sidecar service|
|**[ngx-renamer](https://github.com/paperless-ngx/paperless-ngx/discussions/8154)**|Community script for bulk AI-based renaming of existing docs|
|**n8n / automation workflows**|No-code; triggers on ingest, calls LLM, renames automatically|

---

## Recommendations for Your Situation

1. **For new documents going forward** — the native AI suggestions (v2.7+) are the lowest-friction option. Point it at Ollama or OpenAI and you'll get title suggestions on every new doc.
    
2. **For bulk renaming your existing archive** — you'll want either **Paperless-AI**, **ngx-renamer**, or an **n8n workflow** that iterates your docs via the API, sends content to an LLM with a consistent prompt template, and updates titles.
    
3. **For maximum control over naming conventions** — paperless-gpt or a custom script lets you craft a specific prompt like _"Generate a title in the format: YYYY-MM-DD - Correspondent - Description"_ and enforce it uniformly.
    

---

**Bottom line:** The native AI features are great for suggestions-on-review, but if you want _automated, opinionated, consistent naming_ (especially retroactively), a sidecar like Paperless-AI or an n8n workflow will serve you better. They're complementary — use native for day-to-day and a batch tool to clean up the backlog.