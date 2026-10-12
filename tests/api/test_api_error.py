"""ApiError (FR-05, NFR-11): three required parts, wrapping lower-layer structured errors."""

from __future__ import annotations

import pytest

from tdea.aggregator.run_file import Problem
from tdea.api.api_error import ApiError


def test_three_parts_and_their_printed_lines() -> None:
    """An ApiError keeps what happened, why and what to do, and prints them as three lines."""
    error = ApiError("Invalid run file", "W is missing.", "Add w_months.", status=422)
    assert (error.error_type, error.cause, error.action, error.status) == (
        "Invalid run file", "W is missing.", "Add w_months.", 422)
    assert error.lines() == ("Error: Invalid run file", "Cause: W is missing.",
                             "Action: Add w_months.")
    assert str(error) == "Invalid run file: W is missing. Add w_months."


@pytest.mark.parametrize("parts", [("", "cause", "action"), ("type", "  ", "action"),
                                   ("type", "cause", None)])
def test_every_part_is_required(parts: tuple) -> None:
    """No message may lack a part, so none is only a technical string (NFR-11)."""
    with pytest.raises(ValueError):
        _ = ApiError(*parts)


def test_wrap_takes_the_three_parts_of_any_structured_error() -> None:
    """A run-file problem (and, by the same attributes, a TdeaError) wraps losslessly."""
    wrapped = ApiError.wrap(Problem("Invalid run file", "tau must be 0.7.", "Set tau."))
    assert wrapped.lines()[1] == "Cause: tau must be 0.7." and wrapped.status is None


def test_wrap_refuses_raw_exceptions() -> None:
    """A plain exception has no three parts and is never shown raw (FR-05)."""
    with pytest.raises(TypeError):
        ApiError.wrap(RuntimeError("Traceback (most recent call last): ..."))
        