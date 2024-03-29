use crate::{Error, Result};
use git2;
use serde_derive::Deserialize;
use serde_yaml;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use crate::TestStatus;

#[derive(Debug, Deserialize)]
struct MetadataFile {
    links: Vec<MetadataEntry>,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum MetadataEntry {
    Label(MetadataLabelEntry),
    Link(MetadataLinkEntry),
}

#[derive(Debug, Deserialize)]
struct MetadataLabelEntry {
    label: String,
    url: Option<String>,
    results: Vec<MetadataLabelResult>,
}

#[derive(Debug, Deserialize)]
struct MetadataLinkEntry {
    url: String,
    product: Option<String>,
    results: Vec<MetadataLinkResult>,
}

#[derive(Debug, Deserialize)]
struct MetadataLabelResult {
    test: String,
    status: Option<TestStatus>,
}

#[derive(Debug, Deserialize)]
struct MetadataLinkResult {
    test: String,
    subtest: Option<String>,
    status: Option<TestStatus>,
}

#[derive(Debug)]
pub struct Metadata {
    pub revision: String,
    data: BTreeMap<String, PathMetadata>,
}

impl Metadata {
    fn new(revision: String) -> Metadata {
        Metadata {
            revision,
            data: BTreeMap::new(),
        }
    }

    fn file_path(path: &str, test: &str) -> String {
        let mut test_path = String::from(path);
        test_path.push('/');
        test_path.push_str(test);
        test_path
    }

    fn add_from_file(&mut self, path: &str, metadata_file: MetadataFile) {
        for metadata_entry in metadata_file.links.iter() {
            match metadata_entry {
                MetadataEntry::Label(entry) => {
                    for result in entry.results.iter() {
                        let test_path = Metadata::file_path(path, &result.test);
                        let path_metadata = self.data.entry(test_path).or_default();
                        path_metadata.labels.insert(entry.label.clone());
                        if let Some(ref url) = entry.url {
                            path_metadata.links.insert(url.clone(), Vec::new());
                        }
                    }
                }
                MetadataEntry::Link(entry) => {
                    for result in entry.results.iter() {
                        let test_path = Metadata::file_path(path, &result.test);
                        let path_metadata = self.data.entry(test_path).or_default();
                        let result_filter = LinkFilter {
                            product: entry.product.clone(),
                            status: result.status,
                            subtest: result.subtest.clone(),
                        };
                        let link_metadata =
                            path_metadata.links.entry(entry.url.clone()).or_default();
                        link_metadata.push(result_filter)
                    }
                }
            }
        }
    }

    pub fn patterns_by_label(
        &self,
        filter_labels: Option<&BTreeSet<String>>,
    ) -> BTreeMap<&str, BTreeSet<&str>> {
        let mut by_label: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
        for (pattern, metadata) in self.data.iter() {
            for label in metadata.labels.iter() {
                if let Some(filters) = filter_labels {
                    if !filters.contains(label) {
                        continue;
                    }
                }
                by_label.entry(label).or_default().insert(pattern);
            }
        }
        by_label
    }
}

#[derive(Debug, Default)]
pub struct PathMetadata {
    pub labels: BTreeSet<String>,
    // Link from URL to filters
    pub links: BTreeMap<String, Vec<LinkFilter>>,
}

#[derive(Debug, Default)]
pub struct LinkFilter {
    pub product: Option<String>,
    pub status: Option<TestStatus>,
    pub subtest: Option<String>,
}

pub struct MetadataRepo {
    repo: git2::Repository,
}

impl MetadataRepo {
    pub fn new(path: &Path) -> Result<MetadataRepo> {
        Ok(MetadataRepo {
            repo: git2::Repository::open(path)?,
        })
    }

    pub fn head(&self) -> Result<git2::Commit> {
        Ok(self.repo.head()?.peel_to_commit()?)
    }

    pub fn get_commit(&self, revision: &str) -> Result<git2::Commit> {
        let oid = git2::Oid::from_str(revision)?;
        Ok(self.repo.find_commit(oid)?)
    }

    pub fn read_metadata(&self, commit: &git2::Commit) -> Result<Metadata> {
        let mut metadata = Metadata::new(commit.id().to_string());
        let root = commit.tree()?;
        let mut stack: Vec<(git2::Tree, String)> = vec![(root, "".to_string())];
        while let Some((tree, path)) = stack.pop() {
            for tree_entry in tree.iter() {
                match tree_entry.kind() {
                    Some(git2::ObjectType::Tree) => {
                        let name = tree_entry.name().ok_or_else(|| {
                            Error::String(format!("Tree has non-utf8 name {:?}", tree_entry.name()))
                        })?;
                        if name.starts_with('.') {
                            continue;
                        }
                        stack.push((
                            tree_entry.to_object(&self.repo)?.peel_to_tree()?,
                            format!("{}/{}", path, name),
                        ));
                    }
                    Some(git2::ObjectType::Blob) => {
                        let name = tree_entry.name().ok_or_else(|| {
                            Error::String(format!("Tree has non-utf8 name {:?}", tree_entry.name()))
                        })?;
                        if name == "META.yml" {
                            let blob = tree_entry.to_object(&self.repo)?.peel_to_blob()?;
                            let metadata_file: MetadataFile =
                                serde_yaml::from_slice(blob.content())?;
                            metadata.add_from_file(&path, metadata_file);
                        }
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
        Ok(metadata)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    #[test]
    fn test_parse_metadata() {
        let repo = MetadataRepo::new(&PathBuf::from("/home/jgraham/develop/wpt-metadata")).unwrap();
        let metadata = repo.read_metadata(&repo.head().unwrap()).unwrap();
        println!("{:?}", metadata);
    }
}
