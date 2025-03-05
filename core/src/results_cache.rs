use crate::{Error, Result, Results};
use chrono::{self, NaiveDate};
use core::str;
use git2;
use serde_derive::Deserialize;
use serde_json;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use urlencoding;

pub trait ResultsCache {
    fn run_ref(&self, run_id: &str) -> String;
    fn repo(&self) -> &git2::Repository;
    fn results(
        &self,
        run_id: &str,
        include_tests: Option<&BTreeSet<String>>,
    ) -> Result<BTreeMap<String, Results>> {
        let repo = self.repo();
        let mut results_data = BTreeMap::new();
        let run_ref = repo.find_reference(&self.run_ref(run_id))?;

        let root = run_ref.peel_to_tree()?;
        let mut stack: Vec<(git2::Tree, String)> = vec![(root, "".to_string())];
        while let Some((tree, path)) = stack.pop() {
            for tree_entry in tree.iter() {
                match tree_entry.kind() {
                    Some(git2::ObjectType::Tree) => {
                        let name = tree_entry.name().ok_or_else(|| {
                            Error::String(format!("Tree has non-utf8 name {:?}", tree_entry.name()))
                        })?;
                        stack.push((
                            tree_entry.to_object(repo)?.peel_to_tree()?,
                            format!("{}/{}", path, name),
                        ));
                    }
                    Some(git2::ObjectType::Blob) => {
                        let name = tree_entry.name().ok_or_else(|| {
                            Error::String(format!("Tree has non-utf8 name {:?}", tree_entry.name()))
                        })?;
                        let test_name = match name.rsplit_once('.') {
                            Some((test_name, "json")) => urlencoding::decode(test_name),
                            Some((_, _)) | None => {
                                return Err(Error::String(format!(
                                    "Expected a name ending .json(), got {}",
                                    name
                                )));
                            }
                        }
                        .expect("Test name is valid utf8");
                        let path = format!("{}/{}", path, test_name);
                        if let Some(include) = include_tests {
                            if !include.contains(&path) {
                                continue;
                            }
                        }
                        let blob = tree_entry.to_object(repo)?.peel_to_blob()?;
                        let results: Results = serde_json::from_slice(blob.content())?;
                        results_data.insert(path, results);
                    }
                    _ => {
                        return Err(Error::String(format!(
                            "Unexpected object while walking tree {}",
                            tree_entry.id()
                        )));
                    }
                }
            }
        }
        Ok(results_data)
    }
}

pub struct WptfyiResultsCache {
    repo: git2::Repository,
}

impl WptfyiResultsCache {
    pub fn new(path: &Path) -> Result<WptfyiResultsCache> {
        Ok(WptfyiResultsCache {
            repo: git2::Repository::open(path)?,
        })
    }
}

impl ResultsCache for WptfyiResultsCache {
    fn run_ref(&self, run_id: &str) -> String {
        format!("refs/tags/run/{}/results", run_id)
    }

    fn repo(&self) -> &git2::Repository {
        &self.repo
    }
}

#[derive(Debug, Deserialize)]
pub struct GeckoRuns {
    pub push_date: chrono::NaiveDateTime,
    pub runs: BTreeMap<String, GeckoRun>,
}

#[derive(Debug, Deserialize)]
pub struct GeckoRun {
    pub id: String,
    pub run_info: BTreeMap<String, serde_json::Value>,
}

pub struct GeckoResultsCache {
    repo: git2::Repository,
}

impl GeckoResultsCache {
    pub fn new(path: &Path) -> Result<GeckoResultsCache> {
        Ok(GeckoResultsCache {
            repo: git2::Repository::open(path)?,
        })
    }
}

impl GeckoResultsCache {
    pub fn get_runs(
        self,
        branch: &str,
        from_date: NaiveDate,
        to_date: Option<NaiveDate>,
    ) -> Result<BTreeMap<NaiveDate, BTreeMap<String, GeckoRuns>>> {
        let repo = self.repo();
        let index_tree = self
            .repo
            .find_reference("refs/runs/index")?
            .peel_to_tree()?;
        let mut date = from_date;
        let mut rv = BTreeMap::new();
        let last_date = to_date.unwrap_or_else(|| chrono::Utc::now().date_naive());
        while date <= last_date {
            let date_str = date.format("%Y-%m-%d");
            let date_path = PathBuf::from(format!("runs/{}/{}/revision/", branch, date_str));
            let mut date_entries = BTreeMap::new();
            if let Ok(tree_entry) = index_tree.get_path(&date_path) {
                let commit_tree = tree_entry.to_object(repo)?.peel_to_tree()?;
                for commit_entry in commit_tree.iter() {
                    if let Some(name) = commit_entry.name() {
                        if !name.ends_with(".json") {
                            continue;
                        }
                        let commit = &name[..name.len() - 5];
                        if let Ok(commit_blob) = commit_entry.to_object(repo)?.peel_to_blob() {
                            let commit_entries: GeckoRuns =
                                serde_json::from_slice(commit_blob.content())?;
                            date_entries.insert(commit.into(), commit_entries);
                        }
                    }
                }
                rv.insert(date, date_entries);
            }
            if let Some(next_date) = date.succ_opt() {
                date = next_date
            } else {
                // This should be unreachable
                break;
            }
        }
        Ok(rv)
    }
}

impl ResultsCache for GeckoResultsCache {
    fn run_ref(&self, run_id: &str) -> String {
        format!("refs/runs/{}/results", run_id)
    }

    fn repo(&self) -> &git2::Repository {
        &self.repo
    }
}

pub fn get(results_repo: &Path) -> Result<Box<dyn ResultsCache>> {
    let repo = git2::Repository::open(results_repo)?;
    if repo.find_reference("refs/runs/index").is_ok() {
        Ok(Box::new(GeckoResultsCache::new(results_repo)?))
    } else {
        Ok(Box::new(WptfyiResultsCache::new(results_repo)?))
    }
}
