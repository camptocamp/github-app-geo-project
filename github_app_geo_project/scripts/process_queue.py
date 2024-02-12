"""
Process the jobs present in the database queue.
"""

import argparse
import time
from datetime import datetime

import c2cwsgiutils.setup_process
import plaster
import sqlalchemy.orm

import github_app_geo_project.models


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    c2cwsgiutils.setup_process.fill_arguments(parser)
    args = parser.parse_args()

    c2cwsgiutils.setup_process.init(args.config_uri)

    config_loader = plaster.get_loader(args.config_uri)
    engine = sqlalchemy.engine_from_config(config_loader.get_settings("database"))
    Session = sqlalchemy.orm.sessionmaker(bind=engine)  # pylint: disable=invalid-name

    while True:
        with Session() as session:
            job = (
                session.query(github_app_geo_project.models.Queue)
                .filter(
                    github_app_geo_project.models.Queue.status == github_app_geo_project.models.STATUS_NEW
                )
                .order_by(
                    github_app_geo_project.models.Queue.priority.desc(),
                    github_app_geo_project.models.Queue.created_at.asc(),
                )
                .with_for_update(of=github_app_geo_project.models.Queue, skip_locked=True)
                .first()
            )
            if job is not None:
                job.status = github_app_geo_project.models.STATUS_PENDING
                job.started_at = datetime.now()
                session.commit()

        if job is None:
            time.sleep(1)
            break


if __name__ == "__main__":
    _main()
