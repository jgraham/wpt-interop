import argparse
import csv
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any, Iterable, Iterator, Mapping, MutableSequence, Optional, Self, cast

from . import _wpt_interop
from . import metadata
from .runs import (
    RevisionRuns,
    Run,
    WptFyiRun,
    GeckoRun,
    RunCacheData,
    RunsByRevision,
    fetch_runs_gecko,
    fetch_runs_wptfyi,
)
from .repo import (
    Repo,
    ResultsAnalysisCache,
    GeckoResultsAnalysisCache,
    WptResultsAnalysisCache,
    Metadata,
)

InteropScores = Mapping[str, int]
ScoresByCategory = Mapping[str, list[int]]

logger = logging.getLogger("wpt_interop.main")


@dataclass
class Configuration:
    platform: str
    channel: str
    products: list[str]
    source: str = "wpt"


class Interop:
    year: int
    configurations: list[Configuration]
    end_date: datetime
    _category_data: Optional[Mapping[str, Mapping[str, Any]]] = None
    _interop_data: Optional[Mapping[str, Mapping[str, Any]]] = None

    def __init__(self, wpt_fyi: Optional[str] = None) -> None:
        self.wpt_fyi = wpt_fyi
        self._categories: Optional[Mapping[str, set[str]]] = None

    def _ensure_data(self) -> None:
        if Interop._category_data is None:
            Interop._category_data = metadata.fetch_category_data(self.wpt_fyi)
        if Interop._interop_data is None:
            Interop._interop_data = metadata.fetch_interop_data(self.wpt_fyi)

    def categories(self, only_active: bool = True) -> Mapping[str, set[str]]:
        if self._categories is None:
            self._ensure_data()
            assert self._category_data is not None
            assert self._interop_data is not None

            year_key = str(self.year)
            if year_key not in self._category_data or year_key not in self._interop_data:
                raise ValueError(f"Invalid year {year_key}")
            all_categories = self._category_data[year_key]["categories"]
            year_categories = {
                key
                for key, value in self._interop_data[year_key]["focus_areas"].items()
                if (not only_active or value["countsTowardScore"])
            }

            self._categories = {
                item["name"]: set(item["labels"])
                for item in all_categories
                if item["name"] in year_categories
            }
        return self._categories


class Interop2023(Interop):
    year = 2023
    end_date = datetime(2024, 2, 1)
    configurations = [
        Configuration("desktop", "nightly", ["chrome", "firefox", "safari"]),
        Configuration("desktop", "stable", ["chrome", "firefox", "safari"]),
    ]


class Interop2024(Interop):
    year = 2024
    end_date = datetime(2025, 2, 6)
    configurations = [
        Configuration("desktop", "experimental", ["chrome", "firefox", "safari"]),
        Configuration("desktop", "stable", ["chrome", "firefox", "safari"]),
        Configuration("mobile", "experimental", ["chrome_android", "firefox_android"]),
    ]


class Interop2025(Interop):
    year = 2025
    end_date = datetime(2026, 2, 5)
    configurations = [
        Configuration("desktop", "experimental", ["chrome", "firefox", "safari"]),
        Configuration("desktop", "beta", ["chrome", "firefox"]),
        Configuration("desktop", "stable", ["chrome", "firefox", "safari"]),
        Configuration("mobile", "experimental", ["chrome_android", "firefox_android"]),
        Configuration("desktop", "experimental", ["firefox"], source="gecko"),
    ]


all_interops = [Interop2023, Interop2024, Interop2025]
interop_cls_by_year = cast(Mapping[int, type[Interop]], {item.year: item for item in all_interops})


def revision_prefix(configuration: Configuration) -> str:
    prefix = []
    if configuration.source != "wpt":
        prefix.append(configuration.source)
    if configuration.platform != "desktop":
        prefix.append(configuration.platform)
    if prefix:
        return f"{'-'.join(prefix)}-"
    return ""


