"""Reads the mailbox, calls the email AI module, stores what it returns.

    ./.venv/bin/python email_pipeline.py                 # every email with no analysis yet
    ./.venv/bin/python email_pipeline.py --limit 3       # stop after 3
    ./.venv/bin/python email_pipeline.py --threads       # also summarise each thread
    ./.venv/bin/python email_pipeline.py --dry-run       # plan only, call no model
    ./.venv/bin/python email_pipeline.py --model qwen3:14b

The invoice half has main.py. The email half had nothing: email_listener.py fetches mail,
email_ai.py analyses a message and storage.py stores an analysis, and no file connected the
three, so the email pipeline ran only when a person typed it by hand.

This is the only file that imports both email_ai and storage. Keeping that boundary in one
place is why storage itself never imports email_ai, which would drag Ollama into the
migrations and most of the test suite.

**It adds no analysis of its own.** No retry, because email_ai already retries evidence
validation three times and reports attempt_count, and a second loop here would make that
number meaningless. No repair of model output either: the invoice side has a regex fallback,
and the report has to separate "the model" from "our code repairing the model" because of it.
The email half starts clean and should stay that way until there is a measured reason.

See email-pipeline-spec.md.
"""

import argparse
import re
import sys
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import email_ai
from storage import StorageManager, connect

