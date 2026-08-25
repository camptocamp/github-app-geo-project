# Copyright (c) 2026, Camptocamp SA

"""Script used to check the health of the process-queue daemon."""

import argparse
import subprocess  # nosec
import sys
import time
from pathlib import Path

WATCH_DOG_FILE = Path("/var/ghci/watch_dog")


def main() -> None:
    """Check the health of the process-queue daemon."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, required=True, help="Timeout in seconds")
    parser.add_argument(
        "--warning-timeout", type=int, help="Warning timeout in seconds, default is half of timeout"
    )
    args = parser.parse_args()

    warning_timeout = args.timeout / 2 if args.warning_timeout is None else args.warning_timeout
    blocked_time = time.time() - WATCH_DOG_FILE.stat().st_mtime

    if blocked_time > args.timeout:
        print(
            f"ERROR: the process-queue event loop is blocked since {blocked_time:.0f}s "
            f"(more than the timeout of {args.timeout}s), marking the container as unhealthy.",
            flush=True,
        )
    elif blocked_time > warning_timeout:
        print(
            f"WARNING: the process-queue event loop seems blocked since {blocked_time:.0f}s "
            f"(timeout: {args.timeout}s).",
            flush=True,
        )

    if blocked_time > warning_timeout:
        subprocess.run(["ls", "-l", "/var/ghci/"], check=False)  # noqa: S607
        subprocess.run(["cat", "/var/ghci/job_info"], check=False)  # noqa: S607
        subprocess.run(["ps", "aux"], check=False)  # noqa: S607

    if blocked_time > args.timeout:
        sys.exit(1)
