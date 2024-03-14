"""
Process the jobs present in the database queue.
"""

import argparse
import logging
import os
import time
from datetime import datetime, timedelta

import c2cwsgiutils.loader
import c2cwsgiutils.setup_process
import github.GithubException
import plaster
import sqlalchemy.orm

from github_app_geo_project import application_configuration, configuration, models, module, utils
from github_app_geo_project.module import modules
from github_app_geo_project.views import webhook

_LOGGER = logging.getLogger(__name__)


def main() -> None:
    """Process the jobs present in the database queue."""
    parser = argparse.ArgumentParser(description=__doc__)
    c2cwsgiutils.setup_process.fill_arguments(parser)
    args = parser.parse_args()

    c2cwsgiutils.setup_process.init(args.config_uri)
    loader = plaster.get_loader(args.config_uri)
    config = loader.get_settings("app:app")
    engine = sqlalchemy.engine_from_config(config, "sqlalchemy.")
    Session = sqlalchemy.orm.sessionmaker(bind=engine)  # pylint: disable=invalid-name

    # Create tables if they do not exist
    models.Base.metadata.create_all(engine)

    while True:
        with Session() as session:
            job = (
                session.query(models.Queue)
                .filter(
                    models.Queue.status == models.JobStatus.NEW,
                )
                .order_by(
                    models.Queue.priority.desc(),
                    models.Queue.created_at.asc(),
                )
                .with_for_update(of=models.Queue, skip_locked=True)
                .first()
            )
            if job is None:
                # Get too old pending jobs
                session.execute(
                    sqlalchemy.update(models.Queue)
                    .where(
                        models.Queue.status == models.JobStatus.PENDING,
                        models.Queue.started_at
                        < datetime.now() - timedelta(seconds=int(os.environ.get("JOB_TIMEOUT", 3600))),
                    )
                    .values(status=models.JobStatus.NEW)
                )

                time.sleep(1)
                continue

            job.status = models.JobStatus.PENDING
            job.started_at = datetime.now()
            session.commit()

            job_id = job.id
            job_application = job.application
            job_module = job.module
            owner = job.owner
            repository = job.repository
            event_data = job.event_data
            module_data = job.module_data
            with Session() as session:
                session.commit()
        try:
            with Session() as session:
                if event_data.get("type") == "event":
                    github_objects = configuration.get_github_objects(config, job_application)
                    if "TEST_APPLICATION" in os.environ:
                        webhook.process_event(
                            webhook.ProcessContext(
                                job_application,
                                config,
                                os.environ.get("TEST_OWNER", "camptocamp"),
                                os.environ.get("TEST_REPO", "test"),
                                event_data,
                                session,
                            )
                        )
                    else:
                        for installation in github_objects.integration.get_installations():
                            for repo in installation.get_repos():
                                webhook.process_event(
                                    webhook.ProcessContext(
                                        job_application,
                                        config,
                                        repo.owner.login,
                                        repo.name,
                                        event_data,
                                        session,
                                    )
                                )
                    continue

                current_module = modules.MODULES.get(job_module)
                if current_module is None:
                    _LOGGER.error("Unknown module %s", job_module)
                    continue

                github_application = configuration.get_github_application(
                    config, job_application, owner, repository
                )

                issue_data = ""
                if current_module.required_issue_dashboard():
                    github_objects = configuration.get_github_objects(config, job_application)
                    repository_full = f"{owner}/{repository}"
                    repo = github_application.get_repo(repository_full)
                    open_issues = repo.get_issues(
                        state="open", creator=github_objects.integration.get_app().owner
                    )
                    if open_issues.totalCount > 0:
                        issue_full_data = open_issues[0].body
                        issue_data = utils.get_dashboard_issue_module(issue_full_data, job_module)

                module_config = configuration.get_configuration(
                    config, owner, repository, job_application
                ).get(job_module, {})
                if module_config.get("enabled", application_configuration.MODULE_ENABLED_DEFAULT):  # type: ignore[attr-defined]
                    context = module.ProcessContext(
                        session=session,
                        github_application=github_application,
                        owner=owner,
                        repository=repository,
                        event_data=event_data,
                        module_config=module_config,
                        module_data=module_data,
                        issue_data=issue_data,
                    )
                    try:
                        new_issue_data = current_module.process(context)
                    except github.GithubException.GithubException as exception:
                        _LOGGER.exception(
                            "Failed to process job id: %s on module: %s, module data:\n%s\nevent data:\n%s\nreturn data:\n%s\nreturn headers:\n%s\nreturn message:\n%s\nreturn status: %s",
                            job_id,
                            job_module,
                            module_data,
                            event_data,
                            exception.data,
                            "\n".join(f"{k}: {v}" for k, v in exception.headers.items()),
                            exception.message,
                            exception.status,
                        )
                        raise
                    except Exception:
                        _LOGGER.exception(
                            "Failed to process job id: %s on module: %s, module data:\n%s\nevent data:\n%s",
                            job_id,
                            job_module,
                            module_data,
                            event_data,
                        )
                        raise

                session.execute(
                    sqlalchemy.update(models.Queue)
                    .where(models.Queue.id == job_id)
                    .values(status=models.JobStatus.DONE)
                )
                session.commit()

            if current_module.required_issue_dashboard() and new_issue_data is not None:
                open_issues = repo.get_issues(
                    state="open", creator=github_objects.integration.get_app().owner
                )
                if open_issues.totalCount > 0:
                    issue_full_data = utils.update_dashboard_issue_module(
                        open_issues[0].body, job_module, current_module, new_issue_data
                    )
                    open_issues[0].edit(body=issue_full_data)
                elif new_issue_data:
                    repo.create_issue(
                        "GHCI Dashboard",
                        f"This issue is the dashboard used by GHCI modules.\n\n[Project on GHCI]({config['service-url']}project/{owner}/{repository})\n\n"
                        + new_issue_data,
                    )

        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Failed to process job id: %s on module: %s.", job_id, job_module)
            with Session() as session:
                session.execute(
                    sqlalchemy.update(models.Queue)
                    .where(models.Queue.id == job_id)
                    .values(status=models.JobStatus.ERROR)
                )
                session.commit()


if __name__ == "__main__":
    main()