class InteropScore(Repo):
    name = "interop-scores"
    remote = "https://github.com/jgraham/interop-results.git"
    bare = False
    main_branch = "main"

    def revisions_base_dir(self, interop: Interop) -> str:
        revisions_dir = os.path.join(self.path, str(interop.year), "results", "revisions")
        if not os.path.exists(revisions_dir):
            os.makedirs(revisions_dir)
        return revisions_dir

    def revision_paths(self, interop: Interop) -> Iterator[tuple[str, str]]:
        base_path = self.revisions_base_dir(interop)
        for revision in os.listdir(base_path):
            path = os.path.join(base_path, revision)
            if os.path.isdir(path):
                revision = os.path.basename(path)
                yield revision, path

    def runs(self, interop: Interop, configuration: Configuration) -> RunsByRevision:
        rv = []
        for revision, path in self.revision_paths(interop):
            revision_data = RevisionData.load(path, configuration, revision)
            if revision_data.runs:
                rv.append(revision_data.runs)

        return RunsByRevision(rv)

    def add_run_score(
        self,
        interop: Interop,
        configuration: Configuration,
        run: Run,
        metadata_revision: str,
        score: Mapping[str, int],
    ) -> None:
        revision_dir = os.path.join(self.revisions_base_dir(interop), run.full_revision_hash)
        if not os.path.exists(revision_dir):
            os.makedirs(revision_dir)
        revision_data = RevisionData.load(revision_dir, configuration, run.full_revision_hash)
        updated_paths = revision_data.add_run(
            revision_dir, interop, configuration, run, metadata_revision, score
        )

        self.git("add", *updated_paths)

    def latest_aligned_dir(self, interop: Interop) -> str:
        latest_dir = os.path.join(self.path, str(interop.year), "latest", "aligned")
        if not os.path.exists(latest_dir):
            os.makedirs(latest_dir)
        return latest_dir

    def latest_aligned(
        self, interop: Interop, configuration: Configuration
    ) -> Optional["AlignedRuns"]:
        return AlignedRuns.load(self.latest_aligned_dir(interop), interop, configuration)

    def set_latest_aligned(
        self, interop: Interop, configuration: Configuration, aligned_runs: "AlignedRuns"
    ) -> None:
        updated_paths = aligned_runs.write(self.latest_aligned_dir(interop), interop, configuration)
        self.git("add", *updated_paths)
        daily = aligned_runs.filter_by_day()
        updated_paths = daily.write(self.latest_aligned_dir(interop), interop, configuration, True)
        self.git("add", *updated_paths)

    def historic_aligned(
        self, interop: Interop, configuration: Configuration
    ) -> "HistoricAlignedRuns":
        return HistoricAlignedRuns.load(self.latest_aligned_dir(interop), interop, configuration)

    def set_historic_aligned(
        self,
        interop: Interop,
        configuration: Configuration,
        historic_aligned_runs: "HistoricAlignedRuns",
    ) -> None:
        updated_paths = historic_aligned_runs.write(
            self.latest_aligned_dir(interop), interop, configuration
        )
        self.git("add", *updated_paths)


class RevisionData:
    def __init__(self, runs: RevisionRuns):
        self.runs = runs

    @staticmethod
    def path(base_path: str, configuration: Configuration) -> str:
        runs_path = os.path.join(
            base_path, f"runs-{revision_prefix(configuration)}{configuration.channel}.json"
        )
        return runs_path

    @classmethod
    def load(self, base_path: str, configuration: Configuration, revision: str) -> "RevisionData":
        runs_path = RevisionData.path(base_path, configuration)
        try:
            with open(runs_path) as f:
                if configuration.source == "wpt":
                    runs = cast(
                        MutableSequence[Run], [WptFyiRun.from_json(item) for item in json.load(f)]
                    )
                else:
                    runs = cast(
                        MutableSequence[Run], [GeckoRun.from_json(item) for item in json.load(f)]
                    )
        except (OSError, json.JSONDecodeError):
            runs = []
        return RevisionData(RevisionRuns(revision, runs))

    def add_run(
        self,
        base_path: str,
        interop: Interop,
        configuration: Configuration,
        run: Run,
        metadata_revision: str,
        score: Mapping[str, int],
    ) -> list[str]:
        updated_files = []
        runs_path = RevisionData.path(base_path, configuration)

        if not any(item.run_id == run.run_id for item in self.runs):
            self.runs.runs.append(run)
            with open(runs_path, "w") as f:
                json.dump([run.to_json() for run in self.runs], f, indent=2)
            updated_files.append(runs_path)

        score_path = os.path.join(
            base_path,
            f"{run.browser_name}-{revision_prefix(configuration)}{configuration.channel}-{metadata_revision}.csv",
        )
        self.write(score_path, interop, score)
        updated_files.append(score_path)
        return updated_files

    def write(self, filename: str, interop: Interop, scores: Mapping[str, int]) -> None:
        with open(filename, "w") as f:
            writer = csv.writer(f)

            headers = ["category", "score"]
            writer.writerow(headers)
            for category in sorted(interop.categories().keys()):
                score = scores[category]
                writer.writerow([category, score])


