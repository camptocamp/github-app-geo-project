# Copyright (c) 2026, Camptocamp SA

"""Tests for the patch module."""

from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from github_app_geo_project import module
from github_app_geo_project.module.patch import Patch, _resolve_artifact_target
from github_app_geo_project.settings import settings


@pytest.fixture
def mock_github_project():
    project = MagicMock()
    project.owner = "camptocamp"
    project.repository = "test-repo"
    project.aio_github = MagicMock()
    project.aio_github.rest = MagicMock()
    return project


@pytest.fixture
def mock_context(mock_github_project):
    context = MagicMock(spec=module.ProcessContext)
    context.github_project = mock_github_project
    context.module_event_name = "workflow_run"
    context.module_config = {}
    context.module_event_data = {}
    return context


def _make_mock_workflow_run(
    conclusion: str = "failure",
    head_branch: str = "main",
    run_id: int = 12345,
    owner_login: str = "camptocamp",
    workflow_path: str = ".github/workflows/ci.yaml",
):
    workflow_run = MagicMock()
    workflow_run.id = run_id
    workflow_run.name = "CI"
    workflow_run.head_branch = head_branch
    workflow_run.conclusion = conclusion
    workflow_run.actor = MagicMock()
    workflow_run.triggering_actor = MagicMock()
    workflow_run.head_repository = MagicMock()
    workflow_run.head_repository.owner = MagicMock()
    workflow_run.head_repository.owner.login = owner_login
    workflow_run.repository = MagicMock()
    workflow_run.repository.owner = MagicMock()
    workflow_run.repository.owner.login = owner_login
    sender = MagicMock()
    sender.login = "user"
    workflow_def = MagicMock()
    workflow_def.path = workflow_path
    return workflow_run, sender, workflow_def


def _make_mock_workflow_job(
    conclusion: str = "failure",
    head_branch: str = "main",
    run_id: int = 12345,
    job_name: str = "build",
):
    workflow_job = MagicMock()
    workflow_job.id = 1
    workflow_job.run_id = run_id
    workflow_job.name = job_name
    workflow_job.head_branch = head_branch
    workflow_job.conclusion = conclusion
    workflow_job.status = "completed"
    workflow_job.run_attempt = 1
    workflow_job.steps = []
    sender = MagicMock()
    sender.type = "User"
    return workflow_job, sender


