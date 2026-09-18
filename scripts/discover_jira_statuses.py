# -*- coding: utf-8 -*-
"""Discover Jira workflow statuses and transitions for the configured project.

One-time Jira workflow setup (required before status routing works):

  1. Project settings -> Workflows: add two statuses, e.g.
       - To Do (Auto Approved)
       - To Do (Human Reviewed)
     Add transitions from the initial create status to each new status.
  2. Board settings -> Columns: map each board column to its matching status.
     Board column labels alone are not enough; the API sets workflow status.
  3. Run this script and copy status names or transition IDs into .env.

Usage:
    .venv/Scripts/python.exe scripts/discover_jira_statuses.py
    .venv/Scripts/python.exe scripts/discover_jira_statuses.py --issue KAN-8
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jira_client import JiraClient, JiraError  # noqa: E402


def _print_statuses(client: JiraClient) -> None:
    payload = client._request("GET", f"/rest/api/2/project/{client.project_key}/statuses")
    print(f"=== Statuses for project {client.project_key} ===")
    for issue_type in payload:
        name = issue_type.get("name", "?")
        statuses = [s.get("name") for s in issue_type.get("statuses", [])]
        print(f"  {name}: {', '.join(statuses)}")


def _print_transitions(client: JiraClient, issue_key: str) -> None:
    transitions = client.get_transitions(issue_key)
    print(f"\n=== Transitions from {issue_key} ===")
    if not transitions:
        print("  (none)")
        return
    for transition in transitions:
        to_name = (transition.get("to") or {}).get("name", "?")
        print(f"  {transition.get('id')} -> {to_name}  [{transition.get('name')}]")


def main() -> int:
    parser = argparse.ArgumentParser(description="List Jira statuses and transitions.")
    parser.add_argument(
        "--issue",
        help="Existing issue key to inspect transitions (default: create a throwaway Task)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the throwaway issue when --issue is not provided",
    )
    args = parser.parse_args()

    client = JiraClient()
    if not client.is_configured():
        print("[!] Jira is not configured. Set JIRA_ENABLED and credentials in .env.")
        return 1

    _print_statuses(client)

    issue_key = args.issue
    created = False
    if not issue_key:
        issue_key = client.create_issue(
            summary="[discover] status routing probe - safe to delete",
            description="Created by scripts/discover_jira_statuses.py",
            labels=["discover"],
        )
        created = True
        print(f"\n[*] Created throwaway issue {issue_key}")

    try:
        issue = client._request("GET", f"/rest/api/2/issue/{issue_key}?fields=status")
        status = (issue.get("fields") or {}).get("status") or {}
        print(f"\nCurrent status of {issue_key}: {status.get('name', '?')}")
        _print_transitions(client, issue_key)
    except JiraError as error:
        print(f"[!] Could not inspect {issue_key}: {error}")
        return 1
    finally:
        if created and not args.keep:
            try:
                client._request("DELETE", f"/rest/api/2/issue/{issue_key}")
                print(f"\n[*] Deleted throwaway issue {issue_key}")
            except JiraError as error:
                print(f"[!] Could not delete {issue_key}: {error}")
                print("    Delete it manually in Jira.")

    print("\nSuggested .env entries (adjust to match output above):")
    print("  JIRA_STATUS_AUTO_APPROVED=To Do (Auto Approved)")
    print("  JIRA_STATUS_HUMAN_REVIEWED=To Do (Human Reviewed)")
    print("  # or use transition IDs:")
    print("  # JIRA_TRANSITION_AUTO_APPROVED=<id>")
    print("  # JIRA_TRANSITION_HUMAN_REVIEWED=<id>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
