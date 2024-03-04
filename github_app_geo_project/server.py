"""Main entry point for the server."""

import json
import logging
import os
from typing import Any

import c2cwsgiutils.db
import c2cwsgiutils.health_check
import c2cwsgiutils.pyramid
import pyramid.response
import pyramid.session
from pyramid.config import Configurator
from pyramid.router import Router
from pyramid_mako import add_mako_renderer
from sqlalchemy import engine_from_config

from github_app_geo_project import models, security

_LOGGER = logging.getLogger(__name__)


def forbidden(request: pyramid.request.Request) -> pyramid.response.Response:
    """Return a 403 Forbidden response."""
    is_auth = c2cwsgiutils.auth.is_auth(request)

    if is_auth:
        return pyramid.httpexceptions.HTTPForbidden(request.exception.message)
    return pyramid.httpexceptions.HTTPFound(
        location=request.route_url(
            "c2c_github_login",
            _query={"came_from": request.current_route_url()},
        )
    )


def main(global_config: Any, **settings: Any) -> Router:
    """Start the server in Pyramid."""
    del global_config  # unused

    config = Configurator(settings=settings)

    config.set_session_factory(
        pyramid.session.BaseCookieSessionFactory(json)
        if os.environ.get("GHCI_DEBUG_SESSION", "false").lower() == "true"
        else pyramid.session.SignedCookieSessionFactory(
            os.environ["GHCI_SESSION_SECRET"],
            salt=os.environ["GHCI_SESSION_SALT"],
            httponly=True,
            secure=True,
        )
    )

    config.include(c2cwsgiutils.pyramid.includeme)
    dbsession = c2cwsgiutils.db.init(config, "sqlalchemy", "sqlalchemy-slave")

    health_check = c2cwsgiutils.health_check.HealthCheck(config)
    health_check.add_db_session_check(dbsession, at_least_one_model=models.Queue)
    health_check.add_url_check("http://localhost:8080/")

    engine_from_config(settings, "sqlalchemy.")
    config.registry.settings["mako.imports"] = (
        config.registry.settings.get("mako.imports", "")
        + """
from github_app_geo_project.templates import markdown, sanitizer
    """
    )
    add_mako_renderer(config, ".html")
    config.set_security_policy(security.SecurityPolicy())
    config.add_forbidden_view(forbidden)
    config.add_static_view(
        name="/static",
        path="/app/github_app_geo_project/static",
    )

    config.add_route(
        "home",
        "/",
        request_method="GET",
    )
    config.add_route(
        "welcome",
        "/welcome",
        request_method="GET",
    )
    config.add_route(
        "project",
        "/project/{owner}/{repository}",
        request_method="GET",
    )
    config.add_route(
        "webhook",
        "/webhook/{application}",
        request_method="POST",
    )
    config.add_route(
        "output",
        "output/{id}",
        request_method="GET",
    )

    config.scan("github_app_geo_project.views")

    return config.make_wsgi_app()
