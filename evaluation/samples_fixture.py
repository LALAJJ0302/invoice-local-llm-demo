"""The one place that guarantees `evaluation/samples/` has its PDFs.

    from samples_fixture import SAMPLES_DIR, ensure_samples
    ensure_samples()

**Why this file exists.** The sample invoices are generated, and `.gitignore` ignores `*.pdf`,
so they have never been tracked and do not exist on a fresh clone. Every script that reads
them has to generate them first if they are missing.

Three scripts did that with their own private copy of an `ensure_samples()` function. Four
scripts did not, because there was nothing to remind them, and the omission was invisible
until JJ cloned the repository on 2026-09-16 and six tests failed with `FileNotFoundError`.

The duplication was the defect, not the four omissions. A helper copied into three files
cannot be added to the fourth by anyone who does not already know it exists. So there is now
exactly one, and `tests/test_samples_fixture.py` fails if a script reads the directory without
calling it.

**Why the PDFs are not simply committed.** ReportLab writes a random `/ID` into every file it
produces, so regenerating an identical invoice changes its bytes. Tracking them would make git
report an unchanged document as modified on every run, which is the same problem that keeps
`Enterprise_AI_Workflow_Briefing.docx` out of the tree. It is also the reason the deduplication
key in `storage.py` hashes extracted text rather than file bytes.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(REPO_ROOT, "evaluation")
SAMPLES_DIR = os.path.join(EVAL_DIR, "samples")

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import generate_mock_invoices  # noqa: E402

# The file every caller depends on. Named here rather than spelled out at each call site,
# because a typo in a path produces the same FileNotFoundError this module exists to prevent.
FIRST_SAMPLE = "sample_invoice_1_INV-2026-001.pdf"


def ensure_samples(target_dir: str = SAMPLES_DIR, quiet: bool = True) -> str:
    """Generate the sample invoices into `target_dir` if no PDF is there yet.

    Idempotent: with the PDFs already present this does nothing and touches no file, so it is
    safe to call at the top of every script and once per test session. Returns the directory,
    so a caller can use it inline.
    """
    existing = os.listdir(target_dir) if os.path.isdir(target_dir) else []
    if not any(name.endswith(".pdf") for name in existing):
        if not quiet:
            print(f"[setup] Generating sample invoices into {target_dir}")
        generate_mock_invoices.generate_all_mock_invoices(target_dir=target_dir)
    return target_dir


def sample_path(file_name: str = FIRST_SAMPLE, target_dir: str = SAMPLES_DIR) -> str:
    """Absolute path to one sample, generating the set first if it is missing."""
    return os.path.join(ensure_samples(target_dir), file_name)
