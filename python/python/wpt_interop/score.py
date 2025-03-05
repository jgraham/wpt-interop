import csv
import gzip
import json
import logging
import os
import subprocess
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, cast


from . import _wpt_interop  # type: ignore
from .runs import RunsByDate, fetch_runs_wptfyi, group_by_date
from .metadata import get_category_data

logger = logging.getLogger("wpt_interop.score")

DEFAULT_RESULTS_CACHE_PATH = os.path.join(os.path.abspath(os.curdir), "results-analysis-cache.git")

RunScores = Mapping[str, list[int]]
InteropScore = Mapping[str, int]
ExpectedFailureScores = Mapping[str, list[tuple[int, int]]]


def is_gzip(path: str) -> bool:
    if os.path.splitext(path) == ".gz":
        return True
    try:
        # Check for magic number at the start of the file
        with open(path, "rb") as f:
            return f.read(2) == b"\x1f\x8b"
    except Exception:
        return False


def load_wptreport(path: str) -> Mapping[str, Any]:
    rv = {}
    opener = gzip.GzipFile if is_gzip(path) else open
    with opener(path) as f:  # type: ignore
        try:
            data = json.load(f)
        except Exception as e:
            raise IOError(f"Failed to read {path}") from e
    for item in data["results"]:
        result = {"status": item["status"], "subtests": []}
        for subtest in item["subtests"]:
            result["subtests"].append({"name": subtest["name"], "status": subtest["status"]})
        rv[item["test"]] = result
    return rv


def load_taskcluster_results(
    log_paths: Iterable[str],
    all_tests: set[str],
    expected_failures: Mapping[str, set[Optional[str]]],
) -> Mapping[str, Any]:
    run_results = {}
    for path in log_paths:
        log_results = load_wptreport(path)
        for test_name, results in log_results.items():
            if test_name not in all_tests:
                continue
            if results["status"] == "SKIP":
                # Sometimes we have multiple jobs which log SKIP for tests that aren't run
                continue
            if test_name in run_results:
                logger.warning(f"Got duplicate results for {test_name}")
            run_results[test_name] = results
            if test_name in expected_failures:
                if None in expected_failures[test_name]:
                    run_results[test_name]["expected"] = "FAIL"
                else:
                    for subtest_result in run_results[test_name]:
                        if subtest_result["name"] in expected_failures[test_name]:
                            subtest_result["expected"] = "FAIL"
    return run_results


def date_range(
    year: int, from_date: Optional[datetime] = None, to_date: Optional[datetime] = None
) -> tuple[datetime, datetime]:
    now = datetime.now()
    if from_date is None:
        from_date = datetime(year, 1, 1)
    elif from_date.year != year:
        raise ValueError(f"from_date {from_date} year doesn't match year {year}")
    if to_date is None:
        if now.year == year:
            to_date = datetime(year, now.month, now.day)
        else:
            to_date = datetime(year, 12, 31)
    elif to_date.year != year:
        raise ValueError(f"to_date {to_date} year doesn't match year {year}")
    return from_date, to_date


def update_results_cache(path: str) -> None:
    if not os.path.exists(path):
        os.makedirs(path)
        subprocess.run(["git", "init", "--bare"], cwd=path)
    subprocess.run(
        ["git", "fetch", "--tags", "https://github.com/web-platform-tests/results-analysis-cache"],
        cwd=path,
    )


def score_runs_by_date(
    runs_by_date: RunsByDate,
    tests_by_category: Mapping[str, set[str]],
    results_cache_path: str = DEFAULT_RESULTS_CACHE_PATH,
) -> Mapping[str, Mapping[str, Mapping[str, Any]]]:
    results_by_date: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}

    for date, date_runs in runs_by_date.items():
        results_by_date[date] = {}
        for revision_runs in date_runs:
            revision = revision_runs.revision
            logger.info(f"Scoring {date}: {revision}")
            results_by_date[date][revision] = {}

            run_ids = [item.run_id for item in revision_runs.runs]

            try:
                browser_scores, interop_scores, _ = _wpt_interop.score_runs(
                    results_cache_path, run_ids, tests_by_category, set()
                )
            except Exception:
                logger.warning(
                    f"Failed to compute score for run ids {' '.join(str(item) for item in run_ids)}"
                )
            for i, run in enumerate(revision_runs.runs):
                run_score: dict[str, Any] = {}
                for category in browser_scores.keys():
                    run_score[category] = browser_scores[category][i]
                run_score["version"] = run.browser_version

                results_by_date[date][revision][run.browser_name] = run_score
                results_by_date[date][revision]["interop"] = cast(dict[str, Any], interop_scores)

    return results_by_date


