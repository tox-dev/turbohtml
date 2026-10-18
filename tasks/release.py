"""
Cut a turbohtml release: build the changelog, commit, tag, and push.

The tag push is what triggers the publish workflow, so this script stops once the
annotated tag is on the upstream ``main``. It mirrors the tox-dev release flow and
is invoked by the "Prepare Release" GitHub Actions workflow via ``tox -e release``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from subprocess import check_call, run

from git import Commit, Head, Remote, Repo, TagReference
from packaging.version import Version

_ROOT = Path(__file__).resolve().parents[1]
_UPSTREAM_SLUG = "tox-dev/turbohtml"


def main(version_str: str) -> None:
    """Run the release for *version_str*."""
    version = Version(version_str)
    repo = Repo(str(_ROOT))
    if repo.is_dirty():
        msg = "the repository is dirty; commit or stash changes before releasing"
        raise RuntimeError(msg)

    upstream, release_branch = create_release_branch(repo, version)
    perform_release(repo, upstream, release_branch, version)


def create_release_branch(repo: Repo, version: Version) -> tuple[Remote, Head]:
    """Create a release-<version> branch off the upstream main."""
    upstream = next((remote for remote in repo.remotes if any(_UPSTREAM_SLUG in url for url in remote.urls)), None)
    if upstream is None:
        msg = f"could not find the {_UPSTREAM_SLUG} remote"
        raise RuntimeError(msg)
    upstream.fetch()
    branch = repo.create_head(f"release-{version}", upstream.refs.main)
    branch.checkout()
    return upstream, branch


def perform_release(repo: Repo, upstream: Remote, release_branch: Head, version: Version) -> None:
    """Build the changelog, commit, tag, push, and open the GitHub release."""
    release_commit = build_changelog(repo, version)
    tag = tag_release(repo, release_commit, version)
    repo.git.push(upstream.name, f"{release_branch}:main")
    repo.git.push(upstream.name, tag)
    create_github_release(version)
    repo.heads.main.checkout()
    repo.delete_head(release_branch, force=True)
    upstream.fetch()
    repo.git.reset("--hard", f"{upstream.name}/main")


def build_changelog(repo: Repo, version: Version) -> Commit:
    """Consume the news fragments into the changelog and commit the result."""
    check_call(["towncrier", "build", "--yes", "--version", version.public], cwd=str(_ROOT))
    changelog = _ROOT / "docs" / "changelog.rst"
    # Formatting can change the changelog on the first pass; a second failure stops the release.
    if run(["pre-commit", "run", "--files", str(changelog)], cwd=str(_ROOT), check=False).returncode:
        run(["pre-commit", "run", "--files", str(changelog)], cwd=str(_ROOT), check=True)
    repo.git.add("-A", "--", "docs/changelog", "docs/changelog.rst")
    return repo.index.commit(f"release {version}")


def tag_release(repo: Repo, release_commit: Commit, version: Version) -> TagReference:
    """Create the annotated tag whose push triggers the publish workflow."""
    if str(version) in {tag.name for tag in repo.tags}:
        msg = f"tag {version} already exists"
        raise RuntimeError(msg)
    return repo.create_tag(str(version), ref=release_commit, message=f"release {version}")


def create_github_release(version: Version) -> None:
    """Open the GitHub release with auto-generated notes."""
    run(
        ["gh", "release", "create", str(version), "--title", f"v{version}", "--generate-notes"],
        cwd=str(_ROOT),
        check=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="release", description=__doc__)
    parser.add_argument("--version", required=True)
    main(parser.parse_args().version)
