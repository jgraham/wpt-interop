extern crate wpt_interop as interop;
use interop::TestStatus;
use pyo3::conversion::IntoPyObjectExt;
use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
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
    run_ids: Vec<String>,
    tests: BTreeSet<String>,
) -> PyResult<Vec<BTreeMap<String, Results>>> {
    let results_cache = interop::results_cache::get(&results_repo).map_err(Error::from)?;

    let mut results = Vec::with_capacity(run_ids.len());
    for run_id in run_ids.into_iter() {
        let mut run_results: BTreeMap<String, Results> = BTreeMap::new();
        for (key, value) in results_cache
            .results(&run_id, Some(&tests))
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
    run_ids: Vec<String>,
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
    let results_cache = interop::results_cache::get(&results_repo).map_err(Error::from)?;

    let run_results = run_ids
        .into_iter()
        .map(|run_id| results_cache.results(&run_id, Some(&all_tests)))
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
    run_ids: (String, String),
) -> PyResult<BTreeMap<String, (TestRegression, SubtestRegression, Labels)>> {
    let results_cache = interop::results_cache::get(&results_repo).map_err(Error::from)?;
    let (_, metadata) =
        interop::metadata::load_metadata(&metadata_repo_path, None).map_err(Error::from)?;
    let base_results = results_cache
        .results(&run_ids.0, None)
        .map_err(Error::from)?;
    let comparison_results = results_cache
        .results(&run_ids.1, None)
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

#[pyclass]
struct GeckoRuns {
    #[pyo3(get)]
    push_date: chrono::NaiveDateTime,
    #[pyo3(get)]
    runs: BTreeMap<String, GeckoRun>,
}

impl From<interop::results_cache::GeckoRuns> for GeckoRuns {
    fn from(value: interop::results_cache::GeckoRuns) -> Self {
        let mut runs = BTreeMap::new();
        for (key, value) in value.runs.into_iter() {
            runs.insert(key, GeckoRun::from(value));
        }
        GeckoRuns {
            push_date: value.push_date,
            runs,
        }
    }
}

#[pyclass]
#[derive(Clone)]
struct GeckoRun {
    #[pyo3(get)]
    id: String,
    #[pyo3(get)]
    run_info: BTreeMap<String, Json>,
}

#[derive(Clone)]
struct Json(serde_json::Value);

impl<'py> IntoPyObject<'py> for Json {
    type Target = PyAny; // the Python type
    type Output = Bound<'py, Self::Target>; // in most cases this will be `Bound`
    type Error = PyErr; // the conversion error type, has to be convertable to `PyErr`

    fn into_pyobject(self, py: Python<'py>) -> Result<Self::Output, Self::Error> {
        Ok(serde_json_to_py(py, self.0)?.into_bound(py))
    }
}

impl From<interop::results_cache::GeckoRun> for GeckoRun {
    fn from(value: interop::results_cache::GeckoRun) -> Self {
        let mut run_info = BTreeMap::new();
        for (key, json_val) in value.run_info.into_iter() {
            run_info.insert(key, Json(json_val));
        }
        GeckoRun {
            id: value.id,
            run_info,
        }
    }
}

fn serde_json_to_py(py: Python<'_>, value: serde_json::Value) -> PyResult<PyObject> {
    let out_value = match value {
        serde_json::Value::Null => py.None().into_py_any(py),
        serde_json::Value::Bool(x) => x.into_py_any(py),
        serde_json::Value::Number(number) => {
            if number.is_i64() {
                number.as_i64().into_py_any(py)
            } else if number.is_u64() {
                number.as_u64().into_py_any(py)
            } else if number.is_f64() {
                number.as_f64().into_py_any(py)
            } else {
                Err(pyo3::exceptions::PyTypeError::new_err("Invalid number"))
            }
        }
        serde_json::Value::String(s) => s.into_py_any(py),
        serde_json::Value::Array(vec) => {
            let result = PyList::empty(py);
            for x in vec.into_iter() {
                result.append(serde_json_to_py(py, x)?)?;
            }
            result.into_py_any(py)
        }
        serde_json::Value::Object(map) => {
            let result = PyDict::new(py);
            for (key, x) in map.into_iter() {
                result.set_item(key, serde_json_to_py(py, x)?)?
            }
            result.into_py_any(py)
        }
    }?;
    out_value.into_py_any(py)
}

#[pyfunction]
#[pyo3(signature = (results_repo, branch, from_date, to_date=None))]
fn gecko_runs(
    results_repo: PathBuf,
    branch: String,
    from_date: chrono::NaiveDate,
    to_date: Option<chrono::NaiveDate>,
) -> PyResult<BTreeMap<chrono::NaiveDate, BTreeMap<String, GeckoRuns>>> {
    let results_cache =
        interop::results_cache::GeckoResultsCache::new(&results_repo).map_err(Error::from)?;
    let runs = results_cache
        .get_runs(&branch, from_date, to_date)
        .map_err(Error::from)?;
    let mut rv = BTreeMap::new();
    for (date, date_data) in runs.into_iter() {
        let mut date_result = BTreeMap::new();
        for (commit, commit_data) in date_data.into_iter() {
            date_result.insert(commit, GeckoRuns::from(commit_data));
        }
        rv.insert(date, date_result);
    }
    Ok(rv)
}

#[pymodule]
#[pyo3(name = "_wpt_interop")]
fn _wpt_interop(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(interop_score, m)?)?;
    m.add_function(wrap_pyfunction!(run_results, m)?)?;
    m.add_function(wrap_pyfunction!(score_runs, m)?)?;
    m.add_function(wrap_pyfunction!(interop_tests, m)?)?;
    m.add_function(wrap_pyfunction!(regressions, m)?)?;
    m.add_function(wrap_pyfunction!(gecko_runs, m)?)?;
    Ok(())
}
