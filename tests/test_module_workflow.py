from unittest.mock import AsyncMock, MagicMock

import githubkit.exception
import pytest

from github_app_geo_project.module.workflow import Workflow

_USER = {
    "login": "user",
    "id": 54321,
    "node_id": "JKL012",
    "type": "User",
    "avatar_url": "https://avatars.githubusercontent.com/u/54321?v=4",
    "gravatar_id": "1234567890abcdef",
    "url": "https://api.github.com/users/user",
    "html_url": "https://github.com/user",
    "followers_url": "https://api.github.com/users/user/followers",
    "following_url": "https://api.github.com/users/user/following{/other_user}",
    "gists_url": "https://api.github.com/users/user/gists{/gist_id}",
    "starred_url": "https://api.github.com/users/user/starred{/owner}{/repo}",
    "subscriptions_url": "https://api.github.com/users/user/subscriptions",
    "organizations_url": "https://api.github.com/users/user/orgs",
    "repos_url": "https://api.github.com/users/user/repos",
    "events_url": "https://api.github.com/users/user/events{/privacy}",
    "received_events_url": "https://api.github.com/users/user/received_events",
    "site_admin": False,
}
_REPOSITORY = {
    "id": 12345,
    "node_id": "GHI789",
    "archive_url": "https://api.github.com/repos/user/repo/{archive_format}{/ref}",
    "assignees_url": "https://api.github.com/repos/user/repo/assignees{/user}",
    "blobs_url": "https://api.github.com/repos/user/repo/git/blobs{/sha}",
    "branches_url": "https://api.github.com/repos/user/repo/branches{/branch}",
    "collaborators_url": "https://api.github.com/repos/user/repo/collaborators{/collaborator}",
    "comments_url": "https://api.github.com/repos/user/repo/comments{/number}",
    "commits_url": "https://api.github.com/repos/user/repo/commits{/sha}",
    "compare_url": "https://api.github.com/repos/user/repo/compare/{base}...{head}",
    "contents_url": "https://api.github.com/repos/user/repo/contents/{+path}",
    "contributors_url": "https://api.github.com/repos/user/repo/contributors",
    "deployments_url": "https://api.github.com/repos/user/repo/deployments",
    "downloads_url": "https://api.github.com/repos/user/repo/downloads",
    "events_url": "https://api.github.com/repos/user/repo/events",
    "forks_url": "https://api.github.com/repos/user/repo/forks",
    "git_commits_url": "https://api.github.com/repos/user/repo/git/commits{/sha}",
    "git_refs_url": "https://api.github.com/repos/user/repo/git/refs{/sha}",
    "git_tags_url": "https://api.github.com/repos/user/repo/git/tags{/sha}",
    "git_url": "git://github.com/user/repo.git",
    "issue_comment_url": "https://api.github.com/repos/user/repo/issues/comments{/number}",
    "issue_events_url": "https://api.github.com/repos/user/repo/issues/events{/number}",
    "issues_url": "https://api.github.com/repos/user/repo/issues{/number}",
    "keys_url": "https://api.github.com/repos/user/repo/keys{/key_id}",
    "labels_url": "https://api.github.com/repos/user/repo/labels{/name}",
    "languages_url": "https://api.github.com/repos/user/repo/languages",
    "merges_url": "https://api.github.com/repos/user/repo/merges",
    "milestones_url": "https://api.github.com/repos/user/repo/milestones{/number}",
    "notifications_url": "https://api.github.com/repos/user/repo/notifications{?since,all,participating}",
    "pulls_url": "https://api.github.com/repos/user/repo/pulls{/number}",
    "releases_url": "https://api.github.com/repos/user/repo/releases{/id}",
    "ssh_url": "git://github.com/user/repo.git",
    "stargazers_url": "https://api.github.com/repos/user/repo/stargazers",
    "statuses_url": "https://api.github.com/repos/user/repo/statuses/{sha}",
    "subscribers_url": "https://api.github.com/repos/user/repo/subscribers",
    "subscription_url": "https://api.github.com/repos/user/repo/subscription",
    "tags_url": "https://api.github.com/repos/user/repo/tags",
    "teams_url": "https://api.github.com/repos/user/repo/teams",
    "trees_url": "https://api.github.com/repos/user/repo/git/trees{/sha}",
    "clone_url": "https://github.com/user/repo.git",
    "mirror_url": "git:git.example.com:user/repo.git",
    "hooks_url": "https://api.github.com/repos/user/repo/hooks",
    "svn_url": "svn://github.com/user/repo",
    "homepage": None,
    "language": None,
    "forks_count": 0,
    "stargazers_count": 1,
    "watchers_count": 1,
    "size": 1234,
    "default_branch": "master",
    "open_issues_count": 0,
    "has_pages": False,
    "open_issues": 0,
    "name": "repo",
    "full_name": "user/repo",
    "owner": _USER,
    "html_url": "https://github.com/user/repo",
    "description": "My repository",
    "fork": False,
    "forks": False,
    "url": "https://api.github.com/repos/user/repo",
    "license": None,
    "private": False,
    "disabled": False,
    "pushed_at": "2024-01-01T00:00:00Z",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:10:00Z",
    "watchers": 1,
}
_EVENT = {
    "action": "completed",
    "workflow_run": {
        "id": 123456789,
        "name": "CI/CD Pipeline",
        "head_branch": "master",
        "head_sha": "a1b2c3d4",
        "run_number": 5,
        "event": "push",
        "status": "completed",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:10:00Z",
        "run_attempt": 1,
        "run_started_at": "2024-01-01T00:00:00Z",
        "workflow_id": 987654,
        "html_url": "https://github.com/user/repo/actions/runs/123456789",
        "jobs_url": "https://api.github.com/repos/user/repo/actions/runs/123456789/jobs",
        "logs_url": "https://api.github.com/repos/user/repo/actions/runs/123456789/logs",
        "node_id": "ABC123",
        "url": "https://api.github.com/repos/user/repo/actions/runs/123456789",
        "check_suite_url": "https://api.github.com/repos/user/repo/check-suites/123456789",
        "check_suite_id": 123456789,
        "check_suite_node_id": "DEF456",
        "head_commit": {
            "id": "a1b2c3d4",
            "message": "Fix bug",
            "timestamp": "2024-01-01T00:00:00Z",
            "author": {"name": "Dev User", "email": "dev@example.com"},
            "committer": {"name": "Dev User", "email": "dev@example.com"},
            "tree_id": "abcd1234",
        },
        "repository": _REPOSITORY,
        "head_repository": _REPOSITORY,
        "actor": _USER,
        "triggering_actor": _USER,
        "workflow_url": "https://api.github.com/repos/user/repo/actions/workflows/987654",
        "cancel_url": "https://api.github.com/repos/user/repo/actions/runs/123456789/cancel",
        "rerun_url": "https://api.github.com/repos/user/repo/actions/runs/123456789/rerun",
        "previous_attempt_url": None,
        "pull_requests": [],
        "artifacts_url": "https://api.github.com/repos/user/repo/actions/runs/123456789/artifacts",
        "path": "refs/heads/master",
    },
    "workflow": {
        "id": 987654,
        "node_id": "XYZ789",
        "name": "workflow_name",
        "path": ".github/workflows/ci.yml",
        "state": "active",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "url": "https://api.github.com/repos/user/repo/actions/workflows/987654",
        "html_url": "https://github.com/user/repo/actions/workflows/ci.yml",
        "badge_url": "https://github.com/user/repo/actions/workflows/ci.yml/badge.svg",
    },
    "repository": _REPOSITORY,
    "sender": _USER,
}