def score_wptreports(
    run_logs: Iterable[Iterable[str]],
    year: int = 2024,
    category_filter: Optional[Callable[[str], bool]] = None,
    expected_failures: Optional[Mapping[str, set[Optional[str]]]] = None,
) -> tuple[Mapping[str, list[int]], Optional[ExpectedFailureScores]]:
    """Get Interop scores from a list of paths to wptreport files

    :param runs: A list/iterable with one item per run. Each item is a
    list of wptreport files for that run.
    :param year: Integer year for which to calculate interop scores.
    :param:

    """
    include_expected_failures = expected_failures is not None
    if not include_expected_failures or expected_failures is None:
        expected_failures = {}

    tests_by_category, all_tests = get_category_data(year, category_filter=category_filter)
    runs_results = []
    for log_paths in run_logs:
        runs_results.append(load_taskcluster_results(log_paths, all_tests, expected_failures))

    expected_failure_scores: Optional[Mapping[str, list[tuple[int, int]]]]
    run_scores, _, expected_failure_scores = _wpt_interop.interop_score(
        runs_results, tests_by_category, set()
    )

    if not include_expected_failures:
        # Otherwise this will just be all zeros
        expected_failure_scores = None

    return run_scores, expected_failure_scores


def score_runs(
    year: int,
    run_ids: Iterable[str],
    results_cache_path: str = DEFAULT_RESULTS_CACHE_PATH,
    category_filter: Optional[Callable[[str], bool]] = None,
) -> tuple[RunScores, InteropScore, ExpectedFailureScores]:
    tests_by_category, all_tests = get_category_data(year, category_filter=category_filter)

    update_results_cache(results_cache_path)

    return _wpt_interop.score_runs(results_cache_path, list(run_ids), tests_by_category, set())


def score_all_runs(
    year: int,
    only_active: bool = True,
    results_cache_path: str = DEFAULT_RESULTS_CACHE_PATH,
    products: Optional[list[str]] = None,
    experimental: bool = True,
    from_date: Optional[datetime] = None,
) -> Mapping[str, Mapping[str, Mapping[str, Any]]]:
    if products is None:
        products = ["chrome", "edge", "firefox", "safari"]

    tests_by_category, all_tests = get_category_data(year)

    update_results_cache(results_cache_path)

    from_date, to_date = date_range(year, from_date=from_date)
    runs_by_revision = fetch_runs_wptfyi(
        products, "experimental" if experimental else "stable", from_date, to_date, aligned=False
    )

    return score_runs_by_date(
        group_by_date(runs_by_revision), tests_by_category, results_cache_path
    )


def score_aligned_runs(
    year: int,
    only_active: bool = True,
    results_cache_path: str = DEFAULT_RESULTS_CACHE_PATH,
    products: Optional[list[str]] = None,
    experimental: bool = True,
    max_per_day: int = 1,
) -> Mapping[str, Mapping[str, Mapping[str, Any]]]:
    if products is None:
        products = ["chrome", "edge", "firefox", "safari"]

    tests_by_category, all_tests = get_category_data(year)

    update_results_cache(results_cache_path)

    from_date, to_date = date_range(year)
    runs_by_revision = fetch_runs_wptfyi(
        products,
        "experimental" if experimental else "stable",
        from_date,
        to_date,
        aligned=True,
        max_per_day=max_per_day,
    )

    return score_runs_by_date(
        group_by_date(runs_by_revision), tests_by_category, results_cache_path
    )


def write_per_date_csv(
    year: int,
    results_cache_path: str = DEFAULT_RESULTS_CACHE_PATH,
    products: Optional[list[str]] = None,
) -> None:
    if products is None:
        products = ["chrome", "edge", "firefox", "safari"]

    product_keys = products + ["interop"]

    tests_by_category, _ = get_category_data(year, only_active=False)
    categories = list(tests_by_category.keys())

    update_results_cache(results_cache_path)

    from_date, to_date = date_range(year)

    for experimental, label in [(True, "experimental"), (False, "stable")]:
        filename = f"interop-{year}-{label}-v2.csv"
        with open(filename, "w") as f:
            writer = csv.writer(f)

            headers = ["date"]
            for product in product_keys:
                if product != "interop":
                    headers.append(f"{product}-version")
                for category in categories:
                    headers.append(f"{product}-{category}")

            writer.writerow(headers)

            runs_by_revision = fetch_runs_wptfyi(
                products,
                "experimental" if experimental else "stable",
                from_date,
                to_date,
                aligned=True,
                max_per_day=1,
            )
            results_by_date = score_runs_by_date(
                group_by_date(runs_by_revision), tests_by_category, results_cache_path
            )

            for date, revision_data in sorted(results_by_date.items(), key=lambda item: item[0]):
                # We expect to select a single revision per day
                assert len(revision_data) == 1
                results = revision_data[list(revision_data.keys())[0]]
                row_data = [date]
                for product in product_keys:
                    product_results = results[product]
                    if product != "interop":
                        row_data.append(product_results["version"])
                    for category in categories:
                        row_data.append(product_results[category])
                writer.writerow(row_data)
