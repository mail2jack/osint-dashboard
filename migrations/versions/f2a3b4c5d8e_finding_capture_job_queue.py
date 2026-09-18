"""Add tenant-isolated finding capture job queue.

This is the worker boundary for FEAT-1.  It stores only requests; no web route
or browser execution is introduced by this migration.  PostgreSQL receives
FORCE RLS and two partial unique indexes: one active job globally and one per
tenant.  Those limits are database invariants rather than worker conventions.

Revision ID: f2a3b4c5d8e
Revises: e1f2a3b4c5d7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d8e"
down_revision: str | None = "e1f2a3b4c5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE = "status IN ('queued', 'running')"


def upgrade() -> None:
    op.create_table(
        "finding_capture_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("finding_id", sa.String(36), sa.ForeignKey("findings.id"), nullable=False),
        sa.Column("requested_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target_url", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("screenshot_id", sa.String(36), sa.ForeignKey("finding_screenshots.id")),
        sa.Column("error", sa.String(300)),
        sa.Column("request_metadata", sa.JSON()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.CheckConstraint(_ACTIVE.replace(" IN ('queued', 'running')", " IN ('queued', 'running', 'completed', 'failed', 'cancelled')"), name="ck_finding_capture_job_status"),
    )
    op.create_index("ix_finding_capture_jobs_tenant_id", "finding_capture_jobs", ["tenant_id"])
    op.create_index("ix_finding_capture_jobs_status", "finding_capture_jobs", ["status"])
    op.execute(sa.text(f"CREATE UNIQUE INDEX uq_finding_capture_job_tenant_active ON finding_capture_jobs (tenant_id) WHERE {_ACTIVE}"))
    op.execute(sa.text(f"CREATE UNIQUE INDEX uq_finding_capture_job_global_active ON finding_capture_jobs ((1)) WHERE {_ACTIVE}"))
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("ALTER TABLE finding_capture_jobs ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text("ALTER TABLE finding_capture_jobs FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text("CREATE POLICY tenant_isolation ON finding_capture_jobs USING (current_setting('app.bypass_rls', true) = 'true' OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) WITH CHECK (current_setting('app.bypass_rls', true) = 'true' OR tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON finding_capture_jobs"))
        bind.execute(sa.text("ALTER TABLE finding_capture_jobs NO FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text("ALTER TABLE finding_capture_jobs DISABLE ROW LEVEL SECURITY"))
    op.drop_table("finding_capture_jobs")
