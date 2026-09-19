"""Tests for jira_client.py."""

import json
import os
import sys
from io import BytesIO
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jira_client import JiraClient, JiraError, _normalise_base_url  # noqa: E402


@pytest.fixture
def configured_client():
    return JiraClient(
        enabled=True,
        base_url="https://example.atlassian.net",
        email="user@example.com",
        api_token="secret",
        project_key="INV",
        default_assignee_account_id="acc-default",
        assignee_map={"Luke": "acc-luke", "Neo": "acc-neo"},
    )


class TestJiraClientConfig:
    def test_normalise_base_url_adds_https_when_missing(self):
        assert _normalise_base_url("uts-ai-platform.atlassian.net") == \
            "https://uts-ai-platform.atlassian.net"
        assert _normalise_base_url("https://example.atlassian.net/") == \
            "https://example.atlassian.net"

    def test_not_configured_when_disabled(self):
        client = JiraClient(enabled=False, base_url="https://x.atlassian.net",
                            email="a@b.com", api_token="t", project_key="INV")
        assert client.is_configured() is False

    def test_not_configured_when_credentials_missing(self):
        client = JiraClient(enabled=True, base_url="", email="", api_token="", project_key="")
        assert client.is_configured() is False

    def test_resolve_assignee_prefers_mapped_name(self, configured_client):
        assert configured_client.resolve_assignee("Luke") == "acc-luke"

    def test_resolve_assignee_falls_back_to_default(self, configured_client):
        assert configured_client.resolve_assignee("Unknown") == "acc-default"
        assert configured_client.resolve_assignee(None) == "acc-default"
        assert configured_client.resolve_assignee("") == "acc-default"


class TestJiraClientHTTP:
    def test_create_issue_returns_key(self, configured_client):
        response = BytesIO(json.dumps({"key": "INV-42", "id": "10042"}).encode())
        with patch("jira_client.urlopen", return_value=_FakeResponse(response)):
            key = configured_client.create_issue(
                summary="Review: invoice.pdf",
                description="Needs review",
                labels=["invoice", "review"],
                assignee_account_id="acc-luke",
            )
        assert key == "INV-42"

    def test_create_issue_raises_on_http_error(self, configured_client):
        from urllib.error import HTTPError

        def raise_http(*_args, **_kwargs):
            raise HTTPError("url", 401, "Unauthorized", hdrs=None, fp=BytesIO(b"denied"))

        with patch("jira_client.urlopen", side_effect=raise_http):
            with pytest.raises(JiraError, match="401"):
                configured_client.create_issue("s", "d")

    def test_assign_issue_sends_put(self, configured_client):
        captured = {}

        def fake_urlopen(request, timeout=15):
            captured["method"] = request.method
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode())
            return _FakeResponse(BytesIO(b""))

        with patch("jira_client.urlopen", side_effect=fake_urlopen):
            configured_client.assign_issue("INV-7", "acc-neo")

        assert captured["method"] == "PUT"
        assert captured["url"].endswith("/rest/api/2/issue/INV-7")
        assert captured["body"] == {"fields": {"assignee": {"accountId": "acc-neo"}}}

    def test_transition_issue_uses_transition_id(self, configured_client):
        captured = {}

        def fake_urlopen(request, timeout=15):
            captured["method"] = request.method
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode())
            return _FakeResponse(BytesIO(b""))

        with patch("jira_client.urlopen", side_effect=fake_urlopen):
            configured_client.transition_issue("INV-8", transition_id="21")

        assert captured["method"] == "POST"
        assert captured["url"].endswith("/rest/api/2/issue/INV-8/transitions")
        assert captured["body"] == {"transition": {"id": "21"}}

    def test_transition_issue_resolves_status_name(self, configured_client):
        transitions = BytesIO(json.dumps({
            "transitions": [
                {"id": "11", "name": "Auto approve", "to": {"name": "To Do (Auto Approved)"}},
                {"id": "12", "name": "Human approve", "to": {"name": "To Do (Human Reviewed)"}},
            ]
        }).encode())
        captured = {}

        def fake_urlopen(request, timeout=15):
            if request.method == "GET":
                return _FakeResponse(transitions)
            captured["body"] = json.loads(request.data.decode())
            return _FakeResponse(BytesIO(b""))

        with patch("jira_client.urlopen", side_effect=fake_urlopen):
            configured_client.transition_issue(
                "INV-9", target_status_name="To Do (Human Reviewed)"
            )

        assert captured["body"] == {"transition": {"id": "12"}}

    def test_transition_for_approval_path_auto(self):
        client = JiraClient(
            enabled=True,
            base_url="https://example.atlassian.net",
            email="user@example.com",
            api_token="secret",
            project_key="KAN",
            transition_auto_approved="11",
            status_auto_approved="To Do (Auto Approved)",
        )
        with patch.object(client, "transition_issue") as transition:
            result = client.transition_for_approval_path("KAN-1", "auto")
        transition.assert_called_once_with("KAN-1", transition_id="11")
        assert result == "To Do (Auto Approved)"


class _FakeResponse:
    def __init__(self, fp):
        self._fp = fp

    def read(self):
        return self._fp.read()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