class AlignedRunData:
    def __init__(
        self,
        revision: str,
        run_date: datetime,
        versions_by_product: Mapping[str, str],
        scores_by_category: ScoresByCategory,
        interop_scores: InteropScores,
    ):
        self.revision = revision
        self.run_date = run_date
        self.versions_by_product = versions_by_product
        self.scores_by_category = scores_by_category
        self.interop_scores = interop_scores

    @property
    def day(self) -> tuple[int, int]:
        return (self.run_date.month, self.run_date.day)

    def to_list(self, products: list[str], date_only: bool = False) -> list[str]:
        date = self.run_date.isoformat() if not date_only else self.run_date.strftime("%Y-%m-%d")
        data = [date]
        for i, product in enumerate(products):
            data.append(self.versions_by_product[product])
            for category, scores in sorted(self.scores_by_category.items()):
                data.append(str(self.scores_by_category[category][i]))
        data.extend(str(score) for _, score in sorted(self.interop_scores.items()))
        data.append(self.revision)
        return data

    def to_historic(self, metadata_revision: str) -> "HistoricAlignedRunData":
        return HistoricAlignedRunData(
            self.revision,
            metadata_revision,
            self.run_date,
            self.versions_by_product,
            self.scores_by_category,
            self.interop_scores,
        )


class HistoricAlignedRunData(AlignedRunData):
    def __init__(
        self,
        revision: str,
        metadata_revision: str,
        run_date: datetime,
        versions_by_product: Mapping[str, str],
        scores_by_category: ScoresByCategory,
        interop_scores: InteropScores,
    ):
        super().__init__(
            revision, run_date, versions_by_product, scores_by_category, interop_scores
        )
        self.metadata_revision = metadata_revision

    def to_list(self, products: list[str], date_only: bool = False) -> list[str]:
        rv = super().to_list(products, date_only)
        rv.append(self.metadata_revision)
        return rv


class AlignedRunsMetadata:
    def __init__(self, metadata_revision: str):
        self.metadata_revision = metadata_revision

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Self:
        return cls(data["metadata_revision"])

    def to_json(self) -> Mapping[str, Any]:
        return {"metadata_revision": self.metadata_revision}

    def write(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_json(), f)


