"""Script used to check the health of the process-queue daemon."""

import argparse
import os
import subprocess  # nosec
import sys
import time


def main() -> None:
    """Check the health of the process-queue daemon."""
    parser = argparse.ArgumentParser(description="Check the health of the process-queue daemon")
    parser.add_argument("--timeout", type=int, help="Timeout in seconds")
    args = parser.parse_args()

    blocked_time = time.time() - os.path.getmtime("/var/ghci/watch_dog")

    if blocked_time > args.timeout / 2:
        subprocess.run(["ls", "-l", "/var/ghci/"])  # pylint: disable=subprocess-run-check
        subprocess.run(["cat", "/var/ghci/job_info"])  # pylint: disable=subprocess-run-check
        subprocess.run(["ps", "aux"])  # pylint: disable=subprocess-run-check
    if blocked_time > args.timeout:
        sys.exit(1)
