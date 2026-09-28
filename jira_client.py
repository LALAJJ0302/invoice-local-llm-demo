"""Jira Cloud client for task dispatch after invoice validation.

Uses REST API v2 with plain-text descriptions. Auth is HTTP Basic (email + API token).
Configuration is read from environment variables; see .env.example.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv


class JiraError(Exception):
    """Jira API call failed."""


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _env_or(explicit: Optional[str], key: str) -> str:
    """Use explicit kwarg when provided, including empty string; else read env."""
    if explicit is not None:
        return explicit
    return os.getenv(key) or ""


def _normalise_base_url(url: str) -> str:
    """Ensure JIRA_BASE_URL includes a scheme (https://)."""
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        return ""
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"
    return cleaned


class JiraClient:
    """Thin wrapper around Jira Cloud REST API v2."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        base_url: Optional[str] = None,
        email: Optional[str] = None,
        api_token: Optional[str] = None,
        project_key: Optional[str] = None,
        default_assignee_account_id: Optional[str] = None,
        assignee_map: Optional[Dict[str, str]] = None,
        status_auto_approved: Optional[str] = None,
        status_human_reviewed: Optional[str] = None,
        transition_auto_approved: Optional[str] = None,
        transition_human_reviewed: Optional[str] = None,
    ):
        load_dotenv()
        self.enabled = enabled if enabled is not None else _truthy(os.getenv("JIRA_ENABLED"))
        self.base_url = _normalise_base_url(_env_or(base_url, "JIRA_BASE_URL"))
        self.email = _env_or(email, "JIRA_EMAIL")
        self.api_token = _env_or(api_token, "JIRA_API_TOKEN")
        self.project_key = _env_or(project_key, "JIRA_PROJECT_KEY")
        default_id = (
            default_assignee_account_id
            if default_assignee_account_id is not None
            else os.getenv("JIRA_DEFAULT_ASSIGNEE_ACCOUNT_ID") or ""
        ).strip() or None
        self.default_assignee_account_id = default_id
        if assignee_map is not None:
            self.assignee_map = assignee_map
        else:
            raw_map = os.getenv("JIRA_ASSIGNEE_MAP", "").strip()
            self.assignee_map = json.loads(raw_map) if raw_map else {}
        self.status_auto_approved = (
            _env_or(status_auto_approved, "JIRA_STATUS_AUTO_APPROVED").strip() or None
        )
        self.status_human_reviewed = (
            _env_or(status_human_reviewed, "JIRA_STATUS_HUMAN_REVIEWED").strip() or None
        )
        self.transition_auto_approved = (
            _env_or(transition_auto_approved, "JIRA_TRANSITION_AUTO_APPROVED").strip() or None
        )
        self.transition_human_reviewed = (
            _env_or(transition_human_reviewed, "JIRA_TRANSITION_HUMAN_REVIEWED").strip() or None
        )

    def is_configured(self) -> bool:
        if not self.enabled:
            return False
        return bool(self.base_url and self.email and self.api_token and self.project_key)

    def resolve_assignee(self, local_name: Optional[str]) -> Optional[str]:
        """Map a Streamlit assignee name to a Jira accountId, else the default."""
        if local_name and local_name.strip():
            mapped = self.assignee_map.get(local_name.strip())
            if mapped:
                return mapped
        return self.default_assignee_account_id

    def create_issue(
        self,
        summary: str,
        description: str,
        labels: Optional[List[str]] = None,
        assignee_account_id: Optional[str] = None,
        reporter_account_id: Optional[str] = None,
    ) -> str:
        """Create a Task issue and return its key (e.g. INV-14).

        reporter_account_id sets the Jira reporter. The API user needs Modify Reporter;
        omit it and Jira records the API user instead.
        """
        fields: Dict[str, Any] = {
            "project": {"key": self.project_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": "Task"},
            "labels": labels or ["invoice"],
        }
        if assignee_account_id:
            fields["assignee"] = {"accountId": assignee_account_id}
        if reporter_account_id:
            fields["reporter"] = {"accountId": reporter_account_id}
        payload = self._request("POST", "/rest/api/2/issue", {"fields": fields})
        key = payload.get("key")
        if not key:
            raise JiraError(f"Jira create issue response missing key: {payload!r}")
        return str(key)

    def assign_issue(self, issue_key: str, assignee_account_id: Optional[str]) -> None:
        """Assign or unassign a Jira issue."""
        if assignee_account_id:
            body = {"fields": {"assignee": {"accountId": assignee_account_id}}}
        else:
            body = {"fields": {"assignee": None}}
        self._request("PUT", f"/rest/api/2/issue/{issue_key}", body)

    def get_transitions(self, issue_key: str) -> List[Dict[str, Any]]:
        """List workflow transitions available for an issue."""
        payload = self._request("GET", f"/rest/api/2/issue/{issue_key}/transitions")
        return list(payload.get("transitions") or [])

    def transition_issue(
        self,
        issue_key: str,
        *,
        transition_id: Optional[str] = None,
        target_status_name: Optional[str] = None,
    ) -> None:
        """Move an issue to another status via a workflow transition."""
        if transition_id:
            resolved_id = str(transition_id)
        elif target_status_name:
            transitions = self.get_transitions(issue_key)
            match = next(
                (
                    transition for transition in transitions
                    if (transition.get("to") or {}).get("name") == target_status_name
                ),
                None,
            )
            if not match:
                available = [
                    f"{transition.get('id')} -> "
                    f"{(transition.get('to') or {}).get('name')} [{transition.get('name')}]"
                    for transition in transitions
                ]
                raise JiraError(
                    f"No transition to status {target_status_name!r}. Available: {available}"
                )
            resolved_id = str(match["id"])
        else:
            return

        self._request(
            "POST",
            f"/rest/api/2/issue/{issue_key}/transitions",
            {"transition": {"id": resolved_id}},
        )

    def transition_for_approval_path(self, issue_key: str, approval_path: str) -> Optional[str]:
        """Move an issue to the auto or human-reviewed column status.

        Returns the destination status name when configured and applied, else None.
        """
        if approval_path == "auto":
            transition_id = self.transition_auto_approved
            target_status = self.status_auto_approved
        else:
            transition_id = self.transition_human_reviewed
            target_status = self.status_human_reviewed

        if not transition_id and not target_status:
            return None

        if transition_id:
            self.transition_issue(issue_key, transition_id=transition_id)
        else:
            self.transition_issue(issue_key, target_status_name=target_status)
        return target_status

    def _auth_header(self) -> str:
        token = base64.b64encode(f"{self.email}:{self.api_token}".encode()).decode()
        return f"Basic {token}"

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        if not self.is_configured():
            raise JiraError("Jira is not configured")
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": self._auth_header(),
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8")
                if not raw.strip():
                    return {}
                return json.loads(raw)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise JiraError(f"Jira HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise JiraError(f"Jira request failed: {error}") from error
