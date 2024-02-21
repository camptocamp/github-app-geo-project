"""Module to generate the changelog on a release of a version."""

import json
import logging
import os
import re
import subprocess  # nosec
import tempfile
from typing import Callable, NamedTuple, Optional, Union

import github

from github_app_geo_project import module
from github_app_geo_project.module.standard import changelog_configuration

_LOGGER = logging.getLogger(__name__)


class ChangelogItem(NamedTuple):
    """Changelog item (pull request or commit."""

    object: Union[github.PullRequest.PullRequest, github.Commit.Commit]
    ref: str
    title: str
    author: str
    authors: set[str]
    branch: Optional[str]
    files: set[str]
    labels: set[str]

    def __hash__(self) -> int:
        """Get the hash of the changelog item."""
        return hash(self.ref)


def match(item: ChangelogItem, condition: changelog_configuration.Condition) -> bool:
    """Changelog item match with the condition."""
    match_functions: dict[str, Callable[[ChangelogItem, changelog_configuration.Condition], bool]] = {
        "and": match_and,  # type: ignore[dict-item]
        "or": match_or,  # type: ignore[dict-item]
        "not": match_not,  # type: ignore[dict-item]
        "const": match_const,  # type: ignore[dict-item]
        "title": match_title,  # type: ignore[dict-item]
        "files": match_files,  # type: ignore[dict-item]
        "label": match_label,  # type: ignore[dict-item]
        "branch": match_branch,  # type: ignore[dict-item]
        "author": match_author,  # type: ignore[dict-item]
    }
    if condition["type"] not in match_functions:
        _LOGGER.warning("Unknown condition type: %s", condition["type"])
        return False
    return match_functions[condition["type"]](item, condition)


def match_and(item: ChangelogItem, condition: changelog_configuration.ConditionAndSolidusOr) -> bool:
    """Match all the conditions."""
    for cond in condition["conditions"]:
        if not match(item, cond):
            return False
    return True


def match_or(item: ChangelogItem, condition: changelog_configuration.ConditionAndSolidusOr) -> bool:
    """Match any of the conditions."""
    for cond in condition["conditions"]:
        if match(item, cond):
            return True
    return False


def match_not(item: ChangelogItem, condition: changelog_configuration.ConditionNot) -> bool:
    """Get the opposite of the condition."""
    return not match(item, condition["condition"])


def match_const(item: ChangelogItem, condition: changelog_configuration.ConditionConst) -> bool:
    """Get a constant value."""
    del item
    return condition["value"]


def match_title(item: ChangelogItem, condition: changelog_configuration.ConditionTitle) -> bool:
    """Match the title of the pull request."""
    return re.match(condition["regex"], item.title) is not None


def match_files(item: ChangelogItem, condition: changelog_configuration.ConditionFiles) -> bool:
    """Match all the files of the pull request."""
    file_re = re.compile("|".join(condition["regex"]))
    for file_name in item.files:
        if file_re.match(file_name) is None:
            return False
    return True


def match_label(item: ChangelogItem, condition: changelog_configuration.ConditionLabel) -> bool:
    """Match a label of the pull request."""
    return condition["value"] in item.labels


def match_branch(item: ChangelogItem, condition: changelog_configuration.ConditionBranch) -> bool:
    """Match the branch of the pull request."""
    if not item.branch:
        return False
    return re.match(condition["regex"], item.branch) is not None


def match_author(item: ChangelogItem, condition: changelog_configuration.ConditionAuthor) -> bool:
    """Match the author of the pull request."""
    return condition["value"] == item.author


def get_section(item: ChangelogItem, config: changelog_configuration.Changelog) -> str:
    """Get the section of the changelog item."""
    group = config["default-section"]
    for group_condition in config["routing"]:
        if match(item, group_condition["condition"]):
            group = group_condition["section"]
            if not group_condition.get("continue", False):
                return group
    return group


