"""Module to clean local tool caches (pip, poetry, pyenv, prek, pre-commit, npm)."""

import logging
import logging.handlers
import shutil
from pathlib import Path
from typing import Any

import anyio
import githubkit.exception
from pydantic import BaseModel

from github_app_geo_project import configuration, module
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.settings import settings

_LOGGER = logging.getLogger(__name__)


class _EventData(BaseModel):
    """Event data for the cache clean module."""


class CacheClean(module.Module[None, _EventData, None, None]):
    """Module to clean local tool caches."""

    def title(self) -> str:
        """Get the title of the module."""
        return "Cache clean"

    def description(self) -> str:
        """Get the description of the module."""
        return "Clean local tool caches (pip, poetry, pyenv, prek, pre-commit, npm)"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/blob/master/github_app_geo_project/module/cache_clean/README.md"

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the module."""
        return {}

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            permissions={},
            events=set(),
        )

    def jobs_unique_on(self) -> list[module.Fields] | None:
        """Return the list of fields that should be unique for the jobs."""
        return [module.Fields.MODULE_EVENT_NAME, module.Fields.PRIORITY]

    def get_actions(
        self,
        context: module.GetActionContext,
    ) -> list[module.Action[_EventData]]:
        """Get the action related to the module and the event."""
        if (
            context.github_event_data.get("type") == "event"
            and context.github_event_data.get("name") == "cache-clean"
        ):
            return [
                module.Action(
                    data=_EventData(),
                    priority=module.PRIORITY_CRON + 10,
                ),
            ]
        return []

    async def process(
        self,
        context: module.ProcessContext[None, _EventData],
    ) -> module.ProcessOutput[_EventData, None]:
        """Process the action."""
        await _setup_logger()
        _LOGGER.debug("Starting cache clean")

        home = await anyio.Path.home()

        cache_configs = [
            CacheConfig(
                path=home / ".cache" / "pip",
                max_size=settings.cache_clean.pip_max_size,
                commands=[
                    CacheCommand(["pip", "cache", "purge"], "pip cache purge"),
                ],
                delete=True,
                label="pip",
            ),
            CacheConfig(
                path=home / ".cache" / "pypoetry" / "artifacts",
                max_size=settings.cache_clean.poetry_artifacts_max_size,
                commands=[
                    CacheCommand(
                        ["poetry", "cache", "clear", "--all"],
                        "poetry cache clear --all",
                    ),
                ],
                delete=True,
                label="poetry artifacts",
            ),
            CacheConfig(
                path=home / ".cache" / "pypoetry" / "virtualenvs",
                max_size=settings.cache_clean.poetry_virtualenvs_max_size,
                commands=[],
                delete=True,
                label="poetry virtualenvs",
            ),
            CacheConfig(
                path=home / ".pyenv" / "cache",
                max_size=settings.cache_clean.pyenv_max_size,
                commands=[],
                delete=True,
                label="pyenv cache",
            ),
            CacheConfig(
                path=home / ".cache" / "prek",
                max_size=settings.cache_clean.prek_max_size,
                commands=[],
                delete=True,
                label="prek",
            ),
            CacheConfig(
                path=home / ".cache" / "pre-commit",
                max_size=settings.cache_clean.pre_commit_max_size,
                commands=[],
                delete=True,
                label="pre-commit",
            ),
            CacheConfig(
                path=home / ".npm",
                max_size=settings.cache_clean.npm_max_size,
                commands=[
                    CacheCommand(["npm", "cache", "clean"], "npm cache clean"),
                    CacheCommand(
                        ["npm", "cache", "clean", "--force"],
                        "npm cache clean --force",
                    ),
                ],
                delete=True,
                label="npm",
            ),
        ]

        for config in cache_configs:
            await _process_cache_config(config)

        # Clean the git worktree cache: prune stale worktrees and run gc
        await _clean_git_repositories(home / ".cache" / "ghci" / "git", context.github_project)

        _LOGGER.debug("Cache clean completed")
        return module.ProcessOutput()


class CacheCommand:
    """A command to run for cache cleaning."""

    def __init__(self, args: list[str], label: str) -> None:
        self.args = args
        self.label = label


class CacheConfig:
    """Configuration for a cache directory."""

    def __init__(
        self,
        path: anyio.Path,
        max_size: int,
        commands: list[CacheCommand],
        delete: bool,
        label: str,
    ) -> None:
        self.path = path
        self.max_size = max_size
        self.commands = commands
        self.delete = delete
        self.label = label


async def _setup_logger() -> None:
    """Set up the rotating log file for cache clean."""
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in _LOGGER.handlers):
        return
    log_dir = await anyio.Path.home() / ".cache"
    await log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "clean.log"

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=settings.cache_clean.log_max,
        backupCount=settings.cache_clean.log_backup_count,
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"),
    )
    file_handler.setLevel(logging.INFO)
    _LOGGER.addHandler(file_handler)


async def _get_directory_size(path: anyio.Path) -> int | None:
    """Get the total size of a directory in bytes using du -sb."""
    if not await path.exists():
        return None
    try:
        stdout, success, _ = await module_utils.run_timeout(
            ["du", "-sb", str(path)],
            None,
            30,
            success_message="",
            error_message="",
            timeout_message="",
            cwd=Path("/"),
            error=False,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Failed to get directory size for %s", path)
        return None
    else:
        if success and stdout:
            size_str = stdout.split("\t")[0]
            return int(size_str)
        return None


async def _run_command(command: list[str], label: str) -> bool:
    """Run a cache cleaning command."""
    try:
        _, success, _ = await module_utils.run_timeout(
            command,
            None,
            120,
            success_message="",
            error_message="",
            timeout_message="",
            cwd=Path("/"),
            error=False,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Command '%s' failed with exception", label)
        return False
    else:
        if success:
            _LOGGER.debug("Command '%s' succeeded", label)
        else:
            _LOGGER.warning("Command '%s' failed", label)
        return success


def _delete_directory(path: anyio.Path) -> bool:
    """Delete a directory and its contents."""
    try:
        shutil.rmtree(path)
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Failed to delete directory: %s", path)
        return False
    else:
        _LOGGER.debug("Deleted directory: %s", path)
        return True


async def _process_cache_config(config: CacheConfig) -> None:
    """Process a single cache configuration."""
    size_bytes = await _get_directory_size(config.path)
    if size_bytes is None:
        _LOGGER.debug("Directory %s does not exist, skipping", config.path)
        return

    if size_bytes <= config.max_size:
        size_mb = size_bytes / (1024 * 1024)
        max_size_mb = config.max_size / (1024 * 1024)
        _LOGGER.debug(
            "Cache %s: %.1f MiB (limit: %d MiB), within limits, skipping",
            config.label,
            size_mb,
            max_size_mb,
        )
        return

    size_mb = size_bytes / (1024 * 1024)
    max_size_mb = config.max_size / (1024 * 1024)
    _LOGGER.info(
        "Cleaning cache %s: %.1f MiB (limit: %d MiB)",
        config.label,
        size_mb,
        max_size_mb,
    )

    for cmd in config.commands:
        await _run_command(cmd.args, cmd.label)

    size_bytes = await _get_directory_size(config.path)
    if size_bytes is not None:
        size_mb = size_bytes / (1024 * 1024)
        if size_bytes > config.max_size:
            _LOGGER.warning(
                "Cache %s still over limit (%.1f MiB) after commands, deleting",
                config.label,
                size_mb,
            )
            if config.delete:
                _delete_directory(config.path)
        else:
            _LOGGER.debug(
                "Cache %s now within limits (%.1f MiB) after commands",
                config.label,
                size_mb,
            )


async def _clean_git_repositories(
    cache_path: anyio.Path, github_project: configuration.GithubProject
) -> None:
    """Run git maintenance on all repositories in the git worktree cache."""
    if not await cache_path.exists():
        return
    _LOGGER.debug("Running git maintenance on cached repositories in %s", cache_path)
    async for owner_dir in cache_path.iterdir():
        if not await owner_dir.is_dir():
            continue
        async for repo_dir in owner_dir.iterdir():
            git_dir = repo_dir / ".git"
            if not await git_dir.exists():
                continue
            owner = owner_dir.name
            repo = repo_dir.name
            # Check if the repository is still accessible and not archived
            try:
                response = await github_project.aio_github.rest.repos.async_get(
                    owner=owner,
                    repo=repo,
                )
                if response.parsed_data.archived:
                    _LOGGER.info("Repository %s/%s is archived, removing cache", owner, repo)
                    shutil.rmtree(repo_dir)
                    continue
            except githubkit.exception.RequestFailed as exception:
                if exception.response.status_code == 404:
                    _LOGGER.info(
                        "Repository %s/%s is no longer accessible, removing cache",
                        owner,
                        repo,
                    )
                    shutil.rmtree(repo_dir)
                    continue
                raise
            # Prune stale worktrees
            try:
                await module_utils.run_timeout(
                    ["git", "worktree", "prune"],
                    None,
                    60,
                    success_message=f"Prune worktrees in {repo_dir.name}",
                    error_message=f"Error pruning worktrees in {repo_dir.name}",
                    timeout_message=f"Timeout pruning worktrees in {repo_dir.name}",
                    cwd=Path(repo_dir),
                    error=False,
                )
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Failed to prune worktrees in %s", repo_dir)
            # Run git gc
            try:
                _LOGGER.debug("Running git gc on %s", repo_dir)
                await module_utils.run_timeout(
                    ["git", "gc", "--auto", "--prune=all"],
                    None,
                    600,
                    success_message=f"Git gc on {repo_dir.name}",
                    error_message=f"Error running git gc on {repo_dir.name}",
                    timeout_message=f"Timeout running git gc on {repo_dir.name}",
                    cwd=Path(repo_dir),
                    error=False,
                )
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Failed to run git gc on %s", repo_dir)