class TestGetActions:
    def test_workflow_run_completed_failure(self):
        patch_module = Patch()
        workflow_run, sender, workflow_def = _make_mock_workflow_run()
        event_data = MagicMock()
        event_data.action = "completed"
        event_data.workflow_run = workflow_run
        event_data.sender = sender
        event_data.workflow = workflow_def

        context = module.GetActionContext(
            github_event_name="workflow_run",
            github_event_data={},
            module_event_name="workflow_run",
            owner="camptocamp",
            repository="test-repo",
            github_application=MagicMock(),
        )
        with patch("githubkit.webhooks.parse_obj", return_value=event_data):
            actions = patch_module.get_actions(context)
        assert len(actions) == 1
        assert actions[0].priority == module.PRIORITY_STANDARD

    def test_workflow_run_success_no_action(self):
        patch_module = Patch()
        workflow_run, sender, workflow_def = _make_mock_workflow_run(conclusion="success")
        event_data = MagicMock()
        event_data.action = "completed"
        event_data.workflow_run = workflow_run
        event_data.sender = sender
        event_data.workflow = workflow_def

        context = module.GetActionContext(
            github_event_name="workflow_run",
            github_event_data={},
            module_event_name="workflow_run",
            owner="camptocamp",
            repository="test-repo",
            github_application=MagicMock(),
        )
        with patch("githubkit.webhooks.parse_obj", return_value=event_data):
            actions = patch_module.get_actions(context)
        assert len(actions) == 0

    def test_workflow_run_dynamic_no_action(self):
        patch_module = Patch()
        workflow_run, sender, _ = _make_mock_workflow_run(workflow_path="dynamic/something.yaml")
        event_data = MagicMock()
        event_data.action = "completed"
        event_data.workflow_run = workflow_run
        event_data.sender = sender
        workflow_def_dynamic = MagicMock()
        workflow_def_dynamic.path = "dynamic/something.yaml"
        event_data.workflow = workflow_def_dynamic

        context = module.GetActionContext(
            github_event_name="workflow_run",
            github_event_data={},
            module_event_name="workflow_run",
            owner="camptocamp",
            repository="test-repo",
            github_application=MagicMock(),
        )
        with patch("githubkit.webhooks.parse_obj", return_value=event_data):
            actions = patch_module.get_actions(context)
        assert len(actions) == 0

    def test_workflow_job_completed_failure(self):
        patch_module = Patch()
        workflow_job, sender = _make_mock_workflow_job()
        event_data = MagicMock()
        event_data.action = "completed"
        event_data.workflow_job = workflow_job
        event_data.sender = sender

        context = module.GetActionContext(
            github_event_name="workflow_job",
            github_event_data={},
            module_event_name="workflow_job",
            owner="camptocamp",
            repository="test-repo",
            github_application=MagicMock(),
        )
        with patch("githubkit.webhooks.parse_obj", return_value=event_data):
            actions = patch_module.get_actions(context)
        assert len(actions) == 1

    def test_workflow_job_codeql_no_action(self):
        patch_module = Patch()
        workflow_job, sender = _make_mock_workflow_job(job_name="Analyze (python)")
        event_data = MagicMock()
        event_data.action = "completed"
        event_data.workflow_job = workflow_job
        event_data.sender = sender

        context = module.GetActionContext(
            github_event_name="workflow_job",
            github_event_data={},
            module_event_name="workflow_job",
            owner="camptocamp",
            repository="test-repo",
            github_application=MagicMock(),
        )
        with patch("githubkit.webhooks.parse_obj", return_value=event_data):
            actions = patch_module.get_actions(context)
        assert len(actions) == 0


def _make_artifact(name: str = "Apply HELM generated files.patch"):
    artifact = MagicMock()
    artifact.name = name
    artifact.id = 999
    artifact.expired = False
    artifact.created_at = MagicMock()
    artifact.created_at.timestamp.return_value = 1000.0
    return artifact


def _make_patch_zip_content(
    patch_content: str = "diff --git a/file.txt b/file.txt\n--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new\n",
) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("patch.diff", patch_content)
    return buf.getvalue()


_PATCH_COMMIT_MESSAGE = "Apply HELM generated files\n\nFrom the artifact of the previous workflow run"


def _make_commit(message: str):
    commit = MagicMock()
    commit.commit = MagicMock()
    commit.commit.message = message
    return commit


def _make_open_pull_request(head_ref: str):
    pull_request = MagicMock()
    pull_request.head = MagicMock()
    pull_request.head.ref = head_ref
    return pull_request


