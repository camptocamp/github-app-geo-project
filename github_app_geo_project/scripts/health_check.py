"""Script used to check the health of the process-queue daemon."""

import argparse
import subprocess  # nosec
import sys
import time
from pathlib import Path


def main() -> None:
    """Check the health of the process-queue daemon."""
    parser = argparse.ArgumentParser(description="Check the health of the process-queue daemon")
    parser.add_argument("--timeout", type=int, help="Timeout in seconds")
    args = parser.parse_args()

    blocked_time = time.time() - Path("/var/ghci/watch_dog").stat().st_mtime

    if blocked_time > args.timeout / 2:
        subprocess.run(["ls", "-l", "/var/ghci/"], check=False)  # pylint: disable=subprocess-run-check # noqa: S607,RUF100
        subprocess.run(["cat", "/var/ghci/job_info"], check=False)  # pylint: disable=subprocess-run-check # noqa: S607,RUF100
        subprocess.run(["ps", "aux"], check=False)  # pylint: disable=subprocess-run-check # noqa: S607,RUF100
    if blocked_time > args.timeout:
        sys.exit(1)