class AlignedRuns:
    def __init__(self, data: list[AlignedRunData], metadata: AlignedRunsMetadata):
        self.data = data
        self.metadata = metadata

    def append(self, data: AlignedRunData) -> None:
        self.data.append(data)
        self.data.sort(key=lambda x: x.run_date)

    def filter_by_day(self) -> Self:
        runs: list[AlignedRunData] = []
        if not self.data:
            return self.__class__(runs, self.metadata)
        prev_item = self.data[0]
        for item in self.data[1:]:
            if item.day != prev_item.day:
                runs.append(prev_item)
            prev_item = item
        runs.append(self.data[-1])
        return self.__class__(runs, self.metadata)

    @staticmethod
    def paths(
        base_path: str, configuration: Configuration, date_only: bool = False
    ) -> tuple[str, str]:
        suffix = "-daily" if date_only else ""
        return (
            os.path.join(
                base_path,
                f"{revision_prefix(configuration)}{configuration.channel}-current{suffix}.csv",
            ),
            os.path.join(
                base_path,
                f"{revision_prefix(configuration)}{configuration.channel}-current-metadata.json",
            ),
        )

    @classmethod
    def load(cls, base_path: str, interop: Interop, configuration: Configuration) -> Optional[Self]:
        data_path, metadata_path = cls.paths(base_path, configuration, False)
        try:
            with open(data_path) as f:
                data = cls.data_from_csv(interop, configuration, csv.reader(f))
            with open(metadata_path) as f:
                metadata = AlignedRunsMetadata.from_json(json.load(f))
                return cls(data, metadata)
        except (OSError, json.JSONDecodeError):
            return None

    def write(
        self,
        base_path: str,
        interop: Interop,
        configuration: Configuration,
        date_only: bool = False,
    ) -> list[str]:
        data_path, metadata_path = self.paths(base_path, configuration, date_only)

        categories = list(interop.categories().keys())
        categories.sort()
        headers = ["date"]
        for product in configuration.products:
            headers.append(f"{product}-version")
            headers.extend(f"{product}-{category}" for category in categories)
        headers.extend(f"interop-{category}" for category in categories)
        headers.append("revision")

        with open(data_path, "w") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in self.data:
                writer.writerow(row.to_list(configuration.products, date_only))

        rv = [data_path]

        if not date_only:
            self.metadata.write(metadata_path)
            rv.append(metadata_path)

        return rv

    @staticmethod
    def data_from_csv(
        interop: Interop, configuration: Configuration, rows: Iterable[list[str]]
    ) -> list[AlignedRunData]:
        rv = []
        metadata_columns = {"revision", "date"}
        products = set(configuration.products)
        for (
            metadata_values,
            product_versions,
            scores_by_category,
            interop_scores,
        ) in read_scores_csv(metadata_columns, interop, configuration, True, rows):
            run_date = datetime.fromisoformat(metadata_values["date"])
            assert interop_scores is not None
            if set(product_versions.keys()) != products:
                raise ValueError("Missing product scores")
            rv.append(
                AlignedRunData(
                    metadata_values["revision"],
                    run_date,
                    product_versions,
                    scores_by_category,
                    interop_scores,
                )
            )
        return rv


class HistoricAlignedRuns:
    def __init__(self, data: list[HistoricAlignedRunData]):
        self.data = data
        self.revisions = {item.revision for item in self.data}

    def append(self, data: HistoricAlignedRunData) -> None:
        self.data.append(data)

    def has_revision(self, revision: str) -> bool:
        return revision in self.revisions

    @staticmethod
    def path(base_path: str, configuration: Configuration, date_only: bool = False) -> str:
        return os.path.join(
            base_path, f"{revision_prefix(configuration)}{configuration.channel}-historic.csv"
        )

    @classmethod
    def load(cls, base_path: str, interop: Interop, configuration: Configuration) -> Self:
        data_path = cls.path(base_path, configuration, False)
        try:
            with open(data_path) as f:
                data = cls.data_from_csv(interop, configuration, csv.reader(f))
        except OSError:
            data = []
        return cls(data)

    def write(
        self,
        base_path: str,
        interop: Interop,
        configuration: Configuration,
        date_only: bool = False,
    ) -> list[str]:
        data_path = self.path(base_path, configuration, date_only)

        categories = list(interop.categories().keys())
        categories.sort()
        headers = ["date"]
        for product in configuration.products:
            headers.append(f"{product}-version")
            headers.extend(f"{product}-{category}" for category in categories)
        headers.extend(f"interop-{category}" for category in categories)
        headers.extend(["revision", "metadata-revision"])

        with open(data_path, "w") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in self.data:
                writer.writerow(row.to_list(configuration.products, date_only))

        return [data_path]

    @staticmethod
    def data_from_csv(
        interop: Interop, configuration: Configuration, rows: Iterable[list[str]]
    ) -> list[HistoricAlignedRunData]:
        rv = []
        metadata_columns = {"revision", "metadata-revision", "date"}
        for (
            metadata_values,
            product_versions,
            scores_by_category,
            interop_scores,
        ) in read_scores_csv(metadata_columns, interop, configuration, True, rows):
            run_date = datetime.fromisoformat(metadata_values["date"])
            assert interop_scores is not None
            rv.append(
                HistoricAlignedRunData(
                    metadata_values["revision"],
                    metadata_values["metadata-revision"],
                    run_date,
                    product_versions,
                    scores_by_category,
                    interop_scores,
                )
            )
        return rv