def _setup_process_mocks(
    mock_context,
    mock_github_project,
    head_branch="main",
    run_id=12345,
    branch_commits=None,
    open_pull_requests=None,
    artifacts=None,
    head_commit=None,
):
    mock_context.module_event_name = "workflow_run"
    mock_context.github_event_data = {}

    workflow_run, sender, workflow_def = _make_mock_workflow_run(head_branch=head_branch, run_id=run_id)
    event_data = MagicMock()
    event_data.action = "completed"
    event_data.workflow_run = workflow_run
    event_data.sender = sender
    event_data.workflow = workflow_def

    artifacts_response = MagicMock()
    artifacts_response.parsed_data.artifacts = [_make_artifact()] if artifacts is None else artifacts
    mock_github_project.aio_github.rest.actions.async_list_workflow_run_artifacts = AsyncMock(
        return_value=artifacts_response,
    )

    mock_github_project.aio_github.rest.repos.async_get_branch = AsyncMock()

    if head_commit is None:
        head_commit = _make_commit("Some human commit")
    commit_response = MagicMock()
    commit_response.parsed_data = head_commit
    mock_github_project.aio_github.rest.repos.async_get_commit = AsyncMock(return_value=commit_response)

    if branch_commits is None:
        branch_commits = [_make_commit("Some human commit")]
    commits_response = MagicMock()
    commits_response.parsed_data = branch_commits
    mock_github_project.aio_github.rest.repos.async_list_commits = AsyncMock(return_value=commits_response)

    if open_pull_requests is None:
        open_pull_requests = []
    pull_requests_response = MagicMock()
    pull_requests_response.parsed_data = open_pull_requests
    mock_github_project.aio_github.rest.pulls.async_list = AsyncMock(return_value=pull_requests_response)

    download_response = MagicMock()
    download_response.status_code = 200
    download_response.content = _make_patch_zip_content()
    mock_github_project.aio_github.rest.actions.async_download_artifact = AsyncMock(
        return_value=download_response,
    )

    return event_data


class TestProcess:
    @pytest.mark.asyncio
    async def test_direct_push_success(self, mock_context, mock_github_project, tmp_path):
        patch_module = Patch()
        event_data = _setup_process_mocks(mock_context, mock_github_project)

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=anyio.Path(tmp_path))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("githubkit.webhooks.parse_obj", return_value=event_data),
            patch(
                "github_app_geo_project.module.patch.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.has_changes",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_commit",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            mock_apply_proc = MagicMock()
            mock_apply_proc.returncode = 0
            mock_apply_proc.communicate = AsyncMock(return_value=(b"Applied cleanly", b""))

            mock_push_proc = MagicMock()
            mock_push_proc.returncode = 0
            mock_push_proc.communicate = AsyncMock(return_value=(b"", b""))

            async def mock_create_subprocess_exec(*args, **kwargs):
                if args[1] == "apply":
                    return mock_apply_proc
                if args[1] == "push":
                    return mock_push_proc
                return MagicMock()

            with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec):
                result = await patch_module.process(mock_context)

            assert result.success is not False

    @pytest.mark.asyncio
    async def test_protected_branch_creates_pr(self, mock_context, mock_github_project, tmp_path):
        patch_module = Patch()
        event_data = _setup_process_mocks(
            mock_context, mock_github_project, head_branch="main", run_id=33077106788
        )

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=anyio.Path(tmp_path))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        mock_pull_request = MagicMock()
        mock_pull_request.html_url = "https://github.com/camptocamp/test-repo/pull/1"

        with (
            patch("githubkit.webhooks.parse_obj", return_value=event_data),
            patch(
                "github_app_geo_project.module.patch.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.has_changes",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_commit",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_pull_request",
                new_callable=AsyncMock,
                return_value=(True, mock_pull_request),
            ) as mock_create_pr,
        ):
            mock_apply_proc = MagicMock()
            mock_apply_proc.returncode = 0
            mock_apply_proc.communicate = AsyncMock(return_value=(b"Applied cleanly", b""))

            mock_push_proc = MagicMock()
            mock_push_proc.returncode = 1
            mock_push_proc.communicate = AsyncMock(
                return_value=(
                    b"",
                    b"remote: error: GH006: Protected branch update failed for refs/heads/main.\nremote: protected branch hook declined\n",
                ),
            )

            async def mock_create_subprocess_exec(*args, **kwargs):
                if args[1] == "apply":
                    return mock_apply_proc
                if args[1] == "push":
                    return mock_push_proc
                return MagicMock()

            with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec):
                result = await patch_module.process(mock_context)

            mock_create_pr.assert_called_once()
            call_args = mock_create_pr.call_args
            assert call_args[0][0] == "main"
            assert call_args[0][1] == "ghci/patch/main-33077106788"
            assert call_args[0][2] == "Apply HELM generated files"
            assert result.success is not False

    @pytest.mark.asyncio
    async def test_other_push_failure(self, mock_context, mock_github_project, tmp_path):
        patch_module = Patch()
        event_data = _setup_process_mocks(mock_context, mock_github_project)

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=anyio.Path(tmp_path))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("githubkit.webhooks.parse_obj", return_value=event_data),
            patch(
                "github_app_geo_project.module.patch.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.has_changes",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_commit",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_pull_request",
                new_callable=AsyncMock,
            ) as mock_create_pr,
        ):
            mock_apply_proc = MagicMock()
            mock_apply_proc.returncode = 0
            mock_apply_proc.communicate = AsyncMock(return_value=(b"Applied cleanly", b""))

            mock_push_proc = MagicMock()
            mock_push_proc.returncode = 1
            mock_push_proc.communicate = AsyncMock(
                return_value=(b"", b"error: failed to push some refs\n"),
            )

            async def mock_create_subprocess_exec(*args, **kwargs):
                if args[1] == "apply":
                    return mock_apply_proc
                if args[1] == "push":
                    return mock_push_proc
                return MagicMock()

            with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec):
                result = await patch_module.process(mock_context)

            assert result.success is False
            assert "Failed to push the changes" in result.check_output["summary"]
            mock_create_pr.assert_not_called()


