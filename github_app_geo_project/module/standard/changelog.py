import json
import logging
import os
import re
import subprocess
import tempfile
from typing import NamedTuple, Optional, Union

import github
import yaml

from github_app_geo_project import module
from github_app_geo_project.module.standard import changelog_configuration

_LOGGER = logging.getLogger(__name__)
_CONFIG = yaml.safe_load(
    r"""
enabled: true
default-group: New feature
create-labels: true
groups-conditions:
# Label
  - group-name: Breaking changes
    condition:
        type: label
        value: Breaking changes
  - group-name: New feature
    condition:
        type: label
        value: New feature
  - group-name: Fixed bugs
    condition:
        type: label
        value: Fixed bugs
  - group-name: Documentation
    condition:
        type: label
        value: Documentation
  - group-name: Tests
    condition:
        type: label
        value: Tests
  - group-name: Chore
    condition:
        type: label
        value: Chore
  - group-name: Security fixes
    condition:
        type: label
        value: Security fixes
  - group-name: Dependency update
    condition:
        type: label
        value: Dependency update
  # Other
  - group-name: Documentation
    condition:
        type: files
        regex:
            - .*\.rst$
            - .*\.md$
            - .*\.rst\.[a-z0-9]{2,6}$
            - .*\.md\.[a-z0-9]{2,6}$
            - ^docs?/.*
  - group-name: Chore
    condition:
        type: files
        regex:
            - ^\.github/.*
            - ^ci/.*
  - group-name: Chore
    condition:
        type: title
        regex: ^CI updates$
  - group-name: Security fixes
    condition:
        type: branch
        regex: ^audit-.*
  - group-name: Security fixes
    condition:
        type: and
        conditions:
            - type: branch
              regex: ^dpkg-update/.*
            - type: author
              value: c2c-gid-bot-ci
  - group-name: Security fixes
    condition:
        type: branch
        regex: ^snyk-fix/.*
  - group-name: Dependency update
    condition:
        type: author
        value: renovate[bot]"""
)


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


def match(pr: ChangelogItem, condition: changelog_configuration.Condition) -> bool:
    match_functions = {
        "and": match_and,
        "or": match_or,
        "not": match_not,
        "const": match_const,
        "title": match_title,
        "files": match_files,
        "label": match_label,
        "branch": match_branch,
        "author": match_author,
    }
    if condition["type"] not in match_functions:
        return False
    return match_functions[condition["type"]](pr, condition)


def match_and(pull_request: ChangelogItem, condition: changelog_configuration.ConditionAndSolidusOr) -> bool:
    for c in condition["conditions"]:
        if not match(pull_request, c):
            return False
    return True


def match_or(pull_request: ChangelogItem, condition: changelog_configuration.ConditionAndSolidusOr) -> bool:
    for condition in condition["conditions"]:
        if match(pull_request, condition):
            return True
    return False


def match_not(pr: ChangelogItem, condition: changelog_configuration.ConditionNot) -> bool:
    return not match(pr, condition["condition"])


def match_const(pr: ChangelogItem, condition: changelog_configuration.ConditionConst) -> bool:
    return condition["value"]


def match_title(pr: ChangelogItem, condition: changelog_configuration.ConditionTitle) -> bool:
    return re.match(condition["regex"], pr.title) is not None


def match_files(pr: ChangelogItem, condition: changelog_configuration.ConditionFiles) -> bool:
    file_re = re.compile("|".join(condition["regex"]))
    for f in pr.files:
        if file_re.match(f) is None:
            return False
    return True


def match_label(pr: ChangelogItem, condition: changelog_configuration.ConditionLabel) -> bool:
    return condition["value"] in pr.labels


def match_branch(pr: ChangelogItem, condition: changelog_configuration.ConditionBranch) -> bool:
    if not pr.branch:
        return False
    return re.match(condition["regex"], pr.branch) is not None


def match_author(pr: ChangelogItem, condition: changelog_configuration.ConditionAuthor) -> bool:
    return condition["value"] == pr.author


def get_section(pr: ChangelogItem, config: changelog_configuration.Changelog) -> str:
    group = config["default-section"]
    for group_condition in config["routing"]:
        if match(pr, group_condition["condition"]):
            group = group_condition["section"]
            if not group_condition.get("continue", False):
                return group
    return group


