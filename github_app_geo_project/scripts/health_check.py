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
    args = parser.parse_args()

    blocked_time = time.time() - WATCH_DOG_FILE.stat().st_mtime

    if blocked_time > args.timeout / 2:
        print(
            f"WARNING: the process-queue event loop seems blocked since {blocked_time:.0f}s "
            f"(timeout: {args.timeout}s).",
            flush=True,
        )
        subprocess.run(["ls", "-l", "/var/ghci/"], check=False)  # noqa: S607
        subprocess.run(["cat", "/var/ghci/job_info"], check=False)  # noqa: S607
        subprocess.run(["ps", "aux"], check=False)  # noqa: S607
    if blocked_time > args.timeout:
        print(
            f"ERROR: the process-queue event loop is blocked since {blocked_time:.0f}s "
            f"(more than the timeout of {args.timeout}s), marking the container as unhealthy.",
            flush=True,
        )
        sys.exit(1)