class TestConsecutivePatchLimit:
    @pytest.mark.asyncio
    async def test_limit_reached_skips_patch(self, mock_context, mock_github_project, monkeypatch):
        monkeypatch.setattr(settings.patch, "max_consecutive_commits", 3)
        patch_module = Patch()
        event_data = _setup_process_mocks(
            mock_context,
            mock_github_project,
            branch_commits=[_make_commit(_PATCH_COMMIT_MESSAGE)] * 3,
        )

        with (
            patch("githubkit.webhooks.parse_obj", return_value=event_data),
            patch(
                "github_app_geo_project.module.patch.module_utils.GIT_WORKTREE_CACHE.working_tree",
            ) as mock_working_tree,
            patch(
                "github_app_geo_project.module.patch.module_utils.create_commit",
                new_callable=AsyncMock,
            ) as mock_create_commit,
        ):
            result = await patch_module.process(mock_context)

        assert result.success is False
        assert result.check_output is not None
        assert "infinite loop" in result.check_output["summary"]
        mock_create_commit.assert_not_called()
        mock_working_tree.assert_not_called()

    @pytest.mark.asyncio
    async def test_below_limit_applies_patch(self, mock_context, mock_github_project, tmp_path, monkeypatch):
        monkeypatch.setattr(settings.patch, "max_consecutive_commits", 3)
        patch_module = Patch()
        event_data = _setup_process_mocks(
            mock_context,
            mock_github_project,
            branch_commits=[
                _make_commit(_PATCH_COMMIT_MESSAGE),
                _make_commit(_PATCH_COMMIT_MESSAGE),
                _make_commit("Some human commit"),
            ],
        )

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=anyio.Path(tmp_path))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("githubkit.webhooks.parse_obj", return_value=event_data),
            patch(
                "github_app_geo_project.module.patch.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.has_changes",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_commit",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_create_commit,
        ):
            mock_apply_proc = MagicMock()
            mock_apply_proc.returncode = 0
            mock_apply_proc.communicate = AsyncMock(return_value=(b"Applied cleanly", b""))

            mock_push_proc = MagicMock()
            mock_push_proc.returncode = 0
            mock_push_proc.communicate = AsyncMock(return_value=(b"", b""))

            async def mock_create_subprocess_exec(*args, **kwargs):
                if args[1] == "apply":
                    return mock_apply_proc
                if args[1] == "push":
                    return mock_push_proc
                return MagicMock()

            with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec):
                result = await patch_module.process(mock_context)

        assert result.success is not False
        mock_create_commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_human_head_commit_resets_counter(
        self, mock_context, mock_github_project, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(settings.patch, "max_consecutive_commits", 3)
        patch_module = Patch()
        event_data = _setup_process_mocks(
            mock_context,
            mock_github_project,
            branch_commits=[
                _make_commit("Some human commit"),
                *[_make_commit(_PATCH_COMMIT_MESSAGE)] * 5,
            ],
        )

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=anyio.Path(tmp_path))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("githubkit.webhooks.parse_obj", return_value=event_data),
            patch(
                "github_app_geo_project.module.patch.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.has_changes",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_commit",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_create_commit,
        ):
            mock_apply_proc = MagicMock()
            mock_apply_proc.returncode = 0
            mock_apply_proc.communicate = AsyncMock(return_value=(b"Applied cleanly", b""))

            mock_push_proc = MagicMock()
            mock_push_proc.returncode = 0
            mock_push_proc.communicate = AsyncMock(return_value=(b"", b""))

            async def mock_create_subprocess_exec(*args, **kwargs):
                if args[1] == "apply":
                    return mock_apply_proc
                if args[1] == "push":
                    return mock_push_proc
                return MagicMock()

            with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec):
                result = await patch_module.process(mock_context)

        assert result.success is not False
        mock_create_commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_protected_branch_limit_reached(
        self, mock_context, mock_github_project, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(settings.patch, "max_consecutive_commits", 3)
        patch_module = Patch()
        event_data = _setup_process_mocks(
            mock_context,
            mock_github_project,
            open_pull_requests=[_make_open_pull_request(f"ghci/patch/main-{index}") for index in range(3)],
        )

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=anyio.Path(tmp_path))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("githubkit.webhooks.parse_obj", return_value=event_data),
            patch(
                "github_app_geo_project.module.patch.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.has_changes",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_commit",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_pull_request",
                new_callable=AsyncMock,
            ) as mock_create_pr,
        ):
            mock_apply_proc = MagicMock()
            mock_apply_proc.returncode = 0
            mock_apply_proc.communicate = AsyncMock(return_value=(b"Applied cleanly", b""))

            mock_push_proc = MagicMock()
            mock_push_proc.returncode = 1
            mock_push_proc.communicate = AsyncMock(
                return_value=(
                    b"",
                    b"remote: error: GH006: Protected branch update failed for refs/heads/main.\nremote: protected branch hook declined\n",
                ),
            )

            async def mock_create_subprocess_exec(*args, **kwargs):
                if args[1] == "apply":
                    return mock_apply_proc
                if args[1] == "push":
                    return mock_push_proc
                return MagicMock()

            with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec):
                result = await patch_module.process(mock_context)

        assert result.success is False
        assert result.check_output is not None
        assert "refusing to create" in result.check_output["summary"]
        mock_create_pr.assert_not_called()


