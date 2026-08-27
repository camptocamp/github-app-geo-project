# Copyright (c) 2026, Camptocamp SA

"""Tests for the patch module."""

from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from github_app_geo_project import module
from github_app_geo_project.module.patch import Patch


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


def _setup_process_mocks(mock_context, mock_github_project, head_branch="main", run_id=12345):
    mock_context.module_event_name = "workflow_run"
    mock_context.github_event_data = {}

    workflow_run, sender, workflow_def = _make_mock_workflow_run(head_branch=head_branch, run_id=run_id)
    event_data = MagicMock()
    event_data.action = "completed"
    event_data.workflow_run = workflow_run
    event_data.sender = sender
    event_data.workflow = workflow_def

    artifacts_response = MagicMock()
    artifacts_response.parsed_data.artifacts = [_make_artifact()]
    mock_github_project.aio_github.rest.actions.async_list_workflow_run_artifacts = AsyncMock(
        return_value=artifacts_response,
    )

    mock_github_project.aio_github.rest.repos.async_get_branch = AsyncMock()

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
