# Statement Tracking System - Implementation Summary

## Executive Summary

This document provides a comprehensive summary of the Statement Tracking and Management System design, outlining the complete solution for automatically tracking recurring statements and bills in paperless-ngx.

**Status:** ✅ **Design Complete** - Ready for implementation decision and development

---

## Problem Being Solved

**Current Pain:**
- Manual retrieval of statements from multiple provider websites
- No visibility into which statements are missing
- Uncertainty about when statements become available
- Time-consuming login and download process for each provider
- Easy to forget or miss statement downloads

**Solution:**
Intelligent system that:
1. Analyzes existing paperless-ngx documents to identify recurring statements
2. Learns the recurrence patterns (monthly, quarterly, annual, etc.)
3. Tracks what statements exist and what's missing
4. Provides smart recommendations for when to retrieve statements
5. Handles irregular schedules and provider variations
6. Allows user confirmation and manual corrections
7. Provides foundation for future automated retrieval

---

## Design Highlights

### Three Approaches Presented

#### ✅ Approach 1: Rule-Based Detection (Recommended for MVP)
- Uses explicit rules to identify statements
- Fast implementation (2-3 weeks)
- Predictable behavior
- 80-85% accuracy
- Perfect privacy (no external services)
- **Best for:** Quick start, consistent naming patterns

#### ✅ Approach 2: Machine Learning Classification
- Automatic pattern discovery using ML
- Learns from corrections
- 70-95% accuracy (improves over time)
- Requires 2-3 months implementation
- Self-hosted ML models for privacy
- **Best for:** Large collections, varied formats, long-term

#### ✅ Approach 3: Hybrid (Rule-Based + ML)
- Combines both approaches
- Rules for primary detection, ML for edge cases
- 85-95% accuracy
- Incremental complexity
- Growth path from simple to sophisticated
- **Best for:** Production deployments, balanced approach

### Core Capabilities Designed

1. **Statement Discovery**
   - Analyzes documents from paperless-ngx
   - Detects recurring patterns automatically
   - Groups by correspondent and similarity
   - Calculates confidence scores

2. **Pattern Analysis**
   - Identifies recurrence frequency (monthly, quarterly, annual)
   - Determines release timing (fixed day, last day, last business day, variable)
   - Handles irregular schedules
   - Tracks pattern confidence

3. **Missing Statement Detection**
   - Calculates expected statement dates
   - Identifies gaps in collection
   - Applies grace periods (statements may be late)
   - Priority ranking for recommendations

4. **Smart Recommendations**
   - When to check for statements
   - Availability windows (earliest to latest)
   - Priority scoring
   - Status tracking (pending, missing, overdue)

5. **User Control**
   - Review and confirm detected patterns
   - Correct statement periods manually
   - Override expected dates
   - Mark exceptions (skipped months, account changes)
   - Set custom schedules for irregular providers

---

## Key Algorithms Designed

### 1. Temporal Grouping Algorithm
Groups documents by time intervals to detect recurrence:
- Calculates intervals between consecutive documents
- Identifies modal interval (most common spacing)
- Classifies as monthly (25-35 days), quarterly (85-95 days), or annual
- Computes confidence score based on consistency

### 2. Title Similarity Clustering
Identifies statement series by title patterns:
- Normalizes titles (removes dates, numbers)
- Calculates similarity scores
- Clusters similar titles
- Identifies primary statement series

### 3. Recurrence Pattern Detection
Determines exact release schedule:
- Fixed day of month (e.g., always 5th)
- Last day of month
- Last business day of month
- Variable with average and variance
- Custom patterns for irregular providers

### 4. Expected Date Calculation
Predicts when next statement should be available:
- Applies pattern to calculate next date
- Considers historical variance
- Accounts for weekends and holidays
- Provides availability window (earliest to latest)

