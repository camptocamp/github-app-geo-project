"""Module to generate the changelog on a release of a version."""

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import githubkit.exception
import githubkit.versions.latest.models
import githubkit.versions.v2022_11_28.webhooks.discussion
import githubkit.versions.v2022_11_28.webhooks.pull_request
import githubkit.webhooks
import packaging.version

from github_app_geo_project import module
from github_app_geo_project.configuration import GithubProject
from github_app_geo_project.module import utils
from github_app_geo_project.module.standard import changelog_configuration

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)


class Author:
    """Author of a pull request or commit."""

    def __init__(self, name: str, url: str) -> None:
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

    github: githubkit.versions.latest.models.PullRequest | githubkit.versions.latest.models.Commit
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


Condition = (
    changelog_configuration.ConditionConst
    | changelog_configuration.ConditionAndSolidusOr
    | changelog_configuration.ConditionNot
    | changelog_configuration.ConditionLabel
    | changelog_configuration.ConditionFiles
    | changelog_configuration.ConditionAuthor
    | changelog_configuration.ConditionTitle
    | changelog_configuration.ConditionBranch
)


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

    def __init__(
        self,
        tag_str: str | None = None,
        tag: githubkit.versions.latest.models.Tag | None = None,
        commit: githubkit.versions.latest.models.Commit | None = None,
    ) -> None:
        """Create a tag."""
        if tag_str is None:
            assert tag is not None
            assert tag.name is not None
            tag_str = tag.name
        if tag is not None:
            assert commit is not None
        self.tag_str = tag_str
        self.tag = tag
        self.commit = commit
        tag_match = self.TAG_RE.match(tag_str)
        if tag_match is None:
            tag_match = self.TAG2_RE.match(tag_str)
            if tag_match is None:
                message = f"Invalid tag: {tag_str}"
                raise ValueError(message)
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
            ],
        )
        # Return the first one
        return tags_list[0]
    return None


def get_release(
    tag: githubkit.versions.latest.models.Tag,
) -> githubkit.versions.latest.models.Repository | None:  # wrong type
    """Get the release from the tag."""
    for release in tag.get_repo().get_releases():  # type: ignore[attr-defined]
        if release.tag_name == tag.name:
            return release  # type: ignore[no-any-return]
    return None


def _get_discussion_url(github_project: GithubProject, tag: str) -> str | None:
    categories = github_project.aio_github.graphql(
        """
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
        variables={
            "owner": github_project.owner,
            "name": github_project.repository,
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
    discussions_result = github_project.aio_github.graphql(
        """
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
        variables={
            "owner": github_project.owner,
            "name": github_project.repository,
            "category": category[0]["id"],
        },
    )
    discussions = (
        discussions_result.get("data", {}).get("repository", {}).get("discussions", {}).get("nodes", [])
    )
    discussion = [d for d in discussions if tag in d.get("title", "").split()]
    if not discussion:
        return None
    return cast("str", discussion[0]["url"])


async def generate_changelog(
    github_project: GithubProject,
    configuration: changelog_configuration.Changelog,
    repository: str,
    tag_str: str,
) -> str:
    """Generate the changelog for a tag."""
    milestones = [
        milestone
        for milestone in cast(
            "list[githubkit.versions.latest.models.Milestone]",
            (
                await github_project.aio_github.rest.issues.async_list_milestones(
                    github_project.owner,
                    github_project.repository,
                )
            ).parsed_data,
        )
        if milestone.title == tag_str
    ]
    milestone = (
        milestones[0]
        if milestones
        else (
            await github_project.aio_github.rest.issues.async_create_milestone(
                github_project.owner,
                github_project.repository,
                data={"title": tag_str},
            )
        ).parsed_data
    )

    discussion_url = _get_discussion_url(github_project, tag_str)

    tags: dict[Tag, Tag] = {}
    for github_tag in (
        await github_project.aio_github.rest.repos.async_list_tags(
            github_project.owner,
            github_project.repository,
        )
    ).parsed_data:
        try:
            commit = (
                await github_project.aio_github.rest.repos.async_get_commit(
                    github_project.owner,
                    github_project.repository,
                    github_tag.commit.sha,
                )
            ).parsed_data
            tag = Tag(tag=github_tag, commit=commit)
            tags[tag] = tag
        except ValueError:
            _LOGGER.warning("Invalid tag: %s on repository %s", github_tag.name, repository)
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

    changelog_items = set()

    # Get the commits between oldTag and tag
    for commit in (
        await github_project.aio_github.rest.repos.async_compare_commits(
            github_project.owner,
            github_project.repository,
            f"{old_tag.tag_str}...{tag.tag_str}",
        )
    ).parsed_data.commits:
        has_pr = False
        for pull_request in (
            await github_project.aio_github.rest.repos.async_list_pull_requests_associated_with_commit(
                github_project.owner,
                github_project.repository,
                commit.sha,
            )
        ).parsed_data:
            has_pr = True
            if pull_request.milestone is None or pull_request.milestone.number == milestone.number:
                authors = (
                    {Author(pull_request.user.login, pull_request.user.html_url)}
                    if pull_request.user
                    else set()
                )
                commits = (
                    await github_project.aio_github.rest.pulls.async_list_commits(
                        github_project.owner,
                        github_project.repository,
                        pull_request.number,
                    )
                ).parsed_data
                assert commits is not None
                for commit_ in commits:
                    if commit_.author:
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
                        files={file.filename for file in pull_request.files},
                        labels={label.name for label in pull_request.labels},
                    ),
                )
        if not has_pr:
            author = (
                Author(commit.author.login, commit.author.html_url)
                if isinstance(
                    commit.author,
                    githubkit.versions.latest.models.SimpleUser,
                )
                else None
            )
            changelog_items.add(
                ChangelogItem(
                    github=commit,
                    ref=commit.sha,
                    title=commit.commit.message.split("\n")[0],
                    author=author,
                    authors={author} if author else set(),
                    branch=None,
                    files={file.filename for file in (commit.files or [])},
                    labels=set(),
                ),
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
                f"<p>- [{matched_item.condition}] {item.ref} {item.title} {item.author} ({authors_message}) {item.branch} {len(item.files)} - {labels_message}</p>",
            )
    message_obj = utils.HtmlMessage("\n".join(message))
    message_obj.title = f"Changelog for {tag.major}.{tag.minor}.{tag.patch}"
    _LOGGER.debug(message_obj)

    assert tag.commit is not None
    created = tag.commit.commit.author.date if tag.commit.commit.author else None
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