class Tag:
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
            self.major = tag_match.group(1)
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


def get_labels(config: changelog_configuration.Condition) -> set[str]:
    if config["type"] in ("and", "or"):
        labels = set()
        for c in config["conditions"]:  # type: ignore[typeddict-item]
            labels.update(get_labels(c))
        return labels
    if config["type"] == "not":
        return get_labels(config["condition"])
    if config["type"] == "label":
        return {config["value"]}
    return set()


def get_release(tag: github.Tag.Tag) -> Optional[github.GitRelease.GitRelease]:
    for release in tag.get_repo().get_releases():
        if release.tag_name == tag.name:
            return release
    return None


def get_pull_request_tags(
    repo: github.Repository.Repository, pull_request_number: int, tags: Optional[dict[Tag, Tag]] = None
) -> Optional[Tag]:
    """
    Get the tags that contains the merge commit of the pull request.
    """
    pr = repo.get_pull(pull_request_number)
    # created temporary directory
    with tempfile.TemporaryDirectory() as tmp_directory_name:
        os.chdir(tmp_directory_name)
        subprocess.run(["git", "clone", repo.clone_url], check=True)
        os.chdir(os.path.join(tmp_directory_name, repo.name))
        tags_str = (
            subprocess.run(
                ["git", "tag", "--contains", pr.merge_commit_sha], stdout=subprocess.PIPE, check=True
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
    labels = set()
    for c in configuration["routing"]:
        labels.update(get_labels(c["condition"]))

    repo = github_application.get_repo(repository)

    tags: dict[Tag, Tag] = {}
    for tag_s in repo.get_tags():
        try:
            tag = Tag(tag=tag_s)
            tags[tag] = tag
        except ValueError:
            _LOGGER.warning(f"Invalid tag: %s on repository %s", tag_s, repository)
            continue

    tag = Tag(tag_str)
    if tag not in tags:
        _LOGGER.warning(f"Tag %s not found on repository %s", tag_str, repository)
        return ""
    tag = tags[tag]
    old_tag = _previous_tag(tag, tags)
    if old_tag is None:
        _LOGGER.warning(f"No previous tag found for tag %s on repository %s", tag_str, repository)
        return ""

    changelog_items: set[ChangelogItem] = set()

    # Get the commits between oldTag and tag
    assert old_tag.tag is not None
    assert tag.tag is not None
    for commit in repo.compare(old_tag.tag.name, tag.tag.name).commits:
        has_pr = False
        for pr in commit.get_pulls():
            has_pr = True
            authors = {pr.user.login}
            for c in pr.get_commits():
                authors.add(c.author.login)  # type: ignore[attr-defined]
            changelog_items.add(
                ChangelogItem(
                    object=pr,
                    ref=f"#{pr.number}",
                    title=pr.title,
                    author=pr.user.login,
                    authors=authors,
                    branch=pr.base.ref,
                    files={f.filename for f in pr.get_files()},
                    labels={l.name for l in pr.get_labels()},
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
            tag_str = context.event_data["ref"]  # type: ignore[index,assignment]
        elif context.event_data.get("type") == "release":
            tag_str = context.event_data["release"]["tag_name"]  # type: ignore[union-attr,index,assignment,call-overload]
        elif context.event_data.get("type") == "pull_request":
            # Get the milestone
            pr = context.github_application.get_repo(repository).get_pull(context.event_data.get("pull_request", {}).get("number"))  # type: ignore[union-attr,index,call-overload]
            if pr.milestone:
                tag_str = pr.milestone.title
            else:
                _LOGGER.warning(
                    "No milestone found for pull request %s on repository %s", pr.number, repository
                )
                return

        generate_changelog(context.github_application, context.module_config, repository, tag_str)

    def get_json_schema(self) -> module.JsonDict:
        """Get the JSON schema of the module configuration."""
        # Get changelog-schema.json related to this file
        with open(os.path.join(os.path.dirname(__file__), "changelog-schema.json")) as f:
            return json.loads(f.read()).get("properties", {}).get("changelog")  # type: ignore[no-any-return]
