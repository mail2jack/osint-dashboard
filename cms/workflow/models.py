from datetime import datetime
from sqlalchemy import (
    Column,
    String,
    Text,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
)
from sqlalchemy.orm import relationship
from . import _base


class WorkflowCaseSubject(_base):
    __tablename__ = "workflow_case_subjects"
    case_id = Column(String(36), ForeignKey("workflow_cases.id"), primary_key=True)
    subject_id = Column(
        String(36), ForeignKey("workflow_subjects.id"), primary_key=True
    )


class WorkflowClient(_base):
    __tablename__ = "workflow_clients"

    id = Column(String(36), primary_key=True)
    name = Column(String(200), nullable=False)
    contact_person = Column(String(200))
    email = Column(String(200))
    phone = Column(String(50))
    reference = Column(String(100))
    street = Column(String(200))
    house_number = Column(String(20))
    house_number_addition = Column(String(20))
    postal_code = Column(String(20))
    city = Column(String(100))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.now)

    cases = relationship(
        "WorkflowCase", back_populates="client", cascade="all,delete-orphan"
    )


class WorkflowSubject(_base):
    __tablename__ = "workflow_subjects"

    id = Column(String(36), primary_key=True)
    name = Column(String(200), nullable=False)
    subject_type = Column(String(50), default="person")
    identification_number = Column(String(100))
    email = Column(String(200))
    phone = Column(String(50))
    street = Column(String(200))
    house_number = Column(String(20))
    house_number_addition = Column(String(20))
    postal_code = Column(String(20))
    city = Column(String(100))
    social_accounts = Column(JSON)
    risk_score = Column(Integer, default=0)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


class WorkflowCase(_base):
    __tablename__ = "workflow_cases"

    id = Column(String(36), primary_key=True)
    case_number = Column(String(50), nullable=False)
    title = Column(String(200))
    status = Column(String(20), default="open")
    priority = Column(String(20), default="medium")
    description = Column(Text)
    client_id = Column(String(36), ForeignKey("workflow_clients.id"))
    lead_investigator = Column(String(100))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    pv_body = Column(Text)
    client = relationship("WorkflowClient", back_populates="cases")
    subjects = relationship("WorkflowSubject", secondary="workflow_case_subjects")
    actions = relationship(
        "WorkflowResearchAction", back_populates="case", cascade="all,delete-orphan"
    )
    findings = relationship(
        "WorkflowFinding", back_populates="case", cascade="all,delete-orphan"
    )


class WorkflowResearchAction(_base):
    __tablename__ = "workflow_research_actions"

    id = Column(String(36), primary_key=True)
    case_id = Column(String(36), ForeignKey("workflow_cases.id"), nullable=False)
    action_type = Column(String(50), nullable=False)
    data_value = Column(Text)
    label = Column(String(200))
    status = Column(String(20), default="pending")
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error = Column(Text)
    result_summary = Column(Text)
    cancel_requested = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)

    case = relationship("WorkflowCase", back_populates="actions")
    findings = relationship(
        "WorkflowFinding",
        secondary="workflow_action_findings",
        back_populates="actions",
    )


class WorkflowFinding(_base):
    __tablename__ = "workflow_findings"

    id = Column(String(36), primary_key=True)
    case_id = Column(String(36), ForeignKey("workflow_cases.id"), nullable=False)
    title = Column(String(300), nullable=False)
    detail = Column(Text)
    source_url = Column(String(500))
    source_type = Column(String(50))
    icon = Column(String(10), default="📄")
    verified = Column(Boolean, default=False)
    comment = Column(Text)
    raw_data = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)
    archived_at = Column(DateTime, nullable=True, index=True)

    case = relationship("WorkflowCase", back_populates="findings")
    actions = relationship(
        "WorkflowResearchAction",
        secondary="workflow_action_findings",
        back_populates="findings",
    )
    screenshots = relationship(
        "WorkflowScreenshot", back_populates="finding", cascade="all,delete-orphan"
    )


class WorkflowActionFinding(_base):
    __tablename__ = "workflow_action_findings"
    action_id = Column(
        String(36), ForeignKey("workflow_research_actions.id"), primary_key=True
    )
    finding_id = Column(
        String(36), ForeignKey("workflow_findings.id"), primary_key=True
    )


class WorkflowScreenshot(_base):
    __tablename__ = "workflow_screenshots"

    id = Column(String(36), primary_key=True)
    finding_id = Column(String(36), ForeignKey("workflow_findings.id"), nullable=False)
    url = Column(String(500))
    source_url = Column(String(500))
    file_path = Column(String(500))
    captured_at = Column(DateTime, default=datetime.now)
    notes = Column(Text)

    finding = relationship("WorkflowFinding", back_populates="screenshots")
