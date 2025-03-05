from datetime import datetime
from typing import Mapping, Optional

Json = None | int | float | str | bool | list["Json"] | dict[str, "Json"]
RunScores = Mapping[str, list[int]]
InteropScore = Mapping[str, int]
ExpectedFailureScores = Mapping[str, list[tuple[int, int]]]

class Results:
    status: str
    subtests: list[SubtestResult]
    expected: Optional[str]

class SubtestResult:
    name: str
    status: str
    expected: Optional[str]

class GeckoRun:
    id: str
    run_info: Mapping[str, Json]

class GeckoRuns:
    push_date: datetime
    runs: Mapping[str, GeckoRun]

def interop_score(
    runs: list[Mapping[str, Results]], tests: Mapping[str, set[str]], expected_not_ok: set[str]
) -> tuple[RunScores, InteropScore, ExpectedFailureScores]: ...
def run_results(
    results_repo: str, run_ids: list[str], tests: set[str]
) -> list[Mapping[str, Results]]: ...
def score_runs(
    results_repo: str,
    run_ids: list[str],
    tests_by_category: Mapping[str, set[str]],
    expected_not_ok: set[str],
) -> tuple[RunScores, InteropScore, ExpectedFailureScores]: ...
def interop_tests(
    metadata_repo_path: str,
    labels_by_category: Mapping[str, set[str]],
    metadata_revision: Optional[str],
) -> tuple[str, Mapping[str, set[str]], set[str]]: ...
def regressions(
    results_repo: str, metadata_repo_path: str, run_ids: tuple[str, str]
) -> Mapping[str, tuple[Optional[str], list[tuple[str, str]], list[str]]]: ...
def gecko_runs(
    results_repo: str, branch: str, from_date: datetime, to_date: Optional[datetime]
) -> Mapping[datetime, Mapping[str, GeckoRuns]]: ...
