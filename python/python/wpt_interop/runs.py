import json
import logging
import os
from datetime import datetime, timedelta
from types import TracebackType
from typing import (
    Any,
    ContextManager,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    MutableSequence,
    Optional,
    Sequence,
)
from urllib.parse import urlencode

from .repo import ResultsAnalysisCache
from ._wpt_interop import gecko_runs

import requests

RUNS_URL = "https://wpt.fyi/api/runs"

RunsByDate = Mapping[str, list["RevisionRuns"]]

logger = logging.getLogger("wpt_interop.runs")

Json = None | int | float | str | bool | Sequence["Json"] | Mapping[str, "Json"]


class Run:
    def __init__(
        self,
        run_id: str,
        browser_name: str,
        browser_version: str,
        os_name: str,
        os_version: str,
        revision: str,
        full_revision_hash: str,
        created_at: datetime,
        time_start: datetime,
    ):
        self.run_id = run_id
        self.browser_name = browser_name
        self.browser_version = browser_version
        self.os_name = os_name
        self.os_version = os_version
        self.revision = revision
        self.full_revision_hash = full_revision_hash
        self.created_at = created_at
        self.time_start = time_start

    @classmethod
    def from_json(cls, data: Mapping[str, Json]) -> "Run":
        run_id = data["id"]
        assert isinstance(run_id, str)
        browser_name = data["browser_name"]
        assert isinstance(browser_name, str)
        browser_version = data["browser_version"]
        assert isinstance(browser_version, str)
        os_name = data["os_name"]
        assert isinstance(os_name, str)
        os_version = data["os_version"]
        assert isinstance(os_version, str)
        revision = data["revision"]
        assert isinstance(revision, str)
        full_revision_hash = data["full_revision_hash"]
        assert isinstance(full_revision_hash, str)
        created_at = data["created_at"]
        assert isinstance(created_at, str)
        time_start = data["time_start"]
        assert isinstance(time_start, str)
        return cls(
            run_id,
            browser_name,
            browser_version,
            os_name,
            os_version,
            revision,
            full_revision_hash,
            datetime.fromisoformat(created_at),
            datetime.fromisoformat(time_start),
        )

    def to_json(self) -> MutableMapping[str, Json]:
        return {
            "id": self.run_id,
            "browser_name": self.browser_name,
            "browser_version": self.browser_version,
            "os_name": self.os_name,
            "os_version": self.os_version,
            "revision": self.revision,
            "full_revision_hash": self.full_revision_hash,
            "created_at": self.created_at.isoformat(),
            "time_start": self.time_start.isoformat(),
        }


class WptFyiRun(Run):
    def __init__(
        self,
        run_id: str,
        browser_name: str,
        browser_version: str,
        os_name: str,
        os_version: str,
        revision: str,
        full_revision_hash: str,
        created_at: datetime,
        time_start: datetime,
        time_end: datetime,
        results_url: str,
        raw_results_url: str,
        labels: list[str],
    ):
        super().__init__(
            run_id,
            browser_name,
            browser_version,
            os_name,
            os_version,
            revision,
            full_revision_hash,
            created_at,
            time_start,
        )
        self.time_end = time_end
        self.results_url = results_url
        self.raw_results_url = raw_results_url
        self.labels = labels

    @classmethod
    def from_json(cls, data: Mapping[str, Json]) -> "WptFyiRun":
        run_id = str(data["id"])
        browser_name = data["browser_name"]
        assert isinstance(browser_name, str)
        browser_version = data["browser_version"]
        assert isinstance(browser_version, str)
        os_name = data["os_name"]
        assert isinstance(os_name, str)
        os_version = data["os_version"]
        assert isinstance(os_version, str)
        revision = data["revision"]
        assert isinstance(revision, str)
        full_revision_hash = data["full_revision_hash"]
        assert isinstance(full_revision_hash, str)
        created_at = data["created_at"]
        assert isinstance(created_at, str)
        time_start = data["time_start"]
        assert isinstance(time_start, str)
        time_end = data["time_end"]
        assert isinstance(time_end, str)
        results_url = data["results_url"]
        assert isinstance(results_url, str)
        raw_results_url = data["raw_results_url"]
        assert isinstance(raw_results_url, str)
        labels = data["labels"]
        assert isinstance(labels, list)
        for item in labels:
            assert isinstance(item, str)
        return cls(
            run_id,
            browser_name,
            browser_version,
            os_name,
            os_version,
            revision,
            full_revision_hash,
            datetime.fromisoformat(created_at),
            datetime.fromisoformat(time_start),
            datetime.fromisoformat(time_end),
            results_url,
            raw_results_url,
            labels,
        )

    def to_json(self) -> MutableMapping[str, Json]:
        rv = super().to_json()
        rv.update(
            {
                "results_url": self.results_url,
                "time_end": self.time_end.isoformat(),
                "raw_results_url": self.raw_results_url,
                "labels": self.labels,
            }
        )
        return rv


