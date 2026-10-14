"""Session setup shared by the whole suite.

The sample invoices are generated and gitignored, so they do not exist on a fresh clone. Six
tests in `test_error_taxonomy.py` failed with `FileNotFoundError` for that reason on
2026-09-16, reported by JJ after merging `main` into his branch.

The scripts now self-heal through `samples_fixture.ensure_samples()`, so this fixture is not
strictly required. It is here anyway so that the generation happens once, at a named point,
rather than as a side effect of whichever test happens to run first. A test that fails because
a fixture deep in an import chain did not fire is much harder to read than one that fails here.
"""

import os
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "evaluation"))

from samples_fixture import ensure_samples  # noqa: E402
from storage import DEFAULT_DB_PATH, StorageManager, connect  # noqa: E402

# UI tests must never depend on, or mutate, a developer's working database.  This setup runs
# before pytest imports the test modules because several of them import app.py at collection
# time.  A normal fixture would run too late to redirect those imports.
_UI_DB_DIRECTORY = tempfile.TemporaryDirectory(prefix="invoice-ui-tests-")
UI_TEST_DB_PATH = os.path.join(_UI_DB_DIRECTORY.name, DEFAULT_DB_PATH)
os.environ["WORKFLOW_DB_PATH"] = UI_TEST_DB_PATH
#
# The dataset is deliberately small but covers the states rendered by the dashboard: one
# pending document with no stored line items, one system-approved document, a covering email,
# and a pending outbound notification.  Every run therefore starts from the same state.
ensure_samples()
_ui_store = StorageManager(UI_TEST_DB_PATH)
_run_id = _ui_store.start_run("ui-test-model", 0.80)
_email_id = _ui_store.record_email(
    "<ui-test-invoice@example.test>",
    "accounts@example.test",
    subject="Invoice INV-2026-001",
    received_at="2026-09-15 02:00:00",
    attachment_count=1,
    body_text="Please review the attached invoice.",
    body_source="mock",
    attachment_names=["sample_invoice_1_INV-2026-001.pdf"],
)["email_id"]

_pending = _ui_store.save_invoice(
    _run_id,
    "sample_invoice_1_INV-2026-001.pdf",
    "ui-source-pending",
    "ui-content-pending",
    {
        "invoice_number": "INV-2026-001",
        "vendor_name": "Northwind Services",
        "date": "2026-09-15",
        "total_amount": 1500.00,
        "currency": "AUD",
        "items": [],
    },
    0.76,
    "Validated",
    os.path.join(REPO_ROOT, "evaluation", "samples", "sample_invoice_1_INV-2026-001.pdf"),
    None,
    email_id=_email_id,
    raw_text="TAX INVOICE\nInvoice INV-2026-001\nTotal AUD 1,500.00",
    category="Invoice",
    summary="Three service charges requiring approval.",
)

_approved = _ui_store.save_invoice(
    _run_id,
    "sample_invoice_2_INV-2026-002.pdf",
    "ui-source-approved",
    "ui-content-approved",
    {
        "invoice_number": "INV-2026-002",
        "vendor_name": "Contoso Supplies",
        "date": "2026-09-16",
        "total_amount": 2650.00,
        "currency": "AUD",
        "items": [
            {"description": "Equipment", "quantity": 1, "unit_price": 2000.00,
             "line_total": 2000.00},
            {"description": "Support", "quantity": 1, "unit_price": 650.00,
             "line_total": 650.00},
        ],
    },
    0.96,
    "Validated",
    os.path.join(REPO_ROOT, "evaluation", "samples", "sample_invoice_2_INV-2026-002.pdf"),
    None,
    raw_text="TAX INVOICE\nInvoice INV-2026-002\nTotal AUD 2,650.00",
    category="Invoice",
    summary="Equipment and support invoice.",
)

with connect(UI_TEST_DB_PATH) as _conn:
    _conn.execute(
        "UPDATE invoices SET approval_status = 'Approved' WHERE invoice_id = ?",
        (_approved["invoice_id"],),
    )
    _conn.commit()

_ui_store.queue_outbound(
    _pending["invoice_id"],
    "Jira",
    '{"summary":"Review INV-2026-001"}',
)
_ui_store.finish_run(_run_id, 2)


@pytest.fixture(scope="session", autouse=True)
def sample_invoices():
    """Guarantee the gitignored sample PDFs exist before any test runs.

    Idempotent and cheap: with the files already present it lists a directory and returns.

    Deliberately generates into the real `evaluation/samples/` rather than a `tmp_path`, which
    is a departure from `sample-fixture-spec.md`. The scripts under test resolve that directory
    from a module-level constant, so redirecting it would need monkeypatched globals and the
    tests would then exercise a configuration that never runs in production. The directory is
    already gitignored, so writing generated files into it dirties nothing.
    """
    return ensure_samples()
