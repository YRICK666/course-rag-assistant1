from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from .db import Base


class QAHistory(Base):
    __tablename__ = "qa_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject = Column(String(255), nullable=False, index=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    hits_count = Column(Integer, default=0)
    answer_mode = Column(String(64), default="")
    source_filters = Column(Text, default="")
    warning = Column(Text, default="")
    rewritten_query = Column(Text, default="")
    hits_json = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