def read_scores_csv(
    metadata_columns: set[str],
    interop: Interop,
    configuration: Configuration,
    has_interop_data: bool,
    rows: Iterable[list[str]],
) -> Iterator[
    tuple[Mapping[str, str], Mapping[str, str], ScoresByCategory, Optional[InteropScores]]
]:
    categories = list(interop.categories().keys())
    categories.sort()

    metadata_keys: dict[str, Optional[int]] = {item: None for item in metadata_columns}
    product_version_keys: dict[str, Optional[int]] = {item: None for item in configuration.products}
    product_score_index = {product: i for i, product in enumerate(configuration.products)}
    scores_by_category_keys: dict[str, list[Optional[int]]] = {
        category: [None] * len(configuration.products) for category in categories
    }

    if has_interop_data:
        interop_keys: Optional[dict[str, Optional[int]]] = {
            category: None for category in categories
        }
    else:
        interop_keys = None

    iterator = iter(rows)

    # First row is header
    for i, header in enumerate(next(iterator)):
        if header in metadata_keys:
            if metadata_keys[header] is not None:
                raise ValueError(f"Got duplicate header {header}")
            metadata_keys[header] = i
        else:
            product, category = header.split("-", 1)
            if product != "interop":
                if product not in configuration.products:
                    raise ValueError(f"Unknown header {header}")
                if category == "version":
                    if product_version_keys[product] is not None:
                        raise ValueError(f"Got duplicate header {header}")
                    product_version_keys[product] = i
                else:
                    if category not in scores_by_category_keys:
                        raise ValueError(f"Unknown header {header}")
                    if scores_by_category_keys[category][product_score_index[product]] is not None:
                        raise ValueError(f"Got duplicate header {header}")
                    scores_by_category_keys[category][product_score_index[product]] = i
            else:
                if interop_keys is None:
                    raise ValueError(f"Unexpected header {header}")
                if interop_keys[category]:
                    raise ValueError(f"Got duplicate header {header}")
                interop_keys[category] = i

    for field, key in metadata_keys.items():
        if key is None:
            raise ValueError(f"Missing {field} field")
    for product, key in product_version_keys.items():
        if key is None:
            raise ValueError(f"Missing {product}-version field")
    for category, indexes in scores_by_category_keys.items():
        for i, product in enumerate(configuration.products):
            if indexes[i] is None:
                raise ValueError(f"Missing {product}-{category} field")
    if interop_keys is not None:
        for category, key in interop_keys.items():
            if key is None:
                raise ValueError(f"Missing interop-{category} field")

    # mypy isn't elever enough to work out that we've checked for None values everywhere
    metadata_indexes = cast(dict[str, int], metadata_keys)
    product_version_indexes = cast(dict[str, int], product_version_keys)
    scores_by_category_indexes = cast(dict[str, list[int]], scores_by_category_keys)
    interop_indexes = cast(Optional[dict[str, int]], interop_keys)

    for data_row in iterator:
        metadata = {name: data_row[index] for name, index in metadata_indexes.items()}
        product_versions = {
            product: data_row[index] for product, index in product_version_indexes.items()
        }
        scores_by_category = {
            category: [int(data_row[index]) for index in category_indexes]
            for category, category_indexes in scores_by_category_indexes.items()
        }
        if interop_indexes is not None:
            interop_scores = {
                category: int(data_row[interop_indexes[category]]) for category in categories
            }
        else:
            interop_scores = None

        yield metadata, product_versions, scores_by_category, interop_scores


class RunCache:
    def __init__(self, current_runs: RunsByRevision):
        self.data: dict[str, Any] = {}
        for runs in current_runs:
            for run in runs:
                date = run.time_start.strftime("%Y-%m-%d")
                if date not in self.data:
                    self.data[date] = []
                self.data[date].append(run.to_json())

    def __enter__(self) -> RunCacheData:
        return RunCacheData(self.data)

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        pass


def updated_runs(
    old_runs: RunsByRevision, new_runs: RunsByRevision
) -> Mapping[str, MutableSequence[Run]]:
    updated = {}
    for runs in new_runs:
        if runs.revision not in old_runs:
            updated[runs.revision] = runs.runs
        else:
            old_ids = {item.run_id for item in old_runs[runs.revision]}
            updated_runs = [item for item in runs.runs if item.run_id not in old_ids]
            if updated_runs:
                updated[runs.revision] = updated_runs
    return updated