class TestResolveArtifactTarget:
    def test_plain_artifact_uses_the_run_branch(self):
        target = _resolve_artifact_target(
            "Apply HELM generated files.patch",
            "main",
            "push",
            ["schedule"],
        )
        assert target.branch == "main"
        assert target.message == "Apply HELM generated files"
        assert target.error is None

    def test_branch_suffix_on_an_allowed_event(self):
        target = _resolve_artifact_target(
            "Update the l10n files [prod-2-9].patch",
            "main",
            "schedule",
            ["schedule", "workflow_dispatch", "repository_dispatch"],
        )
        assert target.branch == "prod-2-9"
        assert target.message == "Update the l10n files"
        assert target.error is None

    def test_branch_suffix_on_a_forbidden_event(self):
        target = _resolve_artifact_target(
            "Update the l10n files [prod-2-9].patch",
            "feature-branch",
            "push",
            ["schedule"],
        )
        assert target.branch is None
        assert target.error is not None
        assert "prod-2-9" in target.error
        assert "push" in target.error

    def test_branch_suffix_without_event(self):
        target = _resolve_artifact_target(
            "Update the l10n files [prod-2-9].patch",
            "main",
            None,
            ["schedule"],
        )
        assert target.branch is None
        assert target.error is not None

    def test_brackets_in_the_middle_are_not_a_branch(self):
        target = _resolve_artifact_target(
            "Fix the [ci] configuration.patch",
            "main",
            "schedule",
            ["schedule"],
        )
        assert target.branch == "main"
        assert target.message == "Fix the [ci] configuration"
        assert target.error is None


