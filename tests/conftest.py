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

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "evaluation"))

from samples_fixture import ensure_samples  # noqa: E402


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
