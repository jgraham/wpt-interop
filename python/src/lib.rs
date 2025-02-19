extern crate wpt_interop as interop;
use interop::TestStatus;
use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use std::collections::{BTreeMap, BTreeSet};
use std::convert::TryFrom;
use std::fmt;
use std::path::PathBuf;

#[derive(Debug)]
struct Error(interop::Error);

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::convert::From<interop::Error> for Error {
    fn from(err: interop::Error) -> Error {
        Error(err)
    }
}

impl std::convert::From<Error> for PyErr {
    fn from(err: Error) -> PyErr {
        PyOSError::new_err(err.0.to_string())
    }
}

#[derive(Debug, FromPyObject, IntoPyObject)]
struct Results {
    status: String,
    subtests: Vec<SubtestResult>,
    expected: Option<String>,
}

impl TryFrom<Results> for interop::Results {
    type Error = interop::Error;

    fn try_from(value: Results) -> Result<interop::Results, interop::Error> {
        Ok(interop::Results {
            status: interop::TestStatus::try_from(value.status.as_ref())?,
            subtests: value
                .subtests
                .iter()
                .map(interop::SubtestResult::try_from)
                .collect::<Result<Vec<_>, _>>()?,
            expected: value
                .expected
                .map(|expected| interop::TestStatus::try_from(expected.as_ref()))
                .transpose()?,
        })
    }
}

impl From<interop::Results> for Results {
    fn from(value: interop::Results) -> Results {
        Results {
            status: value.status.to_string(),
            subtests: value
                .subtests
                .iter()
                .map(SubtestResult::from)
                .collect::<Vec<_>>(),
            expected: value.expected.map(|expected| expected.to_string()),
        }
    }
}

#[derive(Debug, FromPyObject, IntoPyObject)]
struct SubtestResult {
    name: String,
    status: String,
    expected: Option<String>,
}

impl TryFrom<&SubtestResult> for interop::SubtestResult {
    type Error = interop::Error;

    fn try_from(value: &SubtestResult) -> Result<interop::SubtestResult, interop::Error> {
        Ok(interop::SubtestResult {
            name: value.name.clone(),
            status: interop::SubtestStatus::try_from(value.status.as_ref())?,
            expected: value
                .expected
                .as_ref()
                .map(|expected| interop::SubtestStatus::try_from(expected.as_ref()))
                .transpose()?,
        })
    }
}

impl From<&interop::SubtestResult> for SubtestResult {
    fn from(value: &interop::SubtestResult) -> SubtestResult {
        SubtestResult {
            name: value.name.clone(),
            status: value.status.to_string(),
            expected: value.expected.map(|expected| expected.to_string()),
        }
    }
}

#[pyfunction]
fn interop_score(
    runs: Vec<BTreeMap<String, Results>>,
    tests: BTreeMap<String, BTreeSet<String>>,
    expected_not_ok: BTreeSet<String>,
) -> PyResult<(
    interop::RunScores,
    interop::InteropScore,
    interop::ExpectedFailureScores,
)> {
    // This is a (second?) copy of all the input data
    let mut interop_runs: Vec<BTreeMap<String, interop::Results>> = Vec::with_capacity(runs.len());
    for run in runs.into_iter() {
        let mut run_map: BTreeMap<String, interop::Results> = BTreeMap::new();
        for (key, value) in run.into_iter() {
            run_map.insert(key, value.try_into().map_err(Error::from)?);
        }
        interop_runs.push(run_map);
    }
    Ok(interop::score_runs(
        interop_runs.iter(),
        &tests,
        &expected_not_ok,
    ))
}

#[pyfunction]
fn run_results(
    results_repo: PathBuf,
    run_ids: Vec<u64>,
    tests: BTreeSet<String>,
) -> PyResult<Vec<BTreeMap<String, Results>>> {
    let results_cache: interop::results_cache::ResultsCache =
        interop::results_cache::ResultsCache::new(&results_repo).map_err(Error::from)?;
    let mut results = Vec::with_capacity(run_ids.len());
    for run_id in run_ids.into_iter() {
        let mut run_results: BTreeMap<String, Results> = BTreeMap::new();
        for (key, value) in results_cache
            .results(run_id, Some(&tests))
            .map_err(Error::from)?
            .into_iter()
        {
            run_results.insert(key, value.into());
        }
        results.push(run_results)
    }
    Ok(results)
}