class Tag:
    """A tag parsed as a semver version."""

    TAG_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
    TAG2_RE = re.compile(r"release_?(\d+)")

    def __init__(self, tag_str: Optional[str] = None, tag: Optional[github.Tag.Tag] = None):
        """Create a tag."""
        if tag_str is None:
            assert tag is not None
            assert tag.name is not None
            tag_str = tag.name
        self.tag_str = tag_str
        self.tag = tag
        tag_match = self.TAG_RE.match(tag_str)
        if tag_match is None:
            tag_match = self.TAG2_RE.match(tag_str)
            if tag_match is None:
                raise ValueError(f"Invalid tag: {tag_str}")
            self.major = int(tag_match.group(1))
            self.minor = 0
            self.patch = 0
        else:
            self.major, self.minor, self.patch = (int(e) for e in tag_match.groups())

    def __eq__(self, other: "Tag") -> bool:  # type: ignore[override]
        """Compare two tags."""
        return self.major == other.major and self.minor == other.minor and self.patch == other.patch

    def __hash__(self) -> int:
        """Get the hash of the tag."""
        return hash((self.major, self.minor, self.patch))

    def __lt__(self, other: "Tag") -> bool:
        """Compare two tags."""
        if self.major < other.major:
            return True
        if self.major > other.major:
            return False
        if self.minor < other.minor:
            return True
        if self.minor > other.minor:
            return False
        return self.patch < other.patch

    def __gt__(self, other: "Tag") -> bool:
        """Compare two tags."""
        if self.major > other.major:
            return True
        if self.major < other.major:
            return False
        if self.minor > other.minor:
            return True
        if self.minor < other.minor:
            return False
        return self.patch > other.patch

    def __cmp__(self, other: "Tag") -> int:
        """Compare two tags."""
        if self < other:
            return -1
        if self > other:
            return 1
        return 0


def _previous_tag(tag: Tag, tags: dict[Tag, Tag]) -> Optional[Tag]:
    if tag.patch != 0:
        test_tag = Tag(".".join(str(e) for e in (tag.major, tag.minor, tag.patch - 1)))
        if test_tag in tags:
            return tags[test_tag]
        return _previous_tag(test_tag, tags)
    if tag.minor != 0:
        test_tag = Tag(".".join(str(e) for e in (tag.major, tag.minor - 1, 0)))
        if test_tag in tags:
            return tags[test_tag]
        return _previous_tag(test_tag, tags)
    if tag.major != 0:
        test_tag = Tag(".".join(str(e) for e in (tag.major - 1, 0, 0)))
        if test_tag in tags:
            return tags[test_tag]
        return _previous_tag(test_tag, tags)
    return None


def get_release(tag: github.Tag.Tag) -> Optional[github.GitRelease.GitRelease]:
    """Get the release from the tag."""
    for release in tag.get_repo().get_releases():  # type: ignore[attr-defined]
        if release.tag_name == tag.name:
            return release  # type: ignore[no-any-return]
    return None


def get_pull_request_tags(
    repo: github.Repository.Repository, pull_request_number: int, tags: Optional[dict[Tag, Tag]] = None
) -> Optional[Tag]:
    """
    Get the tags that contains the merge commit of the pull request.
    """
    pull_request = repo.get_pull(pull_request_number)
    # TODO: use milestone
    # Created temporary directory
    with tempfile.TemporaryDirectory() as tmp_directory_name:
        os.chdir(tmp_directory_name)
        subprocess.run(["git", "clone", repo.clone_url], check=True)  # nosec
        os.chdir(os.path.join(tmp_directory_name, repo.name))
        tags_str = (
            subprocess.run(  # nosec
                ["git", "tag", "--contains", pull_request.merge_commit_sha],
                stdout=subprocess.PIPE,
                check=True,
            )
            .stdout.decode()
            .split("\n")
        )
        found_tags = []
        for tag in tags_str:
            if tag:
                try:
                    found_tags.append(Tag(tag))
                except ValueError:
                    pass

    if not found_tags:
        return None
    found_tags.sort()
    found_tag = found_tags[0]
    if tags and found_tag in tags:
        return tags[found_tag]
    return found_tag


