"""Module to generate the changelog on a release of a version."""

import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any, NamedTuple, Union, cast

import github
import packaging.version

from github_app_geo_project import module
from github_app_geo_project.module import utils
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
    author: Author | None
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
    return all(match(item, cond) for cond in condition["conditions"])


def match_or(item: ChangelogItem, condition: changelog_configuration.ConditionAndSolidusOr) -> bool:
    """Match any of the conditions."""
    return any(match(item, cond) for cond in condition["conditions"])


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
    return all(file_re.match(file_name) is not None for file_name in item.files)


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
    if item.author is None:
        return False
    return condition["value"] == item.author.name


class MatchedItem(NamedTuple):
    """Matched item."""

    item: ChangelogItem
    condition: str


def get_section(item: ChangelogItem, config: changelog_configuration.Changelog) -> tuple[str, MatchedItem]:
    """Get the section of the changelog item."""
    group = config["default-section"]
    for index, group_condition in enumerate(config["routing"]):
        if match(item, group_condition["condition"]):
            group = group_condition["section"]
            if not group_condition.get("continue", False):
                return group, MatchedItem(item, group_condition.get("name", str(index)))
    return group, MatchedItem(item, "default")


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
        # Get previous version
        tags_list = sorted([t for t in tags if t.major < tag.major])
        if not tags_list:
            return None
        previous_major_minor = tags_list[-1]

        # Get all patch for the previous major.minor version
        tags_list = sorted(
            [
                t
                for t in tags
                if t.major == previous_major_minor.major and t.minor == previous_major_minor.minor
            ]
        )
        # Return the first one
        return tags_list[0]
    return None


def get_release(tag: github.Tag.Tag) -> github.GitRelease.GitRelease | None:
    """Get the release from the tag."""
    for release in tag.get_repo().get_releases():  # type: ignore[attr-defined]
        if release.tag_name == tag.name:
            return release  # type: ignore[no-any-return]
    return None


def _get_discussion_url(repo: github.Repository.Repository, tag: str) -> str | None:
    requester = repo._requester  # pylint: disable=protected-access
    _, categories = requester.requestJsonAndCheck(
        "POST",
        requester.graphql_url,
        input={
            "query": """
        query DiscussionCategories($owner: String!, $name: String!) {
            repository(owner: $owner, name: $name) {
                discussionCategories(first: 10) {
                    nodes {
                        name
                        id
                    }
                }
            }
        }
        """,
            "variables": {
                "owner": repo.owner.login,
                "name": repo.name,
            },
        },
    )

    category = [
        c
        for c in categories.get("data", {})
        .get("repository", {})
        .get("discussionCategories", {})
        .get("nodes", [])
        if c.get("name") == "Announcements"
    ]
    if not category:
        return None
    _, discussions = requester.requestJsonAndCheck(
        "POST",
        requester.graphql_url,
        input={
            "query": """
        query Discussion($owner: String!, $name: String!, $category: ID!) {
            repository(owner: $owner, name: $name) {
                discussions(first: 10, categoryId: $category) {
                    nodes {
                        title
                        url
                    }
                }
            }
        }
        """,
            "variables": {
                "owner": repo.owner.login,
                "name": repo.name,
                "category": category[0]["id"],
            },
        },
    )
    discussions = discussions.get("data", {}).get("repository", {}).get("discussions", {}).get("nodes", [])
    discussion = [d for d in discussions if tag in d.get("title", "").split()]
    if not discussion:
        return None
    return cast(str, discussion[0]["url"])


def generate_changelog(
    github_application: github.Github,
    configuration: changelog_configuration.Changelog,
    repository: str,
    tag_str: str,
) -> str:
    """Generate the changelog for a tag."""
    repo = github_application.get_repo(repository)

    milestones = [m for m in repo.get_milestones() if m.title == tag_str]
    milestone = milestones[0] if milestones else repo.create_milestone(tag_str)

    discussion_url = _get_discussion_url(repo, tag_str)

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
            if pull_request.milestone is None or pull_request.milestone.number == milestone.number:
                authors = {Author(pull_request.user.login, pull_request.user.html_url)}
                for commit_ in pull_request.get_commits():
                    if commit_.author is not None:
                        authors.add(Author(commit_.author.login, commit_.author.html_url))
                pull_request.as_issue().edit(milestone=milestone)
                changelog_items.add(
                    ChangelogItem(
                        github=pull_request,
                        ref=f"#{pull_request.number}",
                        title=pull_request.title,
                        author=Author(pull_request.user.login, pull_request.user.html_url),
                        authors=authors,
                        branch=pull_request.head.ref,
                        files={github_file.filename for github_file in pull_request.get_files()},
                        labels={label.name for label in pull_request.get_labels()},
                    )
                )
        if not has_pr:
            author = Author(commit.author.login, commit.author.html_url) if commit.author else None
            changelog_items.add(
                ChangelogItem(
                    github=commit,
                    ref=commit.sha,
                    title=commit.commit.message.split("\n")[0],
                    author=author,
                    authors={author} if author else set(),
                    branch=None,
                    files={f.filename for f in commit.files},
                    labels=set(),
                )
            )

    sections: dict[str, list[MatchedItem]] = {}
    for item in changelog_items:
        section, matched_item = get_section(item, configuration)
        sections.setdefault(section, []).append(matched_item)

    message = []
    for section, items in sections.items():
        message.append(f"<h5>{section}</h5>")
        for matched_item in items:
            item = matched_item.item
            authors_message = ", ".join([a.name for a in item.authors])
            labels_message = ", ".join(item.labels)
            message.append(
                f"<p>- [{matched_item.condition}] {item.ref} {item.title} {item.author} ({authors_message}) {item.branch} {len(item.files)} - {labels_message}</p>"
            )
    message_obj = utils.HtmlMessage("\n".join(message))
    message_obj.title = f"Changelog for {tag.major}.{tag.minor}.{tag.patch}"
    _LOGGER.debug(message_obj)

    created = tag.tag.commit.commit.author.date
    result = [f"# {tag.major}.{tag.minor}.{tag.patch} ({created:%Y-%m-%d})", ""]
    if discussion_url:
        result.append(f"[See announcement]({discussion_url}).")
        result.append("")
    if milestone.description:
        result.append(milestone.description)
        result.append("")
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
        for matched_item in sections[section_config["name"]]:
            item = matched_item.item
            item_authors = [item.author] if item.author else []
            item_authors.extend(a for a in item.authors if a != item.author)
            authors_str = [a.markdown() for a in item_authors]
            result.append(f"- {item.ref} {item.title} ({', '.join(authors_str)})")
        result.append("")
        if section_config.get("closed", False):
            result.append("</details>")

    return "\n".join(result)


