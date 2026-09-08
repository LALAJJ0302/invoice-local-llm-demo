"""Re-exports run_eval's comparison functions so experiments score identically to it.

A second implementation of `matches` would let an experiment and the main harness
disagree about what counts as correct, which is the kind of difference nobody notices
until two numbers in a report cannot both be true.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "evaluation"))
from run_eval import matches, normalise_date, normalise_text  # noqa: F401,E402
