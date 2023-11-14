from collections import defaultdict
from functools import cache
from typing import Any, Callable, Mapping, Optional, Set, Tuple

import requests

CATEGORY_URL = ("https://raw.githubusercontent.com/web-platform-tests/"
                "results-analysis/main/interop-scoring/category-data.json")
INTEROP_DATA_URL = "https://wpt.fyi/static/interop-data.json"
METADATA_URL = "https://wpt.fyi/api/metadata?includeTestLevel=true&product=chrome"


def fetch_category_data() -> Mapping[str, Mapping[str, Any]]:
    return requests.get(CATEGORY_URL).json()


def fetch_interop_data() -> Mapping[str, Mapping[str, Any]]:
    return requests.get(INTEROP_DATA_URL).json()


def fetch_labelled_tests() -> Mapping[str, set]:
    rv = defaultdict(set)
    data = requests.get(METADATA_URL).json()
    for test, metadata in data.items():
        for meta_item in metadata:
            if "label" in meta_item:
                rv[meta_item["label"]].add(test)
    return rv


def categories_for_year(year: int,
                        category_data: Mapping[str, Mapping[str, Any]],
                        interop_data: Mapping[str, Mapping[str, Any]],
                        only_active: bool = True) -> Mapping[str, Set[str]]:
    year_key = str(year)
    if year_key not in category_data or year_key not in interop_data:
        raise ValueError(f"Invalid year {year}")
    all_categories = category_data[year_key]["categories"]
    year_categories = {key for key, value in interop_data[year_key]["focus_areas"].items()
                       if (not only_active or value["countsTowardScore"])}
    return {item["name"]: set(item["labels"])
            for item in all_categories
            if item["name"] in year_categories}


@cache
def get_category_data(year: int,
                      only_active: bool = True,
                      category_filter: Optional[Callable[[str], bool]] = None
                      ) -> Tuple[Mapping[str, Set[str]], Set[str]]:
    category_data = fetch_category_data()
    interop_data = fetch_interop_data()
    labelled_tests = fetch_labelled_tests()

    categories = categories_for_year(year, category_data, interop_data, only_active)

    tests_by_category = {}
    all_tests = set()
    for category_name, labels in categories.items():
        if category_filter is not None and not category_filter(category_name):
            continue
        tests = set()
        for label in labels:
            tests |= labelled_tests.get(label, set())
        tests_by_category[category_name] = tests
        all_tests |= tests

    return tests_by_category, all_tests
