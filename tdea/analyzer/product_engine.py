"""ProductEngine: the analyzer's face to the aggregator for product analyses (sprint 23 Oct).

The aggregator may import only the analyzer (NFR-14), so this facade carries the
git_handler steps a product analysis needs: the FR-02 URL check, the FR-03 accessibility
check with the optional token (FR-04), and one run that clones the repository (FR-06),
reads its history (FR-07), samples it (FR-08) and analyses each selected commit
(ProductAnalyzer, FR-09 to FR-18). The clone is deleted when the run ends, whether it
succeeds or fails (FR-10).

A run in which no analysed commit holds a measurable .py file ends with ProductError
"not_python" (NFR-07: a non-Python repository is an error, not an empty result).

Every git_handler failure leaves this module as ProductError, with the three FR-05
parts and the failure kind, so callers never see a raw exception. The token is a
parameter of check() alone: it reaches GitHubClient's Authorization header for that one
request and is never stored or passed on (FR-04, NFR-19); clones are anonymous.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tdea.analyzer.product_analyzer import CommitAnalysis, Progress, ProductAnalyzer
from tdea.git_handler.commit_sampler import DEFAULT_N, check_n, sample
from tdea.git_handler.errors import TdeaError
from tdea.git_handler.github_client import GitHubClient
from tdea.git_handler.local_repository import LocalRepository
from tdea.git_handler.url_validator import RepositoryRef, UrlValidator


class ProductError(Exception):
    """A product-analysis failure in three parts (FR-05) and its kind."""

    def __init__(self, kind: str, error_type: str, cause: str, action: str) -> None:
        """Keep the kind and the three parts."""
        super().__init__(f"{error_type}: {cause} {action}")
        self.kind = kind
        self.error_type = error_type
        self.cause = cause
        self.action = action

    @classmethod
    def from_tdea(cls, error: TdeaError) -> ProductError:
        """Return the ProductError for a git_handler failure."""
        return cls(error.code.value, error.error_type, error.cause, error.action)


@dataclass(frozen=True)
class Repo:
    """A repository named by a valid URL: owner, name and the result-cache key."""

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        """Return owner/name as given."""
        return f"{self.owner}/{self.name}"

    @property
    def key(self) -> str:
        """Return the case-insensitive key of the repository (FR-22)."""
        return self.full_name.lower()

    @property
    def url(self) -> str:
        """Return the repository's canonical HTTPS address."""
        return f"https://github.com/{self.owner}/{self.name}"


@dataclass(frozen=True)
class ProductRun:
    """What one run produced: the history size and each analysed commit."""

    commits_total: int
    analyses: tuple[CommitAnalysis, ...]


class ProductEngine:
    """Validation, accessibility check and the clone-and-analyse run."""

    def __init__(self, client: GitHubClient | None = None,
                 analyzer: ProductAnalyzer | None = None,
                 clone_source: Callable[[Repo], Path | None] | None = None,
                 temp_root: Path | None = None) -> None:
        """Use ``client`` for FR-03 and ``analyzer`` for the pipeline.

        ``clone_source`` may map a repository to a local path to clone instead of GitHub;
        tests use it so that no test reaches the network.
        """
        self._client = client if client is not None else GitHubClient()
        self._analyzer = analyzer if analyzer is not None else ProductAnalyzer()
        self._clone_source = clone_source
        self._temp_root = temp_root

    @staticmethod
    def parse_url(url: object) -> Repo:
        """Return the repository a URL names, or raise ProductError (FR-02)."""
        try:
            ref = UrlValidator.parse(url)
        except TdeaError as exc:
            raise ProductError.from_tdea(exc) from None
        return Repo(ref.owner, ref.name)

    @staticmethod
    def check_sampling(sampling_n: object) -> int:
        """Return a valid sampling interval N, or raise ProductError (FR-08)."""
        try:
            return check_n(sampling_n)
        except ValueError:
            raise ProductError("invalid_sampling", "Invalid sampling interval",
                               "The sampling interval N must be a whole number of at least 1.",
                               f"Enter 1 to analyse every commit, or leave the default "
                               f"{DEFAULT_N}.") from None

    def check(self, repo: Repo, token: str | None = None) -> None:
        """Confirm the repository is public and non-empty (FR-03), or raise ProductError."""
        try:
            self._client.check_repository(RepositoryRef(repo.owner, repo.name), token)
        except TdeaError as exc:
            raise ProductError.from_tdea(exc) from None

    def run(self, repo: Repo, sampling_n: int, progress: Progress) -> ProductRun:
        """Clone, read the history, sample and analyse; the clone is always deleted."""
        source: RepositoryRef | Path = RepositoryRef(repo.owner, repo.name)
        if self._clone_source is not None:
            local = self._clone_source(repo)
            source = local if local is not None else source
        try:
            with LocalRepository(self._temp_root) as clone:
                clone.clone(source)
                history = list(reversed(clone.history()))
                if not history:
                    raise ProductError("empty_repository", "Empty repository",
                                       f"{repo.full_name} has no commits to analyse.",
                                       "Choose a repository that has at least one commit.")
                selected = sample(history, sampling_n)
                analyses = self._analyzer.analyse(clone, selected, progress)
        except TdeaError as exc:
            raise ProductError.from_tdea(exc) from None
        if not any(analysis.files for analysis in analyses):
            raise ProductError("not_python", "No Python code to analyse",
                               f"No analysed commit of {repo.full_name} holds a Python "
                               "file TDEA could measure; TDEA analyses Python (.py) files "
                               "only.", "Choose a repository that contains Python code.")
        return ProductRun(len(history), tuple(analyses))
