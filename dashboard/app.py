"""Streamlit dashboard for the Paperless Action Queue."""

import streamlit as st
import asyncio
from datetime import datetime

from doc_intelligence_hub.modules.action_queue.database import get_session, init_db, Action
from doc_intelligence_hub.modules.action_queue.enricher import PaperlessEnricher
from doc_intelligence_hub.modules.action_queue.config import settings

# Page config
st.set_page_config(
    page_title="Paperless Action Queue",
    page_icon="📋",
    layout="wide",
)

# Initialize DB
init_db()


def get_actions(status_filter=None, urgency_filter=None, type_filter=None):
    """Fetch actions from the database."""
    db = get_session()
    query = db.query(Action)

    if status_filter:
        query = query.filter(Action.status == status_filter)
    if urgency_filter:
        query = query.filter(Action.urgency == urgency_filter)
    if type_filter:
        query = query.filter(Action.action_type == type_filter)

    actions = query.order_by(Action.due_date.asc().nullslast(), Action.urgency.desc()).all()
    db.close()
    return actions


def update_action_status(action_id: int, new_status: str):
    """Update action status and sync back to Paperless."""
    db = get_session()
    action = db.query(Action).filter_by(id=action_id).first()
    if action:
        action.status = new_status
        action.updated_at = datetime.utcnow()
        if new_status == "completed":
            action.completed_at = datetime.utcnow()
        db.commit()

        # Sync status back to Paperless
        try:
            enricher = PaperlessEnricher()
            asyncio.run(enricher.sync_status(action.document_id, new_status))
        except Exception as e:
            st.warning(f"Status updated locally but Paperless sync failed: {e}")

    db.close()


# Header
st.title("📋 Paperless Action Queue")
st.caption("Extracted actions from your Paperless-NGX documents")

# Sidebar filters
with st.sidebar:
    st.header("Filters")

    status_options = ["pending", "completed", "dismissed", "all"]
    selected_status = st.selectbox("Status", status_options, index=0)

    urgency_options = ["All", "CRITICAL", "HIGH", "MEDIUM", "LOW"]
    selected_urgency = st.selectbox("Urgency", urgency_options, index=0)

    type_options = ["All", "PAY", "RESPOND", "FILE", "REVIEW", "SHARE", "SCHEDULE", "SIGN", "ARCHIVE"]
    selected_type = st.selectbox("Action Type", type_options, index=0)

    st.divider()

    if st.button("🔄 Run Pipeline", type="primary"):
        with st.spinner("Analyzing documents..."):
            from doc_intelligence_hub.modules.action_queue.pipeline import run_pipeline
            stats = asyncio.run(run_pipeline())
            st.success(
                f"Done! Processed: {stats['processed']}, "
                f"Skipped: {stats['skipped']}, Failed: {stats['failed']}"
            )
            st.rerun()

# Apply filters
status_f = None if selected_status == "all" else selected_status
urgency_f = None if selected_urgency == "All" else selected_urgency
type_f = None if selected_type == "All" else selected_type

actions = get_actions(status_filter=status_f, urgency_filter=urgency_f, type_filter=type_f)

# Summary metrics
col1, col2, col3, col4 = st.columns(4)
db = get_session()
with col1:
    st.metric("Pending", db.query(Action).filter_by(status="pending").count())
with col2:
    critical = db.query(Action).filter(
        Action.status == "pending", Action.urgency.in_(["CRITICAL", "HIGH"])
    ).count()
    st.metric("Urgent", critical)
with col3:
    st.metric("Completed", db.query(Action).filter_by(status="completed").count())
with col4:
    st.metric("Total", db.query(Action).count())
db.close()

st.divider()

# Action cards
if not actions:
    st.info("No actions match your filters.")
else:
    for action in actions:
        urgency_colors = {
            "CRITICAL": "🔴",
            "HIGH": "🟠",
            "MEDIUM": "🟡",
            "LOW": "🟢",
        }
        urgency_icon = urgency_colors.get(action.urgency, "⚪")

        with st.container(border=True):
            col_main, col_actions = st.columns([4, 1])

            with col_main:
                st.markdown(f"### {urgency_icon} {action.title}")
                st.caption(
                    f"**{action.action_type}** · "
                    f"{'Due: ' + str(action.due_date) if action.due_date else 'No due date'} · "
                    f"{'$' + f'{action.amount:.2f}' if action.amount else ''} · "
                    f"Confidence: {action.confidence}%"
                )
                if action.summary:
                    st.write(action.summary)

                # Show extracted data in expander
                if action.extracted_data:
                    with st.expander("Extracted Details"):
                        st.json(action.extracted_data)
                if action.ai_reasoning:
                    with st.expander("AI Reasoning"):
                        st.write(action.ai_reasoning)

            with col_actions:
                if action.status == "pending":
                    if st.button("✅ Complete", key=f"complete_{action.id}"):
                        update_action_status(action.id, "completed")
                        st.rerun()
                    if st.button("❌ Dismiss", key=f"dismiss_{action.id}"):
                        update_action_status(action.id, "dismissed")
                        st.rerun()

                # Link to Paperless document
                paperless_url = f"{settings.paperless_url}/documents/{action.document_id}"
                st.markdown(f"[📄 View in Paperless]({paperless_url})")
