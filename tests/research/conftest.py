"""Shared research fixtures: FX-RG1 v1 to v4 built once per session, clones, a key.

The research key is a fixed, test-only 32-byte value so identity keys in tests are
reproducible; real runs use the KeyStore's random key. FX-RG1 v2 (Day 6) is v1 plus the
RE-10 and RE-08 cases; v1's fixtures are unchanged. FX-RG1 v3 (Day 7) is v2 plus the
RE-11 disappearance-mode cases; v1's and v2's commits are unchanged. FX-RG1 v4 (Day 8)
is v3 plus the RE-13 covariate cases; v1's to v3's commits are unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator

import make_fx_rg1
import make_fx_rg1_v2
import make_fx_rg1_v3
import make_fx_rg1_v4
import pytest
import run_setup
from fx_rg1_clone import ClonedFxRg1, ClonedFxRg1V2, clone_fx_rg1, clone_fx_rg1_v2

from tdea.analyzer.research.rg1.pseudonymiser import Pseudonymiser


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


@pytest.fixture(name="fx_rg1_v2", scope="session")
def _fx_rg1_v2(tmp_path_factory: pytest.TempPathFactory) -> make_fx_rg1_v2.FxRg1V2:
    """Build FX-RG1 v2 once for the test session."""
    return make_fx_rg1_v2.build(tmp_path_factory.mktemp("fx-v2") / "fx-rg1")


@pytest.fixture(name="cloned_fx_rg1_v2", scope="session")
def _cloned_fx_rg1_v2(
    fx_rg1_v2: make_fx_rg1_v2.FxRg1V2, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[ClonedFxRg1V2]:
    """Clone FX-RG1 v2 once and read its history up to the RE-10 cut-off."""
    with clone_fx_rg1_v2(fx_rg1_v2, tmp_path_factory.mktemp("clones-v2")) as cloned:
        yield cloned


@pytest.fixture(name="fx_rg1_v3", scope="session")
def _fx_rg1_v3(tmp_path_factory: pytest.TempPathFactory) -> make_fx_rg1_v3.FxRg1V3:
    """Build FX-RG1 v3 once for the test session."""
    return make_fx_rg1_v3.build(tmp_path_factory.mktemp("fx-v3") / "fx-rg1")


@pytest.fixture(name="fx_rg1_v4", scope="session")
def _fx_rg1_v4(tmp_path_factory: pytest.TempPathFactory) -> make_fx_rg1_v4.FxRg1V4:
    """Build FX-RG1 v4 once for the test session."""
    return make_fx_rg1_v4.build(tmp_path_factory.mktemp("fx-v4") / "fx-rg1")


@pytest.fixture(name="built", scope="session")
def _built(fx_rg1_v2: make_fx_rg1_v2.FxRg1V2,
           tmp_path_factory: pytest.TempPathFactory) -> run_setup.Built:
    """Build FX-PAIRS and warm a lint cache with one SYNTHETIC — not CL-03 run."""
    return run_setup.build(tmp_path_factory.mktemp("runs"), fx_rg1_v2)


@pytest.fixture(name="research_key", scope="session")
def _research_key() -> bytes:
    """Return the fixed test-only pseudonymisation key."""
    return bytes(range(32))


@pytest.fixture(name="pseudonymiser", scope="session")
def _pseudonymiser(research_key: bytes) -> Pseudonymiser:
    """Return a Pseudonymiser under the test key, as the aggregator would hand it down."""
    return Pseudonymiser(research_key)
