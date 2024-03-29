"""Module to generate the changelog on a release of a version."""

import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any, NamedTuple, Union, cast

import github

from github_app_geo_project import module
from github_app_geo_project.module.standard import changelog_configuration

_LOGGER = logging.getLogger(__name__)


class Author:
    """Author of a pull request or commit."""

    def __init__(self, name: str, url: str):
        """Create an author."""
        self.name = name
        self.url = url

    def __eq__(self, other: object) -> bool:
        """Check if two authors are equals."""
        if not isinstance(other, Author):
            return NotImplemented
        return self.name == other.name

    def __hash__(self) -> int:
        """Get the hash of the author."""
        return hash(self.name)

    def markdown(self) -> str:
        """Convert an author to a markdown string."""
        if self.name.endswith("[bot]"):
            return f"[@{self.name}]({self.url})"
        return f"@{self.name}"


class ChangelogItem(NamedTuple):
    """Changelog item (pull request or commit."""

    github: github.PullRequest.PullRequest | github.Commit.Commit
    ref: str
    title: str
    author: Author
    authors: set[Author]
    branch: str | None
    files: set[str]
    labels: set[str]

    def __eq__(self, other: object) -> bool:
        """Check if two changelog items are equals."""
        if not isinstance(other, ChangelogItem):
            return NotImplemented
        return self.ref == other.ref

    def __hash__(self) -> int:
        """Get the hash of the changelog item."""
        return hash(self.ref)


Condition = Union[
    changelog_configuration.ConditionConst,
    changelog_configuration.ConditionAndSolidusOr,
    changelog_configuration.ConditionNot,
    changelog_configuration.ConditionLabel,
    changelog_configuration.ConditionFiles,
    changelog_configuration.ConditionAuthor,
    changelog_configuration.ConditionTitle,
    changelog_configuration.ConditionBranch,
]


