import argparse
import csv
import logging
import sys
from datetime import datetime, timedelta

from . import _wpt_interop
from .repo import ResultsAnalysisCache, Metadata
from .runs import fetch_runs_wptfyi


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "warninfo", "debug"],
        help="Logging level",
    )
    parser.add_argument("--pdb", action="store_true", help="Drop into pdb on exception")
    parser.add_argument("--repo-root", default=None, help="Base path for working repos")
    parser.add_argument(
        "--results-analysis-cache", default=None, help="Path to results-analysis-cache repo"
    )
    parser.add_argument("--metadata", default=None, help="Path to metadata repo")
    parser.add_argument(
        "base_browser", action="store", type=str, help="Base browser product to use for comparison"
    )
    parser.add_argument("browser", action="store", type=str, help="Browser product to compare")
    return parser


def main() -> None:
    parser = get_parser()
    args = parser.parse_args()
    try:
        run(args)
    except Exception:
        if args.pdb:
            import traceback

            traceback.print_exc()
            import pdb

            pdb.post_mortem()
        else:
            raise


def run(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.getLevelNamesMapping()[args.log_level.upper()])
    logging.getLogger("wpt_interop").setLevel(logging.INFO)

    results_analysis_repo = ResultsAnalysisCache(args.results_analysis_cache, args.repo_root)
    metadata_repo = Metadata(args.metadata, args.repo_root)

    for repo in [results_analysis_repo, metadata_repo]:
        repo.update()

    now = datetime.now()
    from_date = datetime(now.year, now.month, now.day) - timedelta(days=7)
    browser_names = [args.base_browser, args.browser]
    runs = fetch_runs_wptfyi(browser_names, "experimental", from_date=from_date, aligned=True)
    if not runs:
        print("No aligned runs found in the last 7 days")
        return

    latest_rev_runs = list(runs)[-1]
    by_browser_name = {item.browser_name: item for item in latest_rev_runs}

    regressions = _wpt_interop.regressions(
        results_analysis_repo.path,
        metadata_repo.path,
        (by_browser_name[browser_names[0]].run_id, by_browser_name[browser_names[1]].run_id),
    )

    result_header = f"{browser_names[1]} Result"
    writer = csv.DictWriter(sys.stdout, ["Test", "Subtest", result_header, "Labels"])
    writer.writeheader()
    for test, results in sorted(regressions.items()):
        test_result, subtest_results, labels = results
        writer.writerow(
            {
                "Test": test,
                "Subtest": "",
                result_header: test_result if test_result is not None else "",
                "Labels": ",".join(labels),
            }
        )
        for subtest, new_result in sorted(subtest_results):
            writer.writerow({"Test": "", "Subtest": subtest, result_header: new_result})


if __name__ == "__main__":
    main()
