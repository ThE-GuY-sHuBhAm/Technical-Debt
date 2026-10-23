"""Test support for product analyses on local fixtures (sprint 23 Oct).

StubClient stands in for the FR-03 GitHub check: it records the tokens it is given and
raises a chosen TdeaError, so no test reaches the network. local_service() builds an
AnalysisService whose engine clones a local fixture repository for each placeholder
github.com URL; analysed() submits a URL and waits for the background analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tdea.aggregator.analysis_service import AnalysisService, Submission
from tdea.aggregator.product_store import ProductStore
from tdea.analyzer.product_engine import ProductEngine
from tdea.git_handler.errors import TdeaError

FX_S_URL = "https://github.com/tdea-fixtures/fx-s"
FX_ERR_URL = "https://github.com/tdea-fixtures/fx-err"


@dataclass
class StubClient:
    """Not FR-03: accepts every repository unless a failure is set; records tokens."""

    tokens: list[str | None] = field(default_factory=list)
    failure: TdeaError | None = None

    def check_repository(self, _repository: object, token: str | None = None) -> None:
        """Record the token; raise the failure if one is set."""
        self.tokens.append(token)
        if self.failure is not None:
            raise self.failure


def local_service(root: Path, sources: dict[str, Path],
                  client: StubClient | None = None) -> AnalysisService:
    """Return a service over ``root``/tdea.db that clones ``sources`` by owner/name key."""
    root.mkdir(parents=True, exist_ok=True)
    engine = ProductEngine(client=client if client is not None else StubClient(),
                           clone_source=lambda repo: sources[repo.key],
                           temp_root=root)
    return AnalysisService(ProductStore(root / "tdea.db"), engine,
                           lock_directory=root / "lock")


def analysed(service: AnalysisService, url: str, **options: object) -> Submission:
    """Submit ``url`` and wait for its background analysis to end."""
    submission = service.submit(url, **options)
    assert service.wait(submission.repository_id, timeout=300)
    return submission
