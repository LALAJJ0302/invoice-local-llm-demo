"""Dispatch local tasks to Jira after validation or approval.

SQLite remains the source of truth; Jira is a mirror. Failures are recorded on the outbox
row but never abort invoice processing.
"""

from __future__ import annotations

from typing import Literal, Optional

ApprovalPath = Literal["auto", "human"]

from jira_client import JiraClient, JiraError
from storage import StorageManager, connect, from_cents


def _build_summary(task_type: str, file_name: str, vendor_name: Optional[str]) -> str:
    vendor = (vendor_name or "Unknown vendor").strip()
    return f"{task_type}: {file_name} - {vendor}"


def _build_description(
    *,
    task_type: str,
    reason: str,
    invoice_id: int,
    validation_status: Optional[str],
    validation_score: Optional[float],
    document_type: Optional[str],
    total_cents: Optional[int],
) -> str:
    total = from_cents(total_cents)
    total_line = "no total" if total is None else f"{total:,.2f}"
    score_line = "n/a" if validation_score is None else f"{validation_score:.2f}"
    lines = [
        f"Task type: {task_type}",
        f"Invoice ID: {invoice_id}",
        f"Data quality: {validation_status or 'unknown'} (score {score_line})",
        f"Document type: {document_type or 'Unknown'}",
        f"Total: {total_line}",
        "",
        reason or "No reason recorded.",
    ]
    return "\n".join(lines)


def _apply_approval_status(
    client: JiraClient,
    issue_key: str,
    approval_path: ApprovalPath,
) -> None:
    """Transition a newly created issue into the auto or human-reviewed column."""
    try:
        destination = client.transition_for_approval_path(issue_key, approval_path)
        if destination:
            print(f"  [Jira] Moved {issue_key} to {destination}")
    except JiraError as error:
        print(f"  [Jira] Created {issue_key} but could not move to approval column: {error}")


def dispatch_task_to_jira(
    store: StorageManager,
    task_id: int,
    invoice_id: int,
    task_type: str,
    reason: str,
    *,
    assignee: Optional[str] = None,
    approval_path: ApprovalPath = "human",
    jira: Optional[JiraClient] = None,
) -> None:
    """Queue a Jira dispatch and create the issue when configured."""
    task = store.task_by_id(task_id)
    if task and task["external_ref"]:
        return

    invoice = store.invoice_summary(invoice_id)
    if not invoice:
        return

    file_name = invoice["file_name"] or f"invoice-{invoice_id}"
    summary = _build_summary(task_type, file_name, invoice["vendor_name"])
    description = _build_description(
        task_type=task_type,
        reason=reason,
        invoice_id=invoice_id,
        validation_status=invoice["validation_status"],
        validation_score=invoice["validation_score"],
        document_type=invoice["document_type"],
        total_cents=invoice["total_cents"],
    )
    payload = f"{summary}. {reason or ''}".strip()
    with connect(store.db_path) as conn:
        existing = conn.execute(
            "SELECT outbox_id FROM outbound_messages WHERE task_id = ? AND channel = 'Jira' "
            "AND state IN ('Pending', 'Failed') ORDER BY outbox_id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    outbox_id = int(existing["outbox_id"]) if existing else store.queue_outbound(
        invoice_id, "Jira", payload, task_id=task_id
    )

    client = jira or JiraClient()
    if not client.is_configured():
        print(f"  [Jira] Skipped task #{task_id}: Jira not configured (check JIRA_ENABLED and .env)")
        return

    assignee_id = client.resolve_assignee(assignee)
    path_label = "auto-approved" if approval_path == "auto" else "human-reviewed"
    try:
        issue_key = client.create_issue(
            summary=summary,
            description=description,
            labels=["invoice", task_type.lower(), path_label],
            assignee_account_id=assignee_id,
        )
    except JiraError as error:
        store.mark_outbound_failed(outbox_id, str(error))
        print(f"  [Jira] Failed for task #{task_id}: {error}")
        return

    _apply_approval_status(client, issue_key, approval_path)

    store.set_task_external_ref(task_id, issue_key)
    store.mark_outbound_sent(outbox_id, issue_key)
    print(f"  [Jira] Created {issue_key} for task #{task_id}")


def sync_jira_assignee(
    store: StorageManager,
    task_id: int,
    assignee_name: Optional[str],
    *,
    jira: Optional[JiraClient] = None,
) -> None:
    """Push a Streamlit assignee change to Jira when the task has an external ref."""
    task = store.task_by_id(task_id)
    if not task or not task["external_ref"]:
        return

    client = jira or JiraClient()
    if not client.is_configured():
        return

    assignee_id = client.resolve_assignee(assignee_name)
    try:
        client.assign_issue(task["external_ref"], assignee_id)
    except JiraError as error:
        print(f"  [Warn] Jira assignee sync failed for {task['external_ref']}: {error}")
