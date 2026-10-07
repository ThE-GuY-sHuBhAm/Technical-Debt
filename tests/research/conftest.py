"""Shared research fixtures: FX-RG1 built once per session and one clone of it."""

from __future__ import annotations

from collections.abc import Iterator

import make_fx_rg1
import pytest
from fx_rg1_clone import ClonedFxRg1, clone_fx_rg1


@pytest.fixture(name="fx_rg1", scope="session")
def _fx_rg1(tmp_path_factory: pytest.TempPathFactory) -> make_fx_rg1.FxRg1:
    """Build FX-RG1 once for the test session."""
    return make_fx_rg1.build(tmp_path_factory.mktemp("fx") / "fx-rg1")


@pytest.fixture(name="cloned_fx_rg1", scope="session")
def _cloned_fx_rg1(
    fx_rg1: make_fx_rg1.FxRg1, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[ClonedFxRg1]:
    """Clone FX-RG1 once and read its history up to the clone time."""
    with clone_fx_rg1(fx_rg1, tmp_path_factory.mktemp("clones")) as cloned:
        yield cloned
