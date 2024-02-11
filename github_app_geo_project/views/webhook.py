import logging
from typing import Any

import pyramid.request
from pyramid.view import view_config

_LOGGER = logging.getLogger(__name__)


@view_config(route_name="webhook", renderer="json")  # type: ignore
def webhook(request: pyramid.request.Request) -> dict[str, Any]:
    """Receive GitHub application webhook URL."""
    application = self.request.matchdict["application"]

    MODULES[application]

    return {}