### 5. Missing Statement Detection
Identifies gaps in statement collection:
- Generates expected periods since last statement
- Checks if document exists for each period
- Applies grace periods (don't alert too early)
- Calculates priority based on how late it is
- Status classification (pending, missing, overdue)

---

## System Architecture

### Components

```
┌─────────────────────────────────────────────────────────┐
│                    User Interface                        │
│  ┌──────────┬──────────┬──────────┬──────────┐        │
│  │Dashboard │Providers │Statements│Settings  │        │
│  └──────────┴──────────┴──────────┴──────────┘        │
└─────────────────────────────────────────────────────────┘
                           │
                           ↓
┌─────────────────────────────────────────────────────────┐
│                      API Layer (FastAPI)                 │
│  REST Endpoints + WebSocket (real-time updates)         │
└─────────────────────────────────────────────────────────┘
                           │
                           ↓
┌─────────────────────────────────────────────────────────┐
│                   Core Processing Engine                 │
│  ┌──────────┬──────────┬──────────┬──────────┐        │
│  │Analyzer  │Detector  │Scheduler │Recommender│       │
│  └──────────┴──────────┴──────────┴──────────┘        │
└─────────────────────────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ↓                ↓                ↓
┌─────────────┐  ┌──────────────┐  ┌───────────────┐
│  Paperless  │  │   Database   │  │  Task Queue   │
│     API     │  │(SQLite/PG)   │  │(APScheduler)  │
└─────────────┘  └──────────────┘  └───────────────┘
```

### Data Flow

1. **Discovery Phase:** Paperless → Analyzer → Detector → Catalog
2. **Tracking Phase:** Catalog → Scheduler → Recommender → Dashboard
3. **User Action:** Dashboard → Catalog → Paperless (verify)
4. **Automated (Future):** Recommender → n8n → Scraper → Paperless

---

## Data Models Designed

### Provider
- ID, name, type (credit card, utility, insurance, etc.)
- Paperless correspondent mapping
- Account identifier
- Recurrence pattern
- Importance level
- Source URL (for manual downloads)
- Active status

### Recurrence Pattern
- Frequency (monthly, quarterly, annual, custom)
- Pattern type (fixed day, last day, last business day, variable)
- Pattern details (day, variance, confidence)
- Last analyzed date

### Statement
- ID, provider reference
- Paperless document ID
- Period start and end dates
- Document date
- Status (confirmed, needs review, exception)
- Metadata from paperless

### Recommendation
- Provider and period reference
- Expected date and availability window
- Status (pending, missing, overdue)
- Priority score (0-10)
- Action (download, review, ignore)
- Acknowledgment status

---

## Technology Stack Recommendations

### Backend
- **Language:** Python 3.8+
- **API Framework:** FastAPI (modern, async, auto-documentation)
- **Database:** SQLite (MVP), PostgreSQL (production)
- **ORM:** SQLAlchemy 2.0+
- **HTTP Client:** httpx (async, HTTP/2)
- **Scheduling:** APScheduler
- **Logging:** structlog (structured logs)

### Frontend
- **Framework:** React 18+ with TypeScript
- **Build Tool:** Vite
- **UI Library:** Ant Design or Material-UI
- **State:** React Query + Zustand
- **Charts:** Recharts

### ML (Optional - Future)
- **NLP:** spaCy (self-hosted)
- **Classification:** DistilBERT via transformers
- **Time Series:** Prophet
- **Local LLM:** Ollama (optional enhancement)

### Deployment
- **Containerization:** Docker + docker-compose
- **Reverse Proxy:** nginx or Caddy
- **Process Manager:** systemd
- **Backup:** restic or borg

### Principles
1. **Self-Hosted First** - Complete privacy, no external services
2. **Privacy by Design** - Sensitive data never leaves infrastructure
3. **Extensible Architecture** - Easy to add features later
4. **User Control** - Always allow manual override

---

## Implementation Roadmap

### Phase 1: MVP - Rule-Based System
**Timeline:** 2-3 weeks  
**Effort:** Medium

**Deliverables:**
- [ ] Document Analyzer (Paperless API integration)
- [ ] Pattern Detector (rule-based algorithms)
- [ ] Statement Catalog (SQLite database)
- [ ] Missing Statement Detection
- [ ] CLI interface
- [ ] Basic configuration system
- [ ] Unit tests for core algorithms

**Outcome:** Working system that can discover patterns and detect missing statements

### Phase 2: User Interface
**Timeline:** 2 weeks  
**Effort:** Medium

**Deliverables:**
- [ ] Web dashboard (React + TypeScript)
- [ ] Provider management UI
- [ ] Statement review and confirmation
- [ ] Recommendations display
- [ ] Settings and configuration UI
- [ ] Visual timeline of statements
- [ ] API documentation (auto-generated)

**Outcome:** User-friendly interface for managing statement tracking

### Phase 3: Enhanced Detection
**Timeline:** 2-3 weeks  
**Effort:** Low-Medium

**Deliverables:**
- [ ] Additional detection rules
- [ ] Improved pattern analysis
- [ ] Quarterly and annual pattern support
- [ ] Custom recurrence patterns
- [ ] Exception handling (skipped months, etc.)
- [ ] Pattern confidence improvements
- [ ] Better handling of irregular providers

**Outcome:** More accurate detection and tracking

### Phase 4: Automation Framework
**Timeline:** 3-4 weeks  
**Effort:** High

**Deliverables:**
- [ ] Plugin architecture for downloaders
- [ ] n8n workflow integration
- [ ] Web scraping framework (Playwright)
- [ ] Provider API integrations (where available)
- [ ] Automated download orchestration
- [ ] Retry and error handling
- [ ] Credential management

**Outcome:** Automated statement retrieval (no manual downloads)

### Phase 5: ML Enhancement (Optional)
**Timeline:** 4-6 weeks  
**Effort:** High

**Deliverables:**
- [ ] Data labeling interface
- [ ] Document classifier training
- [ ] NER for period extraction
- [ ] Hybrid rule + ML engine
- [ ] Continuous learning pipeline
- [ ] Model evaluation dashboard

**Outcome:** Self-improving system with higher accuracy

---

## Decision Framework

### Choose Your Approach

**Start with Approach 1 (Rules) if:**
- ✅ You want results quickly (MVP in 2-3 weeks)
- ✅ You have <50 different statement providers
- ✅ Your statements follow consistent naming patterns
- ✅ Privacy is your top priority
- ✅ You prefer predictable, understandable behavior
- ✅ You have limited ML expertise

**Consider Approach 2 (ML) if:**
- ✅ You have >100 different statement types
- ✅ Statements have wildly inconsistent formats
- ✅ You have ML expertise on your team
- ✅ You can invest 2-3 months in development
- ✅ You have enough training data (100+ examples per category)
- ✅ You want minimal long-term maintenance

**Choose Approach 3 (Hybrid) if:**
- ✅ You want the best of both worlds
- ✅ You're building for production use
- ✅ You can invest 1-2 months initially
- ✅ You want to grow capabilities over time
- ✅ You have moderate ML knowledge
- ✅ You value both speed and sophistication

### Implementation Strategy

**Recommended Path:**
1. **Start:** Implement Approach 1 (Rules) - 2-3 weeks
2. **Validate:** Use it for 1-2 months, gather feedback
3. **Enhance:** Add more rules and improve detection
4. **Optional:** Add ML layer (Approach 3) if needed
5. **Automate:** Add retrieval automation (Phase 4)

**Alternative Path (if you have ML expertise):**
1. **Start:** Implement Approach 3 (Hybrid) - 1-2 months
2. **Train:** Label data and train models
3. **Deploy:** Roll out to production
4. **Improve:** Continuous learning from corrections

---

## Resource Requirements

### Development
- **Time:** 2-12 weeks depending on approach
- **Skills:** Python, web development, optional ML
- **Team Size:** 1 developer for MVP, 2-3 for full system

### Infrastructure
- **Minimum:** 1 CPU core, 512 MB RAM, 500 MB storage
- **Recommended:** 2 CPU cores, 2 GB RAM, 5 GB storage
- **With ML:** 4 CPU cores, 4 GB RAM, 10 GB storage

### Costs
- **Self-Hosted:** $0 (use existing infrastructure)
- **Cloud VPS:** $5-10/month
- **Development Time:** Primary cost (your time investment)

---

## Key Benefits

### For Users
✅ **Time Savings:** No more manual checking of 20+ provider websites  
✅ **Peace of Mind:** Know exactly what statements you have and what's missing  
✅ **Smart Alerts:** Get notified when statements should be available  
✅ **Complete Privacy:** All processing happens locally  
✅ **Control:** Manual override and confirmation capabilities  
✅ **Future Proof:** Foundation for full automation  

### For System
✅ **Extensible:** Easy to add new features and providers  
✅ **Maintainable:** Clean architecture, well-documented  
✅ **Testable:** Comprehensive test coverage designed in  
✅ **Scalable:** Handles thousands of documents  
✅ **Reliable:** Robust error handling and recovery  

---

## Risks & Mitigations

### Risk: Pattern Detection Inaccuracy
**Impact:** Medium  
**Probability:** Medium  
**Mitigation:** 
- User confirmation required for auto-detected patterns
- Confidence scores help identify uncertain detections
- Easy correction workflow
- Manual pattern override capability

### Risk: Provider Schedule Changes
**Impact:** Low  
**Probability:** High  
**Mitigation:**
- System adapts to new patterns automatically
- User can manually update expected dates
- Grace periods prevent false alerts
- Pattern confidence decreases with anomalies (triggers review)

### Risk: Performance with Large Collections
**Impact:** Low  
**Probability:** Low  
**Mitigation:**
- Efficient database queries (indexes on key fields)
- Incremental processing (only new documents)
- Caching of analysis results
- Background processing of heavy operations
- PostgreSQL for large deployments

### Risk: Paperless API Changes
**Impact:** Medium  
**Probability:** Low  
**Mitigation:**
- Well-defined API client abstraction layer
- Comprehensive error handling
- Version compatibility checks
- Easy to update API client separately

---

## Success Metrics

### Technical Metrics
- **Pattern Detection Accuracy:** >85% for MVP, >90% with refinement
- **False Positive Rate:** <10% (statements incorrectly flagged as missing)
- **False Negative Rate:** <5% (missing statements not detected)
- **API Response Time:** <200ms (95th percentile)
- **Dashboard Load Time:** <2 seconds

### User Metrics
- **Time Saved:** 30-60 minutes per month per user
- **Statement Coverage:** >95% of recurring statements tracked
- **User Satisfaction:** Easy correction workflow, clear recommendations
- **Setup Time:** <30 minutes for initial discovery
- **Accuracy Improvement:** Pattern confidence increases over time

---

## Next Steps

### Immediate (Design Phase - Complete ✅)
- [x] Document all approaches with pros/cons
- [x] Design core algorithms
- [x] Define data models
- [x] Create architecture diagrams
- [x] Technology stack recommendations
- [x] Implementation roadmap

### User Decision Required
- [ ] **Review this design document**
- [ ] **Choose implementation approach** (1, 2, or 3)
- [ ] **Decide on scope** (MVP only, or full system?)
- [ ] **Confirm technology choices** (Python/FastAPI/React)
- [ ] **Set timeline expectations** (2 weeks? 2 months?)
- [ ] **Allocate resources** (who will implement?)

### Implementation Phase (After Approval)
- [ ] Set up development environment
- [ ] Create project structure
- [ ] Implement core algorithms (Phase 1)
- [ ] Build CLI interface
- [ ] Test with real paperless-ngx data
- [ ] Iterate based on results

### Future Phases (As Needed)
- [ ] Build web dashboard (Phase 2)
- [ ] Enhance detection capabilities (Phase 3)
- [ ] Add automation framework (Phase 4)
- [ ] Implement ML enhancements (Phase 5 - optional)

---

## Questions for Discussion

Before implementation, consider these questions:

1. **Scope:** MVP first or full system from the start?
2. **Approach:** Rule-based (fast), ML (powerful), or Hybrid (balanced)?
3. **Timeline:** What's the desired timeline? (affects approach choice)
4. **Users:** Single user or multiple users?
5. **Automation:** Implement retrieval automation immediately or later?
6. **UI:** Web dashboard required initially or CLI sufficient?
7. **ML:** Is ML expertise available if choosing Approach 2 or 3?
8. **Infrastructure:** Existing server available or need to set up?

---

## Comparison with Alternatives

### vs. Manual Tracking (Spreadsheet)
**Advantages:**
- ✅ Automatic pattern detection
- ✅ Smart reminders
- ✅ Integration with paperless-ngx
- ✅ Visual timeline

### vs. Calendar Reminders
**Advantages:**
- ✅ Adapts to provider schedule changes
- ✅ Tracks what you actually have
- ✅ Handles irregular schedules
- ✅ Historical analysis

### vs. Commercial Services (Plaid, Yodlee)
**Advantages:**
- ✅ Complete privacy
- ✅ No recurring costs
- ✅ Self-hosted control
- ✅ Works with any provider
**Disadvantages:**
- ❌ Manual retrieval (unless Phase 4 implemented)
- ❌ Initial setup time

---

## Documentation Provided

This design includes comprehensive documentation:

1. **README.md** - Overview, objectives, and getting started
2. **DESIGN.md** (This Document) - Complete system design with:
   - Three approach comparisons
   - System architecture diagrams
   - Core algorithms with pseudocode
   - Data models and schemas
   - User workflows
   - Technical considerations
3. **TECHNOLOGY-STACK.md** - Detailed technology recommendations with:
   - ADRs for each major decision
   - Alternative comparisons
   - Implementation examples
   - Security considerations
4. **QUICK-REFERENCE.md** - Operational guide with:
   - CLI commands
   - Common workflows
   - API endpoints
   - Troubleshooting
   - Configuration reference
5. **SUMMARY.md** (This Document) - Executive summary and roadmap

**Total Design Documentation:** ~80+ pages of comprehensive design

---

## Conclusion

This design provides a complete, production-ready plan for an intelligent statement tracking system that:

✅ **Solves the core problem:** Tracks recurring statements automatically  
✅ **Respects privacy:** Self-hosted, no external services  
✅ **Offers flexibility:** Multiple approaches for different needs  
✅ **Enables automation:** Foundation for future automated retrieval  
✅ **Is user-friendly:** Manual override and confirmation capabilities  
✅ **Is well-architected:** Clean, extensible, maintainable design  
✅ **Is thoroughly documented:** Complete specifications and examples  

**The design is complete and ready for implementation.**

The next step is for you to review this design, choose your preferred approach, and decide whether to proceed with implementation.

---

**Document Version:** 1.0  
**Last Updated:** 2026-02-14  
**Status:** ✅ Design Phase Complete - Ready for Implementation Decision  
**Design Author:** AI Assistant  
**For:** rsocko/ideation - Statement Tracking Experiment
