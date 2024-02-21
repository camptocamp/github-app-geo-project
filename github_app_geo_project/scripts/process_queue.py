"""
Process the jobs present in the database queue.
"""

import argparse
import logging
import time
from datetime import datetime

import c2cwsgiutils.loader
import c2cwsgiutils.setup_process
import plaster
import sqlalchemy.orm

from github_app_geo_project import configuration, models, module
from github_app_geo_project.module import modules

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
            if job is not None:
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
        try:
            with Session() as session:
                context = module.ProcessContext(
                    session=session,
                    github_application=configuration.get_github_application(
                        config, job_application, owner, repository
                    ),
                    owner=owner,
                    repository=repository,
                    event_data=event_data,
                    module_config=configuration.get_configuration(config, owner, repository),
                    module_data=module_data,
                )
                modules.MODULES[job_module].process(context)
                session.execute(sqlalchemy.delete(models.Queue).where(models.Queue.id == job_id))
                session.commit()
        except Exception:
            _LOGGER.exception("Failed to process job id=%s on module %s.", job_id, job_module)
            with Session() as session:
                job = session.execute(
                    sqlalchemy.update(models.Queue).get(job_id).values(status=models.JobStatus.ERROR)
                )
                session.commit()

        if job is None:
            time.sleep(1)
            break


if __name__ == "__main__":
    main()
