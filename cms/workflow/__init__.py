from flask import Blueprint

workflow_bp = Blueprint(
    "workflow",
    __name__,
    template_folder="../../templates/cms/workflow",
    url_prefix="/cms/workflow",
)

from . import models, routes  # noqa: E402, F811