def score_aligned_runs(
    results_cache_path: str,
    configuration: Configuration,
    runs: RevisionRuns,
    tests_by_category: Mapping[str, set[str]],
) -> AlignedRunData:
    logger.info(f"Generating aligned results for revision {runs.revision}")
    runs_by_product = {run.browser_name: run for run in runs}
    run_ids = [runs_by_product[product].run_id for product in configuration.products]
    product_versions = {
        product: runs_by_product[product].browser_version for product in configuration.products
    }

    scores_by_category, interop_scores, _ = _wpt_interop.score_runs(
        results_cache_path, run_ids, tests_by_category, set()
    )
    return AlignedRunData(
        runs.revision, runs.min_start_time, product_versions, scores_by_category, interop_scores
    )


def get_runs(
    results_analysis_repo: ResultsAnalysisCache,
    interop: Interop,
    configuration: Configuration,
    aligned: bool,
    run_cache: Optional[RunCache],
) -> RunsByRevision:
    from_date = datetime(interop.year, 1, 1)
    to_date = min(interop.end_date, datetime.now())

    if configuration.source == "wpt":
        return fetch_runs_wptfyi(
            configuration.products,
            configuration.channel,
            from_date,
            to_date,
            aligned=False,
            run_cache=run_cache,
        )
    if configuration.source == "gecko":
        run_info_filter: Mapping[str, int | str | bool] = {
            "os": "linux",
            "debug": False,
            "bits": 64,
            "asan": False,
            "tsan": False,
            "headless": False,
            "display": "x11",
            "ccov": False,
            "privateBrowsing": False,
            "fission": True,
            "incOriginInit": False,
        }

        runs = fetch_runs_gecko(results_analysis_repo, run_info_filter, from_date, to_date)
        return runs

    raise ValueError(f"Don't know how to get runs from source {configuration.source}")


def update_configuration(
    results_analysis_repo: ResultsAnalysisCache,
    metadata_repo: Metadata,
    interop_repo: InteropScore,
    interop: Interop,
    configuration: Configuration,
) -> None:
    metadata_revision, tests_by_category, _ = metadata_repo.tests_by_category(
        interop.categories(True)
    )

    # Score new runs since the run of this code
    stored_runs = interop_repo.runs(interop, configuration)

    run_cache = RunCache(stored_runs)

    all_runs = get_runs(
        results_analysis_repo,
        interop,
        configuration,
        aligned=False,
        run_cache=run_cache,
    )

    updated = updated_runs(stored_runs, all_runs)

    if updated:
        for revision, runs in updated.items():
            logger.info(f"Generating results for revision {revision}")
            try:
                scores, _, _ = _wpt_interop.score_runs(
                    results_analysis_repo.path,
                    [item.run_id for item in runs],
                    tests_by_category,
                    set(),
                )
            except OSError as e:
                if "refs/tags/run/" in str(e):
                    # We didn't find the run data, probably want to try again with just some runs
                    # But skip for now
                    logger.warning(f"Failed to generate scores for revision {revision}:\n  {e}")
                    continue
                raise
            else:
                for i, run in enumerate(runs):
                    run_score = {}
                    for category in interop.categories():
                        category_scores = scores[category]
                        assert len(category_scores) == len(runs)
                        run_score[category] = category_scores[i]
                    interop_repo.add_run_score(
                        interop, configuration, run, metadata_revision, run_score
                    )

        # Check for newly aligned runs
        aligned_all = interop_repo.latest_aligned(interop, configuration)
        recompute_all = True
        if aligned_all is not None:
            # Check if the interop tests changed since the previous metadata revision
            metadata_revision = aligned_all.metadata.metadata_revision
            _, prev_tests_by_category, _ = metadata_repo.tests_by_category(
                interop.categories(), metadata_revision
            )
            recompute_all = prev_tests_by_category != tests_by_category
        if not recompute_all:
            logger.info("Metadata has not changed; adding new runs")
            assert aligned_all is not None
            for revision_runs in all_runs.filter_by_revisions(set(updated.keys())):
                if not revision_runs.is_aligned(configuration.products):
                    continue
                try:
                    aligned_run_data = score_aligned_runs(
                        results_analysis_repo.path, configuration, revision_runs, tests_by_category
                    )
                except OSError as e:
                    if "refs/tags/run/" in str(e):
                        logger.warning(
                            f"""Failed to generate aligned scores for revision {revision}:
  {e}"""
                        )
                        continue
                aligned_all.append(aligned_run_data)
            new_aligned = aligned_all
        else:
            logger.info("Metadata changed; recomputing all runs")
            data = []
            AlignedRunsMetadata(metadata_revision)
            for revision_runs in all_runs:
                if not revision_runs.is_aligned(configuration.products):
                    continue
                try:
                    aligned_run_data = score_aligned_runs(
                        results_analysis_repo.path, configuration, revision_runs, tests_by_category
                    )
                except OSError as e:
                    if "refs/tags/run/" in str(e):
                        logger.warning(
                            f"""Failed to generate aligned scores for revision {revision}:
  {e}"""
                        )
                        continue
                data.append(aligned_run_data)
            new_aligned = AlignedRuns(data, AlignedRunsMetadata(metadata_revision))
        interop_repo.set_latest_aligned(interop, configuration, new_aligned)

        if new_aligned.data:
            aligned_historic = interop_repo.historic_aligned(interop, configuration)
            for new_aligned_run in new_aligned.data:
                if not aligned_historic.has_revision(new_aligned_run.revision):
                    aligned_historic.append(new_aligned_run.to_historic(metadata_revision))
            interop_repo.set_historic_aligned(interop, configuration, aligned_historic)
        else:
            logger.info("Didn't find any new aligned runs")