def _mock_working_tree(tmp_path):
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=anyio.Path(tmp_path))
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    return mock_cm


def _mock_subprocess(push_returncode: int = 0, push_stderr: bytes = b""):
    mock_apply_proc = MagicMock()
    mock_apply_proc.returncode = 0
    mock_apply_proc.communicate = AsyncMock(return_value=(b"Applied cleanly", b""))

    mock_push_proc = MagicMock()
    mock_push_proc.returncode = push_returncode
    mock_push_proc.communicate = AsyncMock(return_value=(b"", push_stderr))

    pushed_commands = []

    async def mock_create_subprocess_exec(*args, **kwargs):
        if args[1] == "apply":
            return mock_apply_proc
        if args[1] == "push":
            pushed_commands.append(list(args))
            return mock_push_proc
        return MagicMock()

    return mock_create_subprocess_exec, pushed_commands


_PROTECTED_STDERR = (
    b"remote: error: GH006: Protected branch update failed for refs/heads/prod-2-9.\n"
    b"remote: protected branch hook declined\n"
)


class TestBranchOverride:
    @pytest.mark.asyncio
    async def test_patch_applied_on_the_requested_branch(self, mock_context, mock_github_project, tmp_path):
        patch_module = Patch()
        event_data = _setup_process_mocks(
            mock_context,
            mock_github_project,
            head_branch="main",
            run_id=42,
            artifacts=[_make_artifact("Update the l10n files [prod-2-9].patch")],
        )
        event_data.workflow_run.event = "schedule"

        mock_create_subprocess_exec, pushed_commands = _mock_subprocess()

        with (
            patch("githubkit.webhooks.parse_obj", return_value=event_data),
            patch(
                "github_app_geo_project.module.patch.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=_mock_working_tree(tmp_path),
            ) as mock_working_tree,
            patch(
                "github_app_geo_project.module.patch.module_utils.has_changes",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_commit",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_create_commit,
            patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec),
        ):
            result = await patch_module.process(mock_context)

        assert result.success is not False
        assert mock_working_tree.call_args[0][1] == "prod-2-9"
        assert pushed_commands[-1][-1] == "HEAD:prod-2-9"
        assert mock_create_commit.call_args[0][0] == (
            "Update the l10n files\n\nFrom the artifact of the previous workflow run"
        )

    @pytest.mark.asyncio
    async def test_branch_override_refused_on_a_pull_request_run(
        self, mock_context, mock_github_project, tmp_path
    ):
        patch_module = Patch()
        event_data = _setup_process_mocks(
            mock_context,
            mock_github_project,
            artifacts=[_make_artifact("Update the l10n files [prod-2-9].patch")],
        )
        event_data.workflow_run.event = "pull_request"

        with (
            patch("githubkit.webhooks.parse_obj", return_value=event_data),
            patch(
                "github_app_geo_project.module.patch.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=_mock_working_tree(tmp_path),
            ) as mock_working_tree,
        ):
            result = await patch_module.process(mock_context)

        assert result.success is False
        assert result.check_output is not None
        assert "not allowed to target another branch" in result.check_output["summary"]
        mock_working_tree.assert_not_called()

    @pytest.mark.asyncio
    async def test_artifacts_grouped_per_branch(self, mock_context, mock_github_project, tmp_path):
        patch_module = Patch()
        event_data = _setup_process_mocks(
            mock_context,
            mock_github_project,
            artifacts=[
                _make_artifact("Update the l10n files [prod-2-9].patch"),
                _make_artifact("Update the l10n files [prod-2-10].patch"),
            ],
        )
        event_data.workflow_run.event = "schedule"

        mock_create_subprocess_exec, pushed_commands = _mock_subprocess()

        with (
            patch("githubkit.webhooks.parse_obj", return_value=event_data),
            patch(
                "github_app_geo_project.module.patch.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=_mock_working_tree(tmp_path),
            ) as mock_working_tree,
            patch(
                "github_app_geo_project.module.patch.module_utils.has_changes",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_commit",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec),
        ):
            result = await patch_module.process(mock_context)

        assert result.success is not False
        assert mock_working_tree.call_count == 2
        assert sorted(call[0][1] for call in mock_working_tree.call_args_list) == ["prod-2-10", "prod-2-9"]
        assert sorted(command[-1] for command in pushed_commands) == ["HEAD:prod-2-10", "HEAD:prod-2-9"]


class TestReusablePullRequest:
    @pytest.mark.asyncio
    async def test_existing_patch_pull_request_is_updated(self, mock_context, mock_github_project, tmp_path):
        patch_module = Patch()
        event_data = _setup_process_mocks(
            mock_context,
            mock_github_project,
            head_branch="prod-2-9",
            run_id=43,
            artifacts=[_make_artifact("Update the l10n files.patch")],
            open_pull_requests=[_make_open_pull_request("ghci/patch/prod-2-9-41")],
            head_commit=_make_commit(_PATCH_COMMIT_MESSAGE),
        )

        mock_create_subprocess_exec, _ = _mock_subprocess(push_returncode=1, push_stderr=_PROTECTED_STDERR)
        mock_pull_request = MagicMock()
        mock_pull_request.html_url = "https://github.com/camptocamp/test-repo/pull/4"

        with (
            patch("githubkit.webhooks.parse_obj", return_value=event_data),
            patch(
                "github_app_geo_project.module.patch.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=_mock_working_tree(tmp_path),
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.has_changes",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_commit",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_pull_request",
                new_callable=AsyncMock,
                return_value=(True, mock_pull_request),
            ) as mock_create_pr,
            patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec),
        ):
            result = await patch_module.process(mock_context)

        assert result.success is not False
        mock_create_pr.assert_called_once()
        assert mock_create_pr.call_args[0][0] == "prod-2-9"
        assert mock_create_pr.call_args[0][1] == "ghci/patch/prod-2-9-41"
        assert mock_create_pr.call_args[0][2] == "Update the l10n files"

    @pytest.mark.asyncio
    async def test_human_pull_request_is_not_reused(self, mock_context, mock_github_project, tmp_path):
        patch_module = Patch()
        event_data = _setup_process_mocks(
            mock_context,
            mock_github_project,
            head_branch="prod-2-9",
            run_id=43,
            artifacts=[_make_artifact("Update the l10n files.patch")],
            open_pull_requests=[_make_open_pull_request("ghci/patch/prod-2-9-41")],
            head_commit=_make_commit("A commit pushed by a human"),
        )

        mock_create_subprocess_exec, _ = _mock_subprocess(push_returncode=1, push_stderr=_PROTECTED_STDERR)
        mock_pull_request = MagicMock()
        mock_pull_request.html_url = "https://github.com/camptocamp/test-repo/pull/5"

        with (
            patch("githubkit.webhooks.parse_obj", return_value=event_data),
            patch(
                "github_app_geo_project.module.patch.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=_mock_working_tree(tmp_path),
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.has_changes",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_commit",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "github_app_geo_project.module.patch.module_utils.create_pull_request",
                new_callable=AsyncMock,
                return_value=(True, mock_pull_request),
            ) as mock_create_pr,
            patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec),
        ):
            result = await patch_module.process(mock_context)

        assert result.success is not False
        mock_create_pr.assert_called_once()
        assert mock_create_pr.call_args[0][1] == "ghci/patch/prod-2-9-43"
