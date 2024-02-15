"""
Send an event in the database queue.
"""

import argparse

import c2cwsgiutils.setup_process
import plaster
import sqlalchemy.orm

import github_app_geo_project.models


def main() -> None:
    """Add an event in the application queue."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event",
        required=True,
        help="The event name to send",
    )
    c2cwsgiutils.setup_process.fill_arguments(parser)
    args = parser.parse_args()

    c2cwsgiutils.setup_process.init(args.config_uri)
    loader = plaster.get_loader(args.config_uri)
    engine = sqlalchemy.engine_from_config(loader.get_settings("app:app"), "sqlalchemy.")
    Session = sqlalchemy.orm.sessionmaker(bind=engine)  # pylint: disable=invalid-name

    with Session() as session:
        job = github_app_geo_project.models.Queue()
        job.data = {
            "type": "event",
            "name": args.event,
        }
        session.add(job)
        session.commit()


if __name__ == "__main__":
    main()