def get_default_years() -> list[int]:
    years = []
    now = datetime.now()
    for year, interop in interop_cls_by_year.items():
        if now.date() <= interop.end_date.date():
            years.append(year)
    return years


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "warn", "info", "debug"],
        help="Logging level",
    )
    parser.add_argument("--pdb", action="store_true", help="Drop into pdb on exception")
    parser.add_argument("--repo-root", default=None, help="Base path for working repos")
    parser.add_argument(
        "--wpt-results-analysis-cache", default=None, help="Path to wpt results-analysis-cache repo"
    )
    parser.add_argument(
        "--gecko-results-analysis-cache",
        default=None,
        help="Path to gecko results-analysis-cache repo",
    )
    parser.add_argument("--metadata", default=None, help="Path to metadata repo")
    parser.add_argument("--interop-score", default=None, help="Path to output interop-score repo")
    parser.add_argument(
        "--year", dest="years", action="append", type=int, help="Interop year to update"
    )
    parser.add_argument("--wpt-fyi", help="Base URL to use for wpt.fyi")
    parser.add_argument(
        "--commit-on-error",
        action="store_true",
        help="Commit complete changes even if there's an uncaught exception",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite local changes to output repo when pulling remote",
    )
    return parser


def run(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.getLevelNamesMapping()[args.log_level.upper()])
    logging.getLogger("wpt_interop").setLevel(logging.INFO)

    results_analysis_repos = {
        "wpt": WptResultsAnalysisCache(args.wpt_results_analysis_cache, args.repo_root),
        "gecko": GeckoResultsAnalysisCache(args.gecko_results_analysis_cache, args.repo_root),
    }
    metadata_repo = Metadata(args.metadata, args.repo_root)
    interop_repo = InteropScore(args.interop_score, args.repo_root)

    years = args.years if args.years is not None else get_default_years()

    for repo in [metadata_repo, interop_repo] + list(results_analysis_repos.values()):
        overwrite = args.overwrite and repo == interop_repo
        repo.update(overwrite)

    for year in years:
        try:
            interop: Interop = interop_cls_by_year[year](args.wpt_fyi)
        except KeyError:
            raise argparse.ArgumentError(None, message=f"No such year {year}")

        interop_repo.clean()

        for configuration in interop.configurations:
            logger.info(
                f"Updating {configuration.platform} channel:{configuration.channel} source:{configuration.source}"
            )
            results_analysis_repo = results_analysis_repos[configuration.source]

            got_exception = False
            try:
                update_configuration(
                    results_analysis_repo, metadata_repo, interop_repo, interop, configuration
                )
            except Exception:
                got_exception = True
                raise
            finally:
                if not got_exception or args.commit_on_error:
                    msg = (
                        f"Update interop score data for platform '{configuration.platform}' "
                        f"channel '{configuration.channel}'"
                    )
                    interop_repo.commit(msg)


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


if __name__ == "__main__":
    main()