class Changelog(module.Module[changelog_configuration.Changelog, dict[str, Any], dict[str, Any]]):
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

    def jobs_unique_on(self) -> list[str] | None:
        """Indicate fields used to ship other jobs."""
        return ["repository", "owner", "module_data"]

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[dict[str, Any]]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        event_data = context.event_data
        if "release" in event_data and event_data.get("action") == "created":
            return [
                module.Action(
                    priority=module.PRIORITY_STATUS,
                    data={"version": event_data["release"]["tag_name"]},
                )
            ]
        if event_data.get("ref_type") == "tag":
            return [
                module.Action(
                    priority=module.PRIORITY_STATUS, data={"type": "tag", "version": event_data["ref"]}
                )
            ]
        if (
            event_data.get("action") in ("edited", "labeled", "unlabeled", "milestoned", "demilestoned")
            and event_data.get("pull_request", {}).get("state") == "closed"
            and event_data.get("sender", {}).get("login")
            != context.github_application.integration.get_app().slug + "[bot]"
        ):
            versions = set()
            milestone_version = event_data.get("milestone", {}).get("title")
            if milestone_version is not None:
                versions.add(milestone_version)
            pull_request_version = event_data.get("pull_request", {}).get("milestone", {}).get("title")
            if pull_request_version is not None:
                versions.add(pull_request_version)
            return [
                module.Action(
                    priority=module.PRIORITY_CRON,
                    data={"version": version},
                )
                for version in versions
            ]

        if (
            event_data.get("action") == "edited"
            and "milestone" in event_data
            and event_data.get("sender", {}).get("login")
            != context.github_application.integration.get_app().slug + "[bot]"
        ):
            versions = {event_data["milestone"]["title"]}
            if "changes" in event_data and "title" in event_data["changes"]:
                versions.add(event_data["changes"]["title"]["from"])

            return [
                module.Action(
                    priority=module.PRIORITY_CRON,
                    data={"version": version},
                )
                for version in versions
            ]
        if event_data.get("action") in ("created", "closed") and "discussion" in event_data:
            return [
                module.Action(
                    priority=module.PRIORITY_STATUS,
                    data={"type": "discussion"},
                )
            ]
        if event_data.get("action") == "edited" and "title" in event_data.get("changes", {}):
            return [
                module.Action(
                    priority=module.PRIORITY_STATUS,
                    data={"type": "discussion"},
                )
            ]

        return []

    async def process(
        self,
        context: module.ProcessContext[changelog_configuration.Changelog, dict[str, Any], dict[str, Any]],
    ) -> module.ProcessOutput[dict[str, Any], dict[str, Any]]:
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

        tag_str = cast(str, context.module_event_data.get("version"))
        if context.module_event_data.get("type") == "tag":
            if not context.module_config.get(
                "create-release", changelog_configuration.CREATE_RELEASE_DEFAULT
            ):
                return module.ProcessOutput()

            prerelease = False
            try:
                latest_release = repo.get_latest_release()
                if latest_release is not None:
                    prerelease = packaging.version.Version(tag_str) < packaging.version.Version(
                        latest_release.tag_name
                    )
            except github.UnknownObjectException as exception:
                if exception.status != 404:
                    raise
            repo.create_git_release(tag_str, tag_str, "", prerelease=prerelease)
            return module.ProcessOutput(
                actions=[
                    module.Action(
                        priority=module.PRIORITY_CRON,
                        data={"version": tag_str},
                    )
                ]
            )
        elif context.module_event_data.get("type") == "discussion":
            title = set()
            title.update(context.event_data.get("discussion", {}).get("title", "").split())
            if "title" in context.event_data.get("changes", {}):
                title.update(context.event_data["changes"]["title"]["from"].split())
            tags = [tag for tag in repo.get_tags() if tag.name in title]
            if not tags:
                _LOGGER.info(
                    "No tag found via for discussion %s on repository %s",
                    context.event_data.get("discussion", {}).get("title"),
                    repository,
                )
                return module.ProcessOutput()
            return module.ProcessOutput(
                actions=[
                    module.Action(
                        priority=module.PRIORITY_CRON,
                        data={"version": tags[0].name},
                    )
                ]
            )

        tags = [tag for tag in repo.get_tags() if tag.name == tag_str]
        if not tags:
            _LOGGER.info("No tag found '%s' on repository '%s'.", tag_str, repository)
            return module.ProcessOutput()

        release = repo.get_release(tag_str)
        assert release is not None
        release.update_release(
            tag_str,
            tag_name=tag_str,
            message=generate_changelog(
                context.github_project.github, context.module_config, repository, tag_str
            ),
        )
        return module.ProcessOutput()

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
                "discussions": "read",
            },
            {"create", "pull_request", "release", "milestone", "discussion"},
        )
