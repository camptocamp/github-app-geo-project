"""
Send an event in the database queue.
"""

import argparse
import os

import c2cwsgiutils.setup_process
from sqlalchemy.engine import create_engine
from sqlalchemy.orm import sessionmaker

import github_app_geo_project.models


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event",
        required=True,
        help="The event name to send",
    )
    c2cwsgiutils.setup_process.fill_arguments(parser)
    args = parser.parse_args()

    c2cwsgiutils.setup_process.init(args.config_uri)

    engine = create_engine(os.environ["SQLALCHEMY_URI"])
    SessionMaker = sessionmaker(engine)  # noqa

    with SessionMaker() as session:
        job = github_app_geo_project.models.Queue()
        job.data = {
            "type": "event",
            "name": args.event,
        }
        session.add(job)
        session.commit()


if __name__ == "__main__":
    main()
