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
from storage import DEFAULT_DB_PATH, StorageManager  # noqa: E402

# Guarantee a schema-valid database exists, for the same reason the fixture below guarantees
# the sample PDFs: both are gitignored, so neither exists on a fresh clone.
#
# This runs at import rather than as a fixture, and that is the whole point. `tests/
# test_sidebar_views.py` and `tests/test_score_block.py` do `import app` at module level to
# reach its helpers, and pytest imports test modules during **collection**, before any fixture
# has run. A fixture is therefore too late: measured on 2026-09-24, a clone with no database
# failed with `KeyError: 'id'` during collection and took the whole suite down with it, fixture
# or no fixture. conftest.py is imported before collection begins, which makes this the only
# point early enough.
#
# What went wrong without it is worth recording, because the guard in app.py looks sufficient
# and is not. `app.py` handles an empty database with `st.stop()`, which does stop a real
# Streamlit script run. Outside one it is a no-op, so an `import app` fell straight through it
# into `keep = set(df["id"])` against an untyped empty frame.
#
# `StorageManager.__init__` creates the schema when the file is absent and only asserts the
# version when it is present, so this is idempotent and costs nothing on a populated machine.
# It creates schema, never rows: a test that needs documents still fails loudly, which is
# correct, because the answer to that is to run the pipeline and not to fake the data.
StorageManager(os.path.join(REPO_ROOT, DEFAULT_DB_PATH))


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
