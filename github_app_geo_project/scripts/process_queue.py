"""
Process the jobs present in the database queue.
"""

import argparse
import os
import time
from datetime import datetime

import c2cwsgiutils.setup_process  # pylint: disable=import-error
from sqlalchemy.engine import create_engine  # pylint: disable=import-error
from sqlalchemy.orm import sessionmaker  # pylint: disable=import-error

import github_app_geo_project.models


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    c2cwsgiutils.setup_process.fill_arguments(parser)
    args = parser.parse_args()

    c2cwsgiutils.setup_process.init(args.config_uri)

    engine = create_engine(os.environ["SQLALCHEMY_URI"])
    SessionMaker = sessionmaker(engine)  # noqa

    while True:
        with SessionMaker() as session:
            job = (
                session.query(github_app_geo_project.models.Queue)
                .filter(
                    github_app_geo_project.models.Queue.status == github_app_geo_project.models.STATUS_NEW
                )
                .order_by(
                    github_app_geo_project.models.Queue.priority.desc(),
                    github_app_geo_project.models.Queue.created_at.asc(),
                )
                .with_for_update(of=github_app_geo_project.models.Queue, skip_locked=True)  # type: ignore[arg-type]
                .first()
            )
            if job is not None:
                job.status = github_app_geo_project.models.STATUS_PENDING
                job.started_at = datetime.now()  # type: ignore[assignment]
                session.commit()

        if job is None:
            time.sleep(1)
            break


if __name__ == "__main__":
    _main()