def generate_changelog(
    github_application: github.Github,
    configuration: changelog_configuration.Changelog,
    repository: str,
    tag_str: str,
) -> str:
    """Generate the changelog for a tag."""
    repo = github_application.get_repo(repository)

    tags: dict[Tag, Tag] = {}
    for tag_s in repo.get_tags():
        try:
            tag = Tag(tag=tag_s)
            tags[tag] = tag
        except ValueError:
            _LOGGER.warning("Invalid tag: %s on repository %s", tag_s, repository)
            continue

    tag = Tag(tag_str)
    if tag not in tags:
        _LOGGER.warning("Tag %s not found on repository %s", tag_str, repository)
        return ""
    tag = tags[tag]
    old_tag = _previous_tag(tag, tags)
    if old_tag is None:
        _LOGGER.warning("No previous tag found for tag %s on repository %s", tag_str, repository)
        return ""

    changelog_items: set[ChangelogItem] = set()

    # Get the commits between oldTag and tag
    assert old_tag.tag is not None
    assert tag.tag is not None
    for commit in repo.compare(old_tag.tag.name, tag.tag.name).commits:
        has_pr = False
        for pull_request in commit.get_pulls():
            has_pr = True
            authors = {pull_request.user.login}
            for commit_ in pull_request.get_commits():
                authors.add(commit_.author.login)
            changelog_items.add(
                ChangelogItem(
                    object=pull_request,
                    ref=f"#{pull_request.number}",
                    title=pull_request.title,
                    author=pull_request.user.login,
                    authors=authors,
                    branch=pull_request.base.ref,
                    files={github_file.filename for github_file in pull_request.get_files()},
                    labels={label.name for label in pull_request.get_labels()},
                )
            )
        if not has_pr:
            changelog_items.add(
                ChangelogItem(
                    object=commit,
                    ref=commit.sha,
                    title=commit.commit.message.split("\n")[0],
                    author=commit.author.login,
                    authors={commit.author.login},
                    branch=commit.committer.login,
                    files={f.filename for f in commit.files},
                    labels=set(),
                )
            )

    sections: dict[str, list[ChangelogItem]] = {}
    for item in changelog_items:
        section = get_section(item, configuration)
        sections.setdefault(section, []).append(item)

    created = tag.tag.commit.commit.author.date
    result = [f"#{tag.major}.{tag.minor}.{tag.patch} ({created:%Y-%m-%d})", ""]
    for section_config in configuration["sections"]:
        if section_config["name"] not in sections:
            continue
        result.append(f"## {section_config['title']}")
        result.append("")
        result.append(section_config.get("description", ""))
        result.append("")
        for item in sections[section_config["name"]]:
            item_authors = [item.author]
            item_authors.extend(a for a in item.authors if a != item.author)
            authors_str = [f"@{a}" for a in item_authors]
            result.append(f"- {item.ref} **{item.title}** ({', '.join(authors_str)})")
        result.append("")
    return "\n".join(result)


class Changelog(module.Module[changelog_configuration.Changelog]):
    """The changelog module."""

    def title(self) -> str:
        """Get the title of the module."""
        return "Generate Changelog in Release"

    def description(self) -> str:
        """Get the description of the module."""
        return "Generate the changelog of the release based on the pull requests merged and commits present in the release."

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return ""

    def get_actions(self, event_data: module.JsonDict) -> list[module.Action]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if event_data.get("type") == "release" and event_data.get("action") == "created":
            return [module.Action(priority=module.PRIORITY_STATUS)]
        if event_data.get("type") == "tag" and event_data.get("action") == "created":
            return [module.Action(priority=module.PRIORITY_STATUS)]
        if (
            event_data.get("type") == "pull_request"
            and event_data.get("action") == "edited"
            and event_data.get("pull_request", {}).get("state") == "closed"  # type: ignore[union-attr]
        ):
            return [module.Action(priority=module.PRIORITY_STATUS)]

        return []

    def process(self, context: module.ProcessContext[changelog_configuration.Changelog]) -> None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        # TODO
        # - get the tag from tag event
        # - get the tag from release event
        # - get the tag from pull request event (throe milestone)
        # - fill (create) the release
        # - fill (create) the milestone
        repository = context.event_data.get("repository", {}).get("full_name")  # type: ignore[union-attr]
        assert isinstance(repository, str)
        tag_str = ""
        if context.event_data.get("type") == "tag":
            tag_str = context.event_data["ref"]  # type: ignore[assignment]
        elif context.event_data.get("type") == "release":
            tag_str = context.event_data.get("release", {}).get("tag_name")  # type: ignore[assignment,union-attr]
        elif context.event_data.get("type") == "pull_request":
            # Get the milestone
            pull_request_number = context.event_data.get("pull_request", {}).get("number")  # type: ignore[union-attr]
            assert isinstance(pull_request_number, int)
            pull_request = context.github_application.get_repo(repository).get_pull(pull_request_number)
            if pull_request.milestone:
                tag_str = pull_request.milestone.title
            else:
                _LOGGER.warning(
                    "No milestone found for pull request %s on repository %s", pull_request.number, repository
                )
                return

        generate_changelog(context.github_application, context.module_config, repository, tag_str)

    def get_json_schema(self) -> module.JsonDict:
        """Get the JSON schema of the module configuration."""
        # Get changelog-schema.json related to this file
        with open(
            os.path.join(os.path.dirname(__file__), "changelog-schema.json"), encoding="utf-8"
        ) as schema_file:
            return json.loads(schema_file.read()).get("properties", {}).get("changelog")  # type: ignore[no-any-return]
