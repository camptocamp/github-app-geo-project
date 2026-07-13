"""Send an event in the database queue."""

import argparse
import asyncio

import sqlalchemy.ext.asyncio

import github_app_geo_project.models
import github_app_geo_project.module
from github_app_geo_project.settings import _AppConfig, settings


async def _main() -> None:
    """Add an event in the application queue."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--application",
        required=True,
        help="The application concerned by the event",
    )
    parser.add_argument(
        "--event",
        required=True,
        help="The event name to send",
    )
    args = parser.parse_args()

    app_config: _AppConfig | None = settings.application_configs.get(args.application)
    engine = sqlalchemy.ext.asyncio.create_async_engine(settings.sqlalchemy.async_url)
    async_session = sqlalchemy.ext.asyncio.async_sessionmaker(engine)

    async with async_session() as session:
        job = github_app_geo_project.models.Queue()
        job.application = args.application
        job.github_event_name = "event"
        job.github_event_data = {
            "type": "event",
            "name": args.event,
        }
        job.module = "dispatcher"
        job.module_event_name = "event"
        job.module_event_data = {
            "modules": app_config.modules if app_config else [],
        }
        job.priority = github_app_geo_project.module.PRIORITY_CRON
        session.add(job)
        await session.commit()

    await engine.dispose()


def main() -> None:
    """Run the main event sending function."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()
