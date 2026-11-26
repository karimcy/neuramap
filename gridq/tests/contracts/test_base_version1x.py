"""`_base.Version1x` (WP-BO-06 step 2): the version a reference to another contract carries.

A reference accepts any ``1.<minor>.<patch>`` (plan §2.4, §5). A contract's own version stays an
exact literal; that is tested per contract.
"""

from __future__ import annotations

import re

import pytest
from pydantic import TypeAdapter, ValidationError

from gridq.contracts import _base
from gridq.contracts._base import VERSION_1X_PATTERN, ContractModel, Version1x

_ADAPTER: TypeAdapter[str] = TypeAdapter(Version1x)


class _Ref(ContractModel):
    """A strict contract model holding one reference version, as `EvidenceRef` does."""

    contract_version: Version1x


@pytest.mark.parametrize("value", ["1.0.0", "1.1.0", "1.10.0", "1.0.12", "1.123.456"])
def test_a_1x_version_is_accepted(value: str) -> None:
    assert _ADAPTER.validate_python(value) == value
    assert _Ref.model_validate_json(f'{{"contract_version": "{value}"}}').contract_version == value
    assert re.fullmatch(VERSION_1X_PATTERN, value)


@pytest.mark.parametrize(
    "value",
    [
        "2.0.0",  # another major
        "0.9.0",  # an earlier major
        "10.0.0",
        "1.1",  # not three parts
        "1.1.0.0",
        "1.01.0",  # leading zero
        "1.0.01",
        "1.0.0\n",  # trailing newline
        " 1.0.0",
        "1.0.0 ",
        "v1.0.0",
        "1.0.0-rc1",
        "1.1٣.0",  # a non-ASCII digit: the JSON Schema pattern would refuse it too
        "",
    ],
)
def test_anything_else_is_refused(value: str) -> None:
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(value)
    with pytest.raises(ValidationError):
        _Ref.model_validate({"contract_version": value})


def test_a_number_is_not_a_version() -> None:
    with pytest.raises(ValidationError):
        _Ref.model_validate_json('{"contract_version": 1.0}')


def test_version1x_is_exported_and_its_schema_is_the_pattern() -> None:
    assert {"Version1x", "VERSION_1X_PATTERN"} <= set(_base.__all__)
    assert _ADAPTER.json_schema() == {"type": "string", "pattern": VERSION_1X_PATTERN}