#[pyfunction]
fn score_runs(
    results_repo: PathBuf,
    run_ids: Vec<u64>,
    tests_by_category: BTreeMap<String, BTreeSet<String>>,
    expected_not_ok: BTreeSet<String>,
) -> PyResult<(
    interop::RunScores,
    interop::InteropScore,
    interop::ExpectedFailureScores,
)> {
    let mut all_tests = BTreeSet::new();
    for tests in tests_by_category.values() {
        all_tests.extend(tests.iter().map(|item| item.into()));
    }
    let results_cache: interop::results_cache::ResultsCache =
        interop::results_cache::ResultsCache::new(&results_repo).map_err(Error::from)?;

    let run_results = run_ids
        .into_iter()
        .map(|run_id| results_cache.results(run_id, Some(&all_tests)))
        .collect::<interop::Result<Vec<_>>>()
        .map_err(Error::from)?;
    Ok(interop::score_runs(
        run_results.iter(),
        &tests_by_category,
        &expected_not_ok,
    ))
}

type TestSet = BTreeSet<String>;
type TestsByCategory = BTreeMap<String, TestSet>;

#[pyfunction]
#[pyo3(signature = (metadata_repo_path, labels_by_category, metadata_revision=None))]
fn interop_tests(
    metadata_repo_path: PathBuf,
    labels_by_category: BTreeMap<String, BTreeSet<String>>,
    metadata_revision: Option<String>,
) -> PyResult<(String, TestsByCategory, TestSet)> {
    let mut tests_by_category = BTreeMap::new();
    let mut all_tests = BTreeSet::new();
    let (commit_id, metadata) =
        interop::metadata::load_metadata(&metadata_repo_path, metadata_revision.as_deref())
            .map_err(Error::from)?;
    let patterns_by_label = metadata.patterns_by_label(None);
    for (category, labels) in labels_by_category.into_iter() {
        let mut tests = BTreeSet::new();
        for label in labels.iter() {
            if let Some(patterns) = patterns_by_label.get(&label.as_str()) {
                tests.extend(patterns.iter().map(|x| x.to_string()));
                all_tests.extend(patterns.iter().map(|x| x.to_string()));
            }
        }
        tests_by_category.insert(category, tests);
    }
    Ok((commit_id.to_string(), tests_by_category, all_tests))
}

fn is_regression(prev_status: TestStatus, new_status: TestStatus) -> bool {
    (prev_status == TestStatus::Pass || prev_status == TestStatus::Ok) && new_status != prev_status
}

fn is_subtest_regression(
    prev_status: interop::SubtestStatus,
    new_status: interop::SubtestStatus,
) -> bool {
    prev_status == interop::SubtestStatus::Pass && new_status != prev_status
}

type TestRegression = Option<String>;
type SubtestRegression = Vec<(String, String)>;
type Labels = Vec<String>;

#[pyfunction]
fn regressions(
    results_repo: PathBuf,
    metadata_repo_path: PathBuf,
    run_ids: (u64, u64),
) -> PyResult<BTreeMap<String, (TestRegression, SubtestRegression, Labels)>> {
    let results_cache: interop::results_cache::ResultsCache =
        interop::results_cache::ResultsCache::new(&results_repo).map_err(Error::from)?;
    let (_, metadata) =
        interop::metadata::load_metadata(&metadata_repo_path, None).map_err(Error::from)?;
    let base_results = results_cache
        .results(run_ids.0, None)
        .map_err(Error::from)?;
    let comparison_results = results_cache
        .results(run_ids.1, None)
        .map_err(Error::from)?;

    let mut regressed = BTreeMap::new();
    for (test, new_results) in comparison_results.iter() {
        if let Some(prev_results) = base_results.get(test) {
            let test_regression = if is_regression(prev_results.status, new_results.status) {
                Some(new_results.status.to_string())
            } else {
                None
            };
            let mut subtest_regressions = Vec::new();
            let prev_subtest_results = BTreeMap::from_iter(
                prev_results
                    .subtests
                    .iter()
                    .map(|result| (&result.name, result.status)),
            );
            let test_metadata = metadata.get(test);
            for (subtest, new_subtest_result) in new_results
                .subtests
                .iter()
                .map(|result| (&result.name, result.status))
            {
                if let Some(prev_subtest_result) = prev_subtest_results.get(&subtest) {
                    if is_subtest_regression(*prev_subtest_result, new_subtest_result) {
                        subtest_regressions.push((subtest.clone(), new_subtest_result.to_string()));
                    }
                }
            }
            if test_regression.is_some() || !subtest_regressions.is_empty() {
                let labels = if let Some(test_metadata) = test_metadata {
                    Vec::from_iter(test_metadata.labels.iter().cloned())
                } else {
                    vec![]
                };
                regressed.insert(test.clone(), (test_regression, subtest_regressions, labels));
            }
        }
    }
    Ok(regressed)
}

#[pymodule]
#[pyo3(name = "_wpt_interop")]
fn _wpt_interop(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(interop_score, m)?)?;
    m.add_function(wrap_pyfunction!(run_results, m)?)?;
    m.add_function(wrap_pyfunction!(score_runs, m)?)?;
    m.add_function(wrap_pyfunction!(interop_tests, m)?)?;
    m.add_function(wrap_pyfunction!(regressions, m)?)?;
    Ok(())
}
