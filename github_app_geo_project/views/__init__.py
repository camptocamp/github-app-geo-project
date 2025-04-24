"""The application views."""

import asyncio


def get_event_loop() -> asyncio.AbstractEventLoop:
    """
    Get the current event loop.

    If there is no current event loop, create a new one.
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()
