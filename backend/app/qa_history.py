from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import desc

from .db import get_session
from .models import QAHistory


def ensure_qa_history_schema() -> None:
    """Add hits_json column to qa_history if missing (idempotent)."""
    session = get_session()
    try:
        from sqlalchemy import inspect, text

        inspector = inspect(session.bind)
        columns = [col["name"] for col in inspector.get_columns("qa_history")]
        if "hits_json" not in columns:
            session.execute(text("ALTER TABLE qa_history ADD COLUMN hits_json TEXT DEFAULT ''"))
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_qa_record(
    subject: str,
    question: str,
    answer: str,
    *,
    hits_count: int = 0,
    answer_mode: str = "",
    source_filters: list[str] | None = None,
    warning: str = "",
    rewritten_query: str = "",
    hits: list[dict] | None = None,
) -> QAHistory:
    session = get_session()
    try:
        record = QAHistory(
            subject=subject,
            question=question,
            answer=answer,
            hits_count=hits_count,
            answer_mode=answer_mode,
            source_filters=json.dumps(source_filters or [], ensure_ascii=False),
            warning=warning,
            rewritten_query=rewritten_query,
            hits_json=json.dumps(hits or [], ensure_ascii=False),
            created_at=datetime.utcnow(),
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_qa_records(
    subject: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[QAHistory]:
    session = get_session()
    try:
        query = (
            session.query(QAHistory)
            .filter(QAHistory.subject == subject)
            .order_by(desc(QAHistory.created_at))
            .offset(offset)
            .limit(limit)
        )
        return list(query)
    finally:
        session.close()


def get_qa_record(record_id: int) -> QAHistory | None:
    session = get_session()
    try:
        return session.query(QAHistory).filter(QAHistory.id == record_id).first()
    finally:
        session.close()


def delete_qa_record(record_id: int) -> bool:
    session = get_session()
    try:
        record = session.query(QAHistory).filter(QAHistory.id == record_id).first()
        if not record:
            return False
        session.delete(record)
        session.commit()
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def qa_history_to_dict(record: QAHistory) -> dict[str, Any]:
    hits_raw = record.hits_json
    if hits_raw:
        try:
            hits = json.loads(hits_raw)
        except (json.JSONDecodeError, TypeError):
            hits = []
    else:
        hits = []
    return {
        "id": record.id,
        "subject": record.subject,
        "question": record.question,
        "answer": record.answer,
        "hits_count": record.hits_count,
        "answer_mode": record.answer_mode,
        "source_filters": json.loads(record.source_filters) if record.source_filters else [],
        "warning": record.warning,
        "rewritten_query": record.rewritten_query,
        "hits": hits,
        "created_at": record.created_at.isoformat(),
    }