def match(item: ChangelogItem, condition: Condition) -> bool:
    """Changelog item match with the condition."""
    match_functions: dict[str, Callable[[ChangelogItem, Condition], bool]] = {
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
    return condition["value"] == item.author.name


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

    def __init__(self, tag_str: str | None = None, tag: github.Tag.Tag | None = None):
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

    def __eq__(self, other: object) -> bool:
        """Compare two tags."""
        if not isinstance(other, Tag):
            return NotImplemented
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


def _previous_tag(tag: Tag, tags: dict[Tag, Tag]) -> Tag | None:
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


def get_release(tag: github.Tag.Tag) -> github.GitRelease.GitRelease | None:
    """Get the release from the tag."""
    for release in tag.get_repo().get_releases():  # type: ignore[attr-defined]
        if release.tag_name == tag.name:
            return release  # type: ignore[no-any-return]
    return None


def generate_changelog(
    github_application: github.Github,
    configuration: changelog_configuration.Changelog,
    repository: str,
    tag_str: str,
    milestone: github.Milestone.Milestone,
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
            authors = {Author(pull_request.user.login, pull_request.user.html_url)}
            for commit_ in pull_request.get_commits():
                authors.add(Author(commit_.author.login, commit_.author.html_url))
            pull_request.as_issue().edit(milestone=milestone)
            changelog_items.add(
                ChangelogItem(
                    github=pull_request,
                    ref=f"#{pull_request.number}",
                    title=pull_request.title,
                    author=Author(pull_request.user.login, pull_request.user.html_url),
                    authors=authors,
                    branch=pull_request.base.ref,
                    files={github_file.filename for github_file in pull_request.get_files()},
                    labels={label.name for label in pull_request.get_labels()},
                )
            )
        if not has_pr:
            changelog_items.add(
                ChangelogItem(
                    github=commit,
                    ref=commit.sha,
                    title=commit.commit.message.split("\n")[0],
                    author=Author(commit.author.login, commit.author.html_url),
                    authors={Author(commit.author.login, commit.author.html_url)},
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
    result = [f"# {tag.major}.{tag.minor}.{tag.patch} ({created:%Y-%m-%d})", ""]
    for section_config in configuration["sections"]:
        if section_config["name"] not in sections:
            continue
        if section_config.get("closed", False):
            result += ["<details><summary>", "", f"## {section_config['title']}", "</summary>"]
        else:
            result.append(f"## {section_config['title']}")
        result.append("")
        result.append(section_config.get("description", ""))
        result.append("")
        for item in sections[section_config["name"]]:
            item_authors = [item.author]
            item_authors.extend(a for a in item.authors if a != item.author)
            authors_str = [a.markdown() for a in item_authors]
            result.append(f"- {item.ref} {item.title} ({', '.join(authors_str)})")
        result.append("")
        if section_config.get("closed", False):
            result.append("</details>")

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
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Changelog"

    def get_actions(self, context: module.GetActionContext) -> list[module.Action]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        event_data = context.event_data
        if "release" in event_data and event_data.get("action") == "created":
            return [module.Action(priority=module.PRIORITY_STATUS, data={"type": "release"})]
        if event_data.get("ref_type") == "tag":
            return [module.Action(priority=module.PRIORITY_STATUS, data={"type": "tag"})]
        if (
            event_data.get("action") == "edited"
            and event_data.get("pull_request", {}).get("state") == "closed"
            and event_data.get("pull_request", {}).get("milestone")
        ):
            return [module.Action(priority=module.PRIORITY_STATUS, data={"type": "pull_request"})]

        return []

    def process(self, context: module.ProcessContext[changelog_configuration.Changelog]) -> None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        repository = cast(str, context.event_data.get("repository", {}).get("full_name"))
        repo = context.github_project.github.get_repo(repository)

        if context.module_config.get("create-labels", changelog_configuration.CREATE_LABELS_DEFAULT):
            existing_labels = {label.name for label in repo.get_labels()}
            for label, config in context.module_config.get("labels", {}).items():
                if label not in existing_labels:
                    repo.create_label(
                        name=label,
                        color=config["color"],
                        description=config.get("description", ""),
                    )

        assert isinstance(repository, str)
        tag_str = ""
        milestone = None
        release = None
        if context.module_data.get("type") == "tag":
            if not context.module_config.get(
                "create-release", changelog_configuration.CREATE_RELEASE_DEFAULT
            ):
                return
            tag_str = cast(str, context.event_data["ref"])
            release = repo.create_git_release(tag_str, tag_str, "")
        elif context.module_data.get("type") == "release":
            if context.module_config.get("create-release", changelog_configuration.CREATE_RELEASE_DEFAULT):
                return
            tag_str = context.event_data.get("release", {}).get("tag_name")
            release = repo.get_release(tag_str)
        elif context.module_data.get("type") == "pull_request":
            # Get the milestone
            tag_str = context.event_data.get("pull_request", {}).get("milestone", {}).get("title")
            release = repo.get_release(tag_str)
            tag = [tag for tag in repo.get_tags() if tag.name == tag_str][0]
            if tag is None:
                _LOGGER.info(
                    "No tag found via the milestone for pull request %s on repository %s",
                    context.event_data.get("pull_request", {}).get("number"),
                    repository,
                )
                return

        if milestone is None:
            milestones = [m for m in repo.get_milestones() if m.title == tag_str]
            if milestones:
                milestone = milestones[0]
            else:
                milestone = repo.create_milestone(tag_str)

        assert release is not None
        release.update_release(
            tag_str,
            tag_name=tag_str,
            message=generate_changelog(
                context.github_project.github, context.module_config, repository, tag_str, milestone
            ),
        )

    def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema of the module configuration."""
        # Get changelog-schema.json related to this file
        with open(
            os.path.join(os.path.dirname(__file__), "changelog-schema.json"), encoding="utf-8"
        ) as schema_file:
            return json.loads(schema_file.read()).get("properties", {}).get("changelog")  # type: ignore[no-any-return]

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the permissions and events required by the module."""
        return module.GitHubApplicationPermissions(
            {
                "contents": "read",
                "pull_requests": "write",
                "issues": "write",
            },
            {"create", "pull_request", "release"},
        )