class GeckoRun(Run):
    def __init__(
        self,
        run_id: str,
        browser_name: str,
        browser_version: str,
        os_name: str,
        os_version: str,
        revision: str,
        full_revision_hash: str,
        created_at: datetime,
        time_start: datetime,
        run_info: Json,
    ):
        super().__init__(
            run_id,
            browser_name,
            browser_version,
            os_name,
            os_version,
            revision,
            full_revision_hash,
            created_at,
            time_start,
        )
        self.run_info = run_info

    @classmethod
    def from_run(
        cls, commit: str, push_date: datetime, run_id: str, run_info: Mapping[str, Json]
    ) -> "GeckoRun":
        browser = run_info["product"]
        assert isinstance(browser, str)
        browser_version = run_info["browser_build_id"]
        assert isinstance(browser_version, str)
        os_name = run_info["os"]
        assert isinstance(os_name, str)
        os_version = run_info["os_version"]
        assert isinstance(os_version, str)

        return cls(
            run_id,
            browser,
            browser_version,
            os_name,
            os_version,
            commit,
            commit,
            push_date,
            push_date,
            run_info,
        )

    @classmethod
    def from_json(cls, data: Mapping[str, Json]) -> "GeckoRun":
        run_id = data["id"]
        assert isinstance(run_id, str)
        browser_name = data["browser_name"]
        assert isinstance(browser_name, str)
        browser_version = data["browser_version"]
        assert isinstance(browser_version, str)
        os_name = data["os_name"]
        assert isinstance(os_name, str)
        os_version = data["os_version"]
        assert isinstance(os_version, str)
        revision = data["revision"]
        assert isinstance(revision, str)
        full_revision_hash = data["full_revision_hash"]
        assert isinstance(full_revision_hash, str)
        created_at = data["created_at"]
        assert isinstance(created_at, str)
        time_start = data["time_start"]
        assert isinstance(time_start, str)
        run_info = data["run_info"]
        return cls(
            run_id,
            browser_name,
            browser_version,
            os_name,
            os_version,
            revision,
            full_revision_hash,
            datetime.fromisoformat(created_at),
            datetime.fromisoformat(time_start),
            run_info,
        )

    def to_json(self) -> MutableMapping[str, Json]:
        rv = super().to_json()
        rv["run_info"] = self.run_info
        return rv


class RevisionRuns:
    # Runs for a specific revision
    def __init__(self, revision: str, runs: MutableSequence[Run]):
        self.revision = revision
        self.runs = runs

    def __len__(self) -> int:
        return len(self.runs)

    def __bool__(self) -> bool:
        return bool(self.runs)

    def __iter__(self) -> Iterator[Run]:
        yield from self.runs

    def append(self, run: Run) -> None:
        self.runs.append(run)

    def extend(self, other: Iterable[Run]) -> None:
        self.runs.extend(other)

    @property
    def min_start_time(self) -> datetime:
        return min(item.time_start for item in self.runs)

    def run_ids(self) -> list[str]:
        return [item.run_id for item in self.runs]

    def is_aligned(self, products: list[str]) -> bool:
        # Check if we have a run for each product
        return {item.browser_name for item in self.runs} == set(products)


class RunsByRevision:
    def __init__(self, runs: list[RevisionRuns]) -> None:
        self._runs = runs
        self._make_index()

    def _make_index(self) -> None:
        self._runs.sort(key=lambda x: x.min_start_time)
        self._index = {}
        for run in self._runs:
            self._index[run.revision] = run

    def __iter__(self) -> Iterator[RevisionRuns]:
        """Iterator over runs in date order"""
        for run in self._runs:
            yield run

    def __contains__(self, revision: str) -> bool:
        return revision in self._index

    def __getitem__(self, revision: str) -> RevisionRuns:
        return self._index[revision]

    def filter_by_revisions(self, revisions: set[str]) -> "RunsByRevision":
        return RunsByRevision([item for item in self._runs if item.revision in revisions])


def group_by_date(runs_by_revision: RunsByRevision) -> RunsByDate:
    runs_by_date: dict[str, list[RevisionRuns]] = {}
    for rev_runs in runs_by_revision:
        date = rev_runs.min_start_time.strftime("%Y-%m-%d")
        if date not in runs_by_date:
            runs_by_date[date] = []
        runs_by_date[date].append(rev_runs)

    return runs_by_date


def run_info_filter_matches(
    run_info_filter: Mapping[str, Json], run_info: Mapping[str, Json]
) -> bool:
    for key, value in run_info_filter.items():
        if key in run_info and run_info[key] != value:
            return False
    return True