# A leading Re:/Fwd:/FW: chain, however many deep, in either case, with or without spaces.
_REPLY_PREFIX = re.compile(r"^\s*(?:(?:re|fwd|fw)\s*:\s*)+", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def thread_key(subject: Optional[str]) -> str:
    """The grouping key for a subject line, with the reply chain stripped.

    **This is a guess, and the thread_source column exists so it is never reported as
    anything else.** It is wrong in two known ways. Three different vendors in the mock
    mailbox all send a message titled "Monthly statement", and this merges them into one
    thread. A subject edited mid-conversation splits one conversation into two.

    Sender is deliberately not part of the key. Adding it would fix "Monthly statement" and
    break every real thread, because a reply comes from a different address than the message
    it answers. retrieval_eval.py already measured that shape of trade: keyword scores 1.00 on
    thread queries and 0.00 on vendor queries, and one rule cannot satisfy both.

    The correct key is the RFC 5322 In-Reply-To and References chain, which needs intake to
    capture it. That is email_listener.py, which is Luke's.
    """
    text = subject or ""
    text = _REPLY_PREFIX.sub("", text)
    return _WHITESPACE.sub(" ", text).strip().casefold()


def has_reply_prefix(subject: Optional[str]) -> bool:
    """Whether a subject announces itself as a reply or a forward."""
    return bool(_REPLY_PREFIX.match(subject or ""))


def group_by_subject(rows: Iterable[Dict[str, Any]]) -> "OrderedDict[str, List[Dict[str, Any]]]":
    """Groups email rows into threads, oldest first within each, insertion order preserved."""
    threads: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for row in rows:
        threads.setdefault(thread_key(row["subject"]), []).append(row)
    return threads


def assign_threads(store: StorageManager) -> Dict[str, int]:
    """Writes thread_id and thread_source over the whole mailbox.

    Idempotent: recomputes from subjects every time, so a message whose subject was corrected
    moves to the right thread on the next run. Only rows whose thread came from a subject are
    touched, so a thread_id written from real headers later is never overwritten by a guess.
    """
    with connect(store.db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT email_id, subject, thread_source FROM email_messages ORDER BY email_id")]

        written = 0
        for key, members in group_by_subject(
                [r for r in rows if r["thread_source"] in (None, "subject")]).items():
            for member in members:
                conn.execute(
                    "UPDATE email_messages SET thread_id = ?, thread_source = 'subject' "
                    "WHERE email_id = ?",
                    (key, member["email_id"]),
                )
                written += 1
        conn.commit()

        threads = conn.execute(
            "SELECT COUNT(DISTINCT thread_id) FROM email_messages "
            "WHERE thread_id IS NOT NULL").fetchone()[0]
        grouped = [dict(r) for r in conn.execute(
            "SELECT thread_id, subject FROM email_messages WHERE thread_id IS NOT NULL")]

    # A thread where no message announces itself as a reply, yet holds several messages, is
    # several originals that happen to share a subject. That is the collision.
    #
    # **The obvious metric is wrong and was written first.** "More than one sender in a
    # thread" flags every genuine conversation, because a reply comes from a different address
    # than the message it answers. Counting senders would have reported real threads as
    # defects and the collision would have been hidden inside that number.
    #
    # This one has a false positive of its own: somebody who replies after deleting the "Re:"
    # is counted as a collision. Subject matching cannot tell those apart, which is the
    # argument for capturing In-Reply-To at intake rather than a better heuristic here.
    by_thread: Dict[str, List[Optional[str]]] = {}
    for row in grouped:
        by_thread.setdefault(row["thread_id"], []).append(row["subject"])
    collisions = sum(
        1 for subjects in by_thread.values()
        if len(subjects) > 1 and not any(has_reply_prefix(s) for s in subjects)
    )

    return {"emails": written, "threads": threads, "collision_threads": collisions}


def to_input(row: Dict[str, Any]) -> "email_ai.EmailMessageInput":
    return email_ai.EmailMessageInput(
        message_id=row["message_id"],
        sent_at=row["received_at"],
        subject=row["subject"] or "",
        sender=row["sender"],
        body=row["body_text"],
        attachments=[],
    )


def analysable_emails(store: StorageManager, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Emails that can be analysed: they have a body. Ones without are skipped, not failed.

    An email with no body_text is not an error. seed_mock_emails.py fills the mock mailbox and
    real intake will fill the rest, but until FR-1.3 lands a row can exist with headers only.
    """
    sql = ("SELECT email_id, message_id, sender, subject, received_at, body_text, thread_id "
           "FROM email_messages WHERE body_text IS NOT NULL AND TRIM(body_text) != '' "
           "ORDER BY email_id")
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    with connect(store.db_path) as conn:
        return [dict(r) for r in conn.execute(sql)]


def run(db_path: str = "workflow_platform.db", model: str = email_ai.MODEL_NAME,
        limit: Optional[int] = None, threads: bool = False,
        dry_run: bool = False) -> Tuple[int, Dict[str, Any]]:
    store = StorageManager(db_path)

    print(f"=== Email analysis over {db_path} ===")
    grouping = assign_threads(store)
    print(f"[*] {grouping['emails']} emails grouped into {grouping['threads']} threads by "
          f"subject, {grouping['collision_threads']} of them a probable collision")
    if grouping["collision_threads"]:
        print("[!] A thread whose messages never say 'Re:' is several originals sharing a "
              "subject, not a conversation. thread_source says 'subject' for this reason.")

    rows = analysable_emails(store, limit)
    print(f"[*] {len(rows)} emails with a body to analyse, model {model}")

    if dry_run:
        for row in rows:
            print(f"    would analyse {row['message_id']}  {(row['subject'] or '')[:50]}")
        if threads:
            for key, members in group_by_subject(rows).items():
                print(f"    would summarise thread {key!r} over {len(members)} messages")
        print("\nDry run only. No model called, nothing written.")
        return 0, {"planned": len(rows)}

    if not rows:
        print("[*] Nothing to analyse.")
        return 0, {"analysed": 0}

    run_id = store.start_email_run(model)
    print(f"[*] run {run_id} opened\n")

    summary = {"analysed": 0, "failed": 0, "validated": 0, "needs_review": 0,
               "actions": 0, "undated": 0, "threads": 0, "thread_failed": 0}

    for row in rows:
        try:
            outcome = email_ai.analyse_email(to_input(row))
            record = email_ai.create_email_analysis_record(
                to_input(row), outcome, run_id=run_id)
            result = store.save_email_analysis(record.model_dump(mode="json"))
        except Exception as error:                      # noqa: BLE001
            # One malformed message must not end a run over twenty. It is named and counted,
            # because a failure nobody sees is worse than one that is reported.
            summary["failed"] += 1
            print(f"[!] {row['message_id']}: {type(error).__name__}: {error}")
            continue

        summary["analysed"] += 1
        summary["actions"] += result["action_item_count"]
        summary["undated"] += result["undated_count"]
        key = "validated" if outcome.validation_status == "Validated" else "needs_review"
        summary[key] += 1
        print(f"    {(row['subject'] or '')[:42]:<42} {outcome.analysis.category:<15} "
              f"{outcome.validation_status:<12} attempts={outcome.attempt_count} "
              f"actions={result['action_item_count']}")

    if threads:
        print()
        for key, members in group_by_subject(rows).items():
            try:
                thread = email_ai.EmailThreadInput(
                    thread_id=key, messages=[to_input(m) for m in members])
                outcome = email_ai.summarise_thread(thread)
                record = email_ai.create_thread_analysis_record(
                    thread, outcome, run_id=run_id)
                result = store.save_thread_analysis(
                    record.model_dump(mode="json"), thread_source="subject")
            except Exception as error:                  # noqa: BLE001
                summary["thread_failed"] += 1
                print(f"[!] thread {key!r}: {type(error).__name__}: {error}")
                continue

            summary["threads"] += 1
            print(f"    thread {key[:36]:<36} {len(members)} msgs  "
                  f"{outcome.validation_status:<12} decisions={result['decision_count']} "
                  f"actions={result['action_item_count']}")

    store.finish_run(run_id, summary["analysed"])

    print("\n" + "=" * 62)
    print(f"  analysed          {summary['analysed']}, failed {summary['failed']}")
    print(f"  validated         {summary['validated']}, needs review {summary['needs_review']}")
    print(f"  action items      {summary['actions']}, of which undated {summary['undated']}")
    if threads:
        print(f"  threads           {summary['threads']}, failed {summary['thread_failed']}")
    print(f"  run               {run_id}, model {model}")
    print("=" * 62)
    print("Every thread above was grouped by subject, not by a reply chain. Nothing here is")
    print("scored: there is no ground truth for classification or summarisation yet.")

    return (1 if summary["failed"] else 0), summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyse the stored mailbox with the local model.")
    parser.add_argument("--db", default="workflow_platform.db")
    parser.add_argument("--model", default=email_ai.MODEL_NAME)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--threads", action="store_true", help="also summarise each thread")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    code, _ = run(db_path=args.db, model=args.model, limit=args.limit,
                  threads=args.threads, dry_run=args.dry_run)
    sys.exit(code)