@pytest.mark.asyncio
async def test_process_success() -> None:
    # Create a mock context
    context = MagicMock()
    context.github_project.owner = "owner"
    context.github_project.repository = "repository"
    context.github_event_name = "workflow_run"
    context.module_event_name = "workflow_run"
    context.github_event_data = dict(_EVENT)
    context.github_event_data["workflow_run"]["conclusion"] = "success"

    default_branch = AsyncMock()
    default_branch.return_value = "master"
    context.github_project.default_branch = default_branch

    repo = MagicMock()
    context.github_project.aio_repo = repo
    repo.default_branch = "master"
    github = MagicMock()
    context.github_project.aio_github = github
    rest = MagicMock()
    github.rest = rest
    repos = AsyncMock()
    rest.repos = repos
    response = MagicMock()
    response.status_code = 404
    repos.async_get_content.side_effect = githubkit.exception.RequestFailed(response)

    # Create an instance of the Workflow class
    workflow = Workflow()

    # Call the process method
    transversal_status = await workflow.update_transversal_status(
        context,
        None,
        {
            "owner/repository": {
                "workflow_name": {
                    "date": None,
                    "jobs": [],
                    "url": None,
                },
            },
        },
    )

    assert transversal_status == {}


@pytest.mark.asyncio
async def test_process_failure() -> None:
    # Create a mock context
    context = MagicMock()
    context.github_project.owner = "owner"
    context.github_project.repository = "repository"
    context.github_event_name = "workflow_run"
    context.module_event_name = "workflow_run"
    context.github_event_data = dict(_EVENT)
    context.github_event_data["workflow_run"]["conclusion"] = "failure"

    default_branch = AsyncMock()
    default_branch.return_value = "master"
    context.github_project.default_branch = default_branch

    repo = MagicMock()
    context.github_project.aio_repo = repo
    repo.default_branch = "master"
    github = MagicMock()
    context.github_project.aio_github = github
    rest = MagicMock()
    github.rest = rest
    repos = AsyncMock()
    rest.repos = repos
    repo_response = MagicMock()
    repo_response.status_code = 404
    repos.async_get_content.side_effect = githubkit.exception.RequestFailed(repo_response)
    actions = AsyncMock()
    rest.actions = actions
    actions_response = MagicMock()
    actions_response.status_code = 200
    actions_response.parsed_data = {
        "total_count": 1,
        "jobs": [
            {
                "id": 123456789,
                "run_id": 29679449,
                "run_url": "https://api.github.com/repos/octo-org/octo-repo/actions/runs/29679449",
                "node_id": "MDEyOldvcmtmbG93IEpvYjM5OTQ0NDQ5Ng==",
                "head_sha": "f83a356604ae3c5d03e1b46ef4d1ca77d64a90b0",
                "url": "https://api.github.com/repos/octo-org/octo-repo/actions/jobs/399444496",
                "html_url": "https://github.com/octo-org/octo-repo/runs/29679449/jobs/399444496",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2020-01-20T17:42:40Z",
                "completed_at": "2020-01-20T17:44:39Z",
                "name": "build",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2020-01-20T09:42:40.000-08:00",
                        "completed_at": "2020-01-20T09:42:41.000-08:00",
                    },
                ],
                "check_run_url": "https://api.github.com/repos/octo-org/octo-repo/check-runs/399444496",
                "labels": ["self-hosted", "foo", "bar"],
                "runner_id": 1,
                "runner_name": "my runner",
                "runner_group_id": 2,
                "runner_group_name": "my runner group",
                "workflow_name": "CI",
                "head_branch": "main",
            }
        ],
    }
    actions.async_list_jobs_for_workflow_run.return_value = actions_response

    # Create an instance of the Workflow class
    workflow = Workflow()

    # Call the process method
    transversal_status = await workflow.update_transversal_status(context, None, {})

    assert "updated" in transversal_status["owner/repository"]
    del transversal_status["owner/repository"]["updated"]
    # Assert the expected output
    assert transversal_status == {
        "owner/repository": {
            "master": {
                "workflow_name": {
                    "date": "2024-01-01T00:00:00+00:00",
                    "jobs": [],
                    "url": "https://github.com/user/repo/actions/runs/123456789",
                },
            },
        },
    }