def fetch_runs_gecko(
    results_analysis_repo: ResultsAnalysisCache,
    run_info_filter: Mapping[str, Json],
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
) -> RunsByRevision:
    now = datetime.now()
    if from_date is None:
        from_date = datetime(now.year, 1, 1)
    if to_date is None:
        to_date = datetime(now.year, now.month, now.day)

    rv = []
    for date_commits in gecko_runs(
        results_analysis_repo.path, "mozilla-central", from_date, to_date
    ).values():
        for commit, commit_runs in date_commits.items():
            revision_runs = RevisionRuns(commit, [])
            push_date = commit_runs.push_date
            for run_data in commit_runs.runs.values():
                if run_info_filter_matches(run_info_filter, run_data.run_info):
                    revision_runs.append(
                        GeckoRun.from_run(commit, push_date, run_data.id, run_data.run_info)
                    )
            if revision_runs:
                rv.append(revision_runs)
                # TODO: remove this
                assert len(revision_runs) == 1

    return RunsByRevision(rv)


def fetch_runs_wptfyi(
    products: list[str],
    channel: str,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    aligned: bool = True,
    max_per_day: Optional[int] = None,
    run_cache: Optional[ContextManager["RunCacheData"]] = None,
) -> RunsByRevision:
    """Fetch all the runs for a given date range.

    Runs are only fetched if they aren't found (keyed by date) in the run_cache."""

    revision_index: MutableMapping[str, int] = {}
    rv: list[RevisionRuns] = []

    now = datetime.now()
    if from_date is None:
        from_date = datetime(now.year, 1, 1)
    if to_date is None:
        to_date = datetime(now.year, now.month, now.day)

    query = [
        ("label", "master"),
        ("label", channel),
    ]
    for product in products:
        query.append(("product", product))
    if aligned:
        query.append(("aligned", "true"))
    if max_per_day:
        query.append(("max-count", str(max_per_day)))

    url = f"{RUNS_URL}?{urlencode(query)}"

    fetch_date = from_date
    cache_cutoff_date = now - timedelta(days=3)

    if run_cache is None:
        run_cache = RunCache(products, channel, aligned, max_per_day)
    assert run_cache is not None

    with run_cache as cache:
        while fetch_date < to_date:
            next_date = fetch_date + timedelta(days=1)

            if fetch_date in cache and fetch_date < cache_cutoff_date:
                logger.debug(f"Using cached data for {fetch_date.strftime('%Y-%m-%d')}")
                day_runs = cache[fetch_date]
            else:
                date_query = urlencode(
                    {"from": fetch_date.strftime("%Y-%m-%d"), "to": next_date.strftime("%Y-%m-%d")}
                )
                date_url = f"{url}&{date_query}"
                logger.info(f"Fetching runs from {date_url}")
                day_runs = requests.get(date_url).json()
                cache[fetch_date] = day_runs

            by_revision = group_by_revision(day_runs)
            for revision, runs in by_revision.items():
                if revision not in revision_index:
                    idx = len(rv)
                    revision_index[revision] = idx
                    rv.append(RevisionRuns(revision, []))

                rv[revision_index[revision]].extend(runs)

            fetch_date = next_date

    return RunsByRevision(rv)


def group_by_revision(runs: list[Mapping[str, Any]]) -> Mapping[str, list[WptFyiRun]]:
    rv: dict[str, list[WptFyiRun]] = {}
    for run_json in runs:
        run = WptFyiRun.from_json(run_json)
        if run.full_revision_hash not in rv:
            rv[run.full_revision_hash] = []
        rv[run.full_revision_hash].append(run)
    return rv


class RunCacheData:
    """Run cache that stores a map of {date: [Run as JSON]}, matching the fetch_runs endpoint"""

    def __init__(self, data: MutableMapping[str, Any]):
        self.data = data

    def __contains__(self, date: datetime) -> bool:
        return date.strftime("%Y-%m-%d") in self.data

    def __getitem__(self, date: datetime) -> list[Mapping[str, Any]]:
        return self.data[date.strftime("%Y-%m-%d")]

    def __setitem__(self, date: datetime, value: list[Mapping[str, Any]]) -> None:
        self.data[date.strftime("%Y-%m-%d")] = value


class RunCache:
    def __init__(
        self,
        products: list[str],
        channel: str,
        aligned: bool = True,
        max_per_day: Optional[int] = None,
    ):
        products_str = "-".join(products)

        self.path = (
            f"products:{products_str}-channel:{channel}-"
            f"aligned:{aligned}-max_per_day:{max_per_day}.json"
        )
        self.data: Optional[RunCacheData] = None

    def __enter__(self) -> RunCacheData:
        if os.path.exists(self.path):
            with open(self.path) as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    data = {}
        if self.data is None:
            data = {}
        self.data = RunCacheData(data)
        return self.data

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        if self.data is not None:
            with open(self.path, "w") as f:
                json.dump(self.data.data, f)
            self.data = None
