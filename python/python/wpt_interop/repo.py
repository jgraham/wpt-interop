import logging
import os
import subprocess
from typing import Mapping, Optional

from . import _wpt_interop

logger = logging.getLogger("wpt_interop.repo")


class Repo:
    name: str
    remote: str
    bare: bool
    main_branch: Optional[str] = None
    fetch_tags: bool = False
    fetch_spec: Optional[list[str]] = None

    def __init__(self, path: Optional[str], repo_root: Optional[str]):
        if repo_root is None:
            repo_root = os.curdir
        if path is None:
            path = os.path.join(os.path.abspath(repo_root), self.name)
        self.path = path

    def git(self, command: str, *args: str) -> subprocess.CompletedProcess:
        cmd_args = ["git", command] + list(args)
        logger.info(f"Running {' '.join(cmd_args)}")
        try:
            complete = subprocess.run(cmd_args, cwd=self.path, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            logger.warning(f"{' '.join(cmd_args)} failed with exit status {e.returncode}")
            if e.stdout:
                logger.warning(f"Captured stdout:\n{e.stdout.decode('utf8', 'replace')}")
            if e.stderr:
                logger.warning(f"Captured stderr:\n{e.stderr.decode('utf8', 'replace')}")
            raise
        if complete.stdout:
            logger.info(f"Captured stdout:\n{complete.stdout.decode('utf8', 'replace')}")
        if complete.stderr:
            logger.info(f"Captured stderr:\n{complete.stderr.decode('utf8', 'replace')}")
        return complete

    def status(self, untracked: bool = False) -> bytes:
        untracked_mode = "all" if untracked else "no"
        args = ["--porcelain", f"--untracked-files={untracked_mode}"]
        complete = self.git("status", *args)
        return complete.stdout

    def has_staged(self) -> bool:
        status = self.status()
        for line in status.splitlines():
            if len(line) > 2 and line[2:3] == b" " and line[0:1] not in [b" ", b"?", b"!"]:
                return True
        return False

    def update(self, overwrite: bool = False) -> None:
        logger.info(f"Updating repo {self.name} {self.path} {os.path.exists(self.path)}")
        if not os.path.exists(self.path):
            logger.info("Repo doesn't exist, creating a new clone")
            os.makedirs(self.path)
            args = []
            if self.bare:
                args.append("--mirror")
            if self.fetch_tags:
                args.append("--tags")

            if self.remote is None:
                args += [self.path, "-b", "main"]
                self.git("init", *args)
            else:
                args.extend([self.remote, self.path])
                self.git("clone", *args)
        else:
            logger.info("Repo exists, fetching updates")
            args = []
            if not self.bare and self.fetch_tags:
                args.append("--tags")
            args.append(self.remote)
            if self.bare:
                args.append("+refs/heads/*:refs/heads/*")
                if self.fetch_tags:
                    args.append("+refs/tags/*:refs/tags/*")
                if self.fetch_spec:
                    args.extend(self.fetch_spec)
            else:
                assert self.main_branch is not None
                remotes = self.git("remote")
                if b"origin\n" not in remotes.stdout:
                    self.git("remote", "add", "origin", self.remote)
                args.append("+refs/heads/*:refs/remotes/origin/*")
                if self.fetch_spec:
                    args.extend(self.fetch_spec)
            self.git("fetch", *args)
            if self.main_branch is not None:
                try:
                    self.git("rev-parse", "--verify", self.main_branch)
                except subprocess.CalledProcessError:
                    self.git("checkout", "-b", self.main_branch, f"origin/{self.main_branch}")

                self.git("checkout", self.main_branch)
                if overwrite:
                    self.git("reset", "--hard", f"origin/{self.main_branch}")
                else:
                    self.git("merge", "--ff-only", f"origin/{self.main_branch}")

    def clean(self) -> None:
        if self.bare:
            raise ValueError("Can't clean bare repository")
        if not os.path.exists(self.path) or not os.path.exists(os.path.join(self.path, ".git")):
            return
        if len(self.status(untracked=True)):
            logger.info(f"Cleaning repository checkout {self.path}")
            try:
                self.git("checkout", "HEAD", "--")
            except subprocess.CalledProcessError:
                # If this failed there probably isn't a HEAD commit
                pass
            self.git("reset", "--hard")
            self.git("clean", "-df")

    def commit(self, msg: str) -> None:
        if self.bare:
            raise ValueError("Can't commit in bare repository")
        self.git("status", "--porcelain")
        if self.has_staged():
            logger.info(f"Commiting changes to {self.name}")
            self.git("commit", "-m", msg)
        else:
            logger.info(f"No changes in {self.name}")


class ResultsAnalysisCache(Repo): ...


class WptResultsAnalysisCache(ResultsAnalysisCache):
    name = "results-analysis-cache.git"
    remote = "https://github.com/web-platform-tests/results-analysis-cache.git"
    bare = True
    fetch_tags = True


class GeckoResultsAnalysisCache(ResultsAnalysisCache):
    name = "gecko-wpt-results.git"
    remote = "https://github.com/jgraham/gecko-results-cache.git"
    bare = True
    fetch_tags = True
    fetch_spec = ["+refs/runs/*:refs/runs/*"]


class Metadata(Repo):
    name = "wpt-metadata.git"
    remote = "https://github.com/web-platform-tests/wpt-metadata.git"
    bare = True

    def __init__(self, path: Optional[str], repo_root: Optional[str]):
        super().__init__(path, repo_root)
        self._tests = None

    def tests_by_category(
        self, labels_by_category: Mapping[str, set[str]], metadata_revision: Optional[str] = None
    ) -> tuple[str, Mapping[str, set[str]], set[str]]:
        return _wpt_interop.interop_tests(self.path, labels_by_category, metadata_revision)
