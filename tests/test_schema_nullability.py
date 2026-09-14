"""Tests for the nullable-required schema.

The whole proposal rests on one piece of Pydantic behaviour: a field declared `Optional[T]`
with NO default is placed in `required` even though its type admits null. If that is not true,
the variant is just `Optional` with extra words and every measurement built on it is void.

That behaviour is not obvious, it is easy to break by adding `= None` back while tidying, and
it is invisible in a diff to anyone who does not already know the rule. So it is asserted here
rather than trusted, and it is asserted against the emitted JSON Schema, because the JSON
Schema is what the constrained decoder actually receives.

See nullable-required-schema-spec.md.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "evaluation"))

from sentinel_comparison import (  # noqa: E402
    NullableRequiredInvoice,
    RequiredInvoice,
)

SCALARS = ["invoice_number", "vendor_name", "date", "total_amount", "currency"]


@pytest.fixture(scope="module")
def schema():
    return NullableRequiredInvoice.model_json_schema()


@pytest.mark.parametrize("field", SCALARS)
def test_every_scalar_is_required(schema, field):
    """The decoder must emit the key. This is the 2026-08-26 omission defect."""
    assert field in schema["required"]


@pytest.mark.parametrize("field", SCALARS)
def test_every_scalar_admits_null(schema, field):
    """The decoder may emit null. This is the group's rule 3: never forced to invent."""
    branches = schema["properties"][field].get("anyOf")
    assert branches is not None, f"{field} is not a union, so it cannot express null"
    assert {"type": "null"} in branches


def test_adding_a_default_would_break_the_required_list():
    """Guards the failure mode: `= None` put back while tidying silently empties `required`.

    This is not hypothetical. `Optional[str] = None` is what ships in main.py today and is the
    reason the omission defect exists at all.
    """
    from typing import Optional

    from pydantic import BaseModel

    class WithDefault(BaseModel):
        invoice_number: Optional[str] = None

    assert WithDefault.model_json_schema().get("required", []) == []


def test_items_is_unchanged_from_the_required_arm():
    """The two arms must differ in exactly one variable: the nullability of the scalars.

    `items` is a nested list, and section 5.4.3 of the report measured that adding one costs
    three scalar field-values. If the arms disagreed on `items` the comparison would confound
    nullability with schema shape.
    """
    nullable = NullableRequiredInvoice.model_fields["items"]
    required = RequiredInvoice.model_fields["items"]
    assert nullable.annotation == required.annotation
    assert nullable.default == required.default


def test_null_actually_validates():
    """The JSON Schema permitting null is worth little if the model rejects it on parse."""
    record = NullableRequiredInvoice(
        invoice_number=None, vendor_name=None, date=None, total_amount=None, currency=None
    )
    assert record.invoice_number is None
    assert record.total_amount is None


def test_omitting_a_field_is_still_rejected():
    """Nullable is not optional. Absence must still be an error, or nothing was gained."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        NullableRequiredInvoice(vendor_name=None, date=None, total_amount=None, currency=None)