class Changelog(module.Module[changelog_configuration.Changelog, dict[str, Any], dict[str, Any], None]):
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
        if context.event_name == "release":
            event_data_release = githubkit.webhooks.parse_obj("release", context.event_data)
            if event_data_release.action == "created":
                return [
                    module.Action(
                        priority=module.PRIORITY_STATUS,
                        data={"version": event_data_release.release.tag_name},
                    ),
                ]
        if context.event_name == "create":
            event_data_create = githubkit.webhooks.parse_obj("create", context.event_data)
            if event_data_create.ref_type == "tag":
                return [
                    module.Action(
                        priority=module.PRIORITY_STATUS,
                        data={"type": "tag", "version": event_data_create.ref},
                    ),
                ]
        if context.event_name == "delete":
            event_data_delete = githubkit.webhooks.parse_obj("delete", context.event_data)
            if event_data_delete.ref_type == "tag":
                return [
                    module.Action(
                        priority=module.PRIORITY_STATUS,
                        data={"type": "tag", "version": event_data_delete.ref},
                    ),
                ]
        if context.event_name == "pull_request":
            event_data_pull_request = githubkit.webhooks.parse_obj("pull_request", context.event_data)
            if (
                event_data_pull_request.action
                in ("edited", "labeled", "unlabeled", "milestoned", "demilestoned")
                and event_data_pull_request.pull_request.state == "closed"
                and (
                    not event_data_pull_request.sender
                    or event_data_pull_request.sender.login != context.github_application.slug + "[bot]"
                )
            ):
                versions = set()
                if event_data_pull_request.action in ("milestoned", "demilestoned") and (
                    isinstance(
                        event_data_pull_request,
                        githubkit.versions.v2022_11_28.webhooks.pull_request.WebhookPullRequestMilestoned  # type: ignore[attr-defined]
                        | githubkit.versions.v2022_11_28.webhooks.pull_request.WebhookPullRequestDemilestoned,  # type: ignore[attr-defined]
                    )
                ):
                    milestone_version = (
                        event_data_pull_request.milestone.title if event_data_pull_request.milestone else None
                    )
                    if milestone_version is not None:
                        versions.add(milestone_version)
                pull_request_version = (
                    event_data_pull_request.pull_request.milestone.title
                    if event_data_pull_request.pull_request.milestone
                    else None
                )
                if pull_request_version is not None:
                    versions.add(pull_request_version)
                return [
                    module.Action(
                        priority=module.PRIORITY_CRON,
                        data={"version": version},
                    )
                    for version in versions
                ]

        if context.event_name == "milestone":
            event_data_milestone = githubkit.webhooks.parse_obj("milestone", context.event_data)
            if (
                event_data_milestone.action == "edited"
                and event_data_milestone.milestone
                and event_data_milestone.sender.login != context.github_application.slug + "[bot]"
            ):
                versions = {event_data_milestone.milestone.title}
                if event_data_milestone.changes and event_data_milestone.changes.title:
                    versions.add(event_data_milestone.changes.title.from_)

                return [
                    module.Action(
                        priority=module.PRIORITY_CRON,
                        data={"version": version},
                    )
                    for version in versions
                ]
        if context.event_name == "discussion":
            event_data_discussion = githubkit.webhooks.parse_obj("discussion", context.event_data)
            if event_data_discussion.action in ("created", "closed"):
                return [
                    module.Action(
                        priority=module.PRIORITY_STATUS,
                        data={"type": "discussion"},
                    ),
                ]
            if (
                event_data_discussion.action == "edited"
                and event_data_discussion.changes
                and event_data_discussion.changes.title
            ):
                return [
                    module.Action(
                        priority=module.PRIORITY_STATUS,
                        data={"type": "discussion"},
                    ),
                ]

        return []

    async def process(
        self,
        context: module.ProcessContext[changelog_configuration.Changelog, dict[str, Any]],
    ) -> module.ProcessOutput[dict[str, Any], None]:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        repository = f"{context.github_project.owner}/{context.github_project.repository}"

        if context.module_config.get("create-labels", changelog_configuration.CREATE_LABELS_DEFAULT):
            labels = (
                await context.github_project.aio_github.rest.issues.async_list_labels_for_repo(
                    context.github_project.owner,
                    context.github_project.repository,
                )
            ).parsed_data
            assert labels is not None
            existing_labels = {label.name for label in labels}
            for label, config in context.module_config.get("labels", {}).items():
                if label not in existing_labels:
                    await context.github_project.aio_github.rest.issues.async_create_label(
                        context.github_project.owner,
                        context.github_project.repository,
                        data={
                            "name": label,
                            "color": config["color"],
                            "description": config.get("description", ""),
                        },
                    )

        tag_str = cast("str", context.module_event_data.get("version"))
        if context.module_event_data.get("type") == "tag":
            if not context.module_config.get(
                "create-release",
                changelog_configuration.CREATE_RELEASE_DEFAULT,
            ):
                return module.ProcessOutput()

            prerelease = False
            try:
                latest_release = await context.github_project.aio_github.rest.repos.async_get_latest_release(
                    context.github_project.owner,
                    context.github_project.repository,
                )
                if latest_release is not None:
                    prerelease = packaging.version.Version(tag_str) < packaging.version.Version(
                        latest_release.parsed_data.tag_name,
                    )
            except githubkit.exception.RequestFailed as exception:
                if exception.response.status_code != 404:
                    raise
            await context.github_project.aio_github.rest.repos.async_create_release(
                context.github_project.owner,
                context.github_project.repository,
                data={
                    "tag_name": tag_str,
                    "name": tag_str,
                    "body": "",
                    "prerelease": prerelease,
                },
            )
            return module.ProcessOutput(
                actions=[
                    module.Action(
                        priority=module.PRIORITY_CRON,
                        data={"version": tag_str},
                    ),
                ],
            )
        if context.module_event_data.get("type") == "discussion":
            assert context.event_name == "discussion"
            event_data = githubkit.webhooks.parse_obj("discussion", context.event_data)
            title = set()
            title.update(event_data.discussion.title.split())
            if (
                isinstance(
                    event_data,
                    githubkit.versions.v2022_11_28.webhooks.discussion.WebhookDiscussionEdited,  # type: ignore[attr-defined]
                )
                and event_data.changes
                and event_data.changes.title
            ):
                title.update(event_data.changes.title.from_.split())
            tags = [
                tag
                for tag in (
                    await context.github_project.aio_github.rest.repos.async_list_tags(
                        context.github_project.owner,
                        context.github_project.repository,
                    )
                ).parsed_data
                if tag.name in title
            ]
            if not tags:
                _LOGGER.info(
                    "No tag found via for discussion %s on repository %s",
                    event_data.discussion.title,
                    repository,
                )
                return module.ProcessOutput()
            return module.ProcessOutput(
                actions=[
                    module.Action(
                        priority=module.PRIORITY_CRON,
                        data={"version": tags[0].name},
                    ),
                ],
            )

        tags = [
            tag
            for tag in (
                await context.github_project.aio_github.rest.repos.async_list_tags(
                    context.github_project.owner,
                    context.github_project.repository,
                )
            ).parsed_data
            if tag.name == tag_str
        ]
        if not tags:
            _LOGGER.info("No tag found '%s' on repository '%s'.", tag_str, repository)
            return module.ProcessOutput()

        release = await context.github_project.aio_github.rest.repos.async_get_release_by_tag(
            context.github_project.owner,
            context.github_project.repository,
            tag_str,
        )
        assert release is not None
        await context.github_project.aio_github.rest.repos.async_update_release(
            context.github_project.owner,
            context.github_project.repository,
            release.parsed_data.id,
            data={
                "tag_name": tag_str,
                "name": tag_str,
                "body": await generate_changelog(
                    context.github_project,
                    context.module_config,
                    repository,
                    tag_str,
                ),
            },
        )
        return module.ProcessOutput()

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema of the module configuration."""
        # Get changelog-schema.json related to this file
        with (Path(__file__).parent / "changelog-schema.json").open(encoding="utf-8") as schema_file:
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
