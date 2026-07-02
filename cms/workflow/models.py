"""
Workflow models — re-exported from the main CMS database.

All workflow entities now live in the main database under the same
tables as their CMS counterparts:
  - WorkflowClient → Client (with added `reference` column)
  - WorkflowSubject → Subject (with added `social_accounts` column)
  - WorkflowCase → Case (with added `pv_body` and `pv_updated_at`)
  - WorkflowFinding → Finding (with added workflow-specific columns)
  - WorkflowResearchAction → ResearchAction (new table)
  - WorkflowActionFinding → ActionFinding (new junction table)
  - WorkflowScreenshot → FindingScreenshot (new table)

These aliases exist so the rest of the workflow code (routes, research)
can keep using the same class names during the migration.
"""

from cms.models import (
    Client as WorkflowClient,
    Case as WorkflowCase,
    Subject as WorkflowSubject,
    Finding as WorkflowFinding,
    ResearchAction as WorkflowResearchAction,
    ActionFinding as WorkflowActionFinding,
    FindingScreenshot as WorkflowScreenshot,
)

__all__ = [
    "WorkflowClient",
    "WorkflowCase",
    "WorkflowSubject",
    "WorkflowFinding",
    "WorkflowResearchAction",
    "WorkflowActionFinding",
    "WorkflowScreenshot",
]
