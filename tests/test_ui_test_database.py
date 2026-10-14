"""The dashboard suite runs against deterministic data, never a developer database."""

import os

import app
from signed_in import REPO_ROOT
from storage import connect


def test_dashboard_uses_the_isolated_database_selected_by_the_suite():
    developer_db = os.path.realpath(os.path.join(REPO_ROOT, "workflow_platform.db"))
    assert os.path.realpath(app.DB_PATH) != developer_db
    assert app.DB_PATH == os.environ["WORKFLOW_DB_PATH"]


def test_isolated_database_contains_the_dashboard_scenarios():
    with connect(app.DB_PATH) as conn:
        states = {
            row["approval_status"]
            for row in conn.execute("SELECT approval_status FROM invoices")
        }
        pending_outbox = conn.execute(
            "SELECT COUNT(*) AS count FROM outbound_messages WHERE state = 'Pending'"
        ).fetchone()["count"]

    assert {"Pending", "Approved"}.issubset(states)
    assert pending_outbox >= 1
