"""FastAPI application entry point."""

import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import c2casgiutils
import c2casgiutils.config
import c2casgiutils.headers
import sentry_sdk
from c2casgiutils import health_checks
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import start_http_server
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.templating import Jinja2Templates

from github_app_geo_project.settings import settings
from github_app_geo_project.templates import (
    markdown,
    pprint_date,
    pprint_duration,
    pprint_full_date,
    pprint_short_date,
    sanitizer,
)
from github_app_geo_project.views.dashboard import DashboardData
from github_app_geo_project.views.home import HomeData
from github_app_geo_project.views.jobs import JobsData
from github_app_geo_project.views.logs import LogsData
from github_app_geo_project.views.output import OutputByNameData
from github_app_geo_project.views.project import ProjectData
from github_app_geo_project.views.schema import SchemaData
from github_app_geo_project.views.webhook import WebhookData
from github_app_geo_project.views.welcome import WelcomeData

_LOGGER = logging.getLogger(__name__)

if c2casgiutils.config.settings.sentry.dsn or "SENTRY_DSN" in os.environ:
    _LOGGER.info(
        "Sentry is enabled with URL: %s",
        c2casgiutils.config.settings.sentry.dsn or os.environ.get("SENTRY_DSN"),
    )
    sentry_sdk.init(
        **{
            k: v
            for k, v in c2casgiutils.config.settings.sentry.model_dump().items()
            if v is not None and k != "tags"
        },
    )

    for tag, value in c2casgiutils.config.settings.sentry.tags.items():
        sentry_sdk.set_tag(tag, value)

templates = Jinja2Templates(directory=str(settings.template_dir))
templates.env.globals["markdown"] = markdown
templates.env.globals["sanitizer"] = sanitizer
templates.env.globals["pprint_date"] = pprint_date
templates.env.globals["pprint_short_date"] = pprint_short_date
templates.env.globals["pprint_full_date"] = pprint_full_date
templates.env.globals["pprint_duration"] = pprint_duration
templates.context_processors.append(
    lambda request: {"nonce": getattr(request.state, "nonce", "")},
)
for _filter in (
    "markdown",
    "sanitizer",
    "pprint_date",
    "pprint_short_date",
    "pprint_full_date",
    "pprint_duration",
):
    templates.env.filters[_filter] = globals()[_filter]


@asynccontextmanager
async def _lifespan(main_app: FastAPI) -> AsyncIterator[None]:
    _LOGGER.info("Starting the application")
    _LOGGER.debug("Settings: %s", settings.model_dump())

    await c2casgiutils.startup(main_app)

    main_app.state.async_engine = create_async_engine(settings.sqlalchemy.async_url)
    main_app.state.async_session_factory = async_sessionmaker(main_app.state.async_engine)

    health_checks.FACTORY.add(
        health_checks.SQLAlchemy(
            main_app.state.async_session_factory,
            tags=["liveness", "sqlalchemy", "all"],
        ),
    )
    health_checks.FACTORY.add(health_checks.Redis(tags=["liveness", "redis", "all"]))

    if c2casgiutils.config.settings.prometheus.port > 0:
        start_http_server(c2casgiutils.config.settings.prometheus.port)

    yield

    await main_app.state.async_engine.dispose()
    _LOGGER.info("Application stopped")


app = FastAPI(title="GitHub App Geo Project", lifespan=_lifespan)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

route_prefix = c2casgiutils.config.settings.route_prefix
route_prefix_escaped = re.escape(route_prefix[1:])
_LOGGER.info("Using route prefix: '%s'", route_prefix)

_Header = str | list[str] | dict[str, str] | dict[str, list[str]] | None
_ui_csp_headers: dict[str, _Header] = {
    "Content-Security-Policy": {
        "default-src": ["'self'"],
        "script-src-elem": [
            "'self'",
            c2casgiutils.headers.CSP_NONCE,
            "https://cdnjs.cloudflare.com/ajax/libs/bootstrap/",
            "https://cdnjs.cloudflare.com/ajax/libs/jquery/",
            "https://cdnjs.cloudflare.com/ajax/libs/popper.js/",
        ],
        "style-src-elem": [
            "'self'",
            c2casgiutils.headers.CSP_NONCE,
            "https://cdnjs.cloudflare.com/ajax/libs/bootstrap/",
            "https://cdnjs.cloudflare.com/ajax/libs/bootstrap-icons/",
        ],
        "font-src": [
            "'self'",
            "https://cdnjs.cloudflare.com/ajax/libs/bootstrap-icons/",
        ],
        "img-src": [
            "'self'",
            "data:",
        ],
        "connect-src": [
            "'self'",
            "https://cdnjs.cloudflare.com/ajax/libs/bootstrap/",
            "https://cdnjs.cloudflare.com/ajax/libs/jquery/",
            "https://cdnjs.cloudflare.com/ajax/libs/popper.js/",
        ],
    },
}

_ui_path_match = (
    rf"^{route_prefix_escaped}(?:welcome|project/.*|dashboard/.*|output/[0-9]+|logs/[0-9]+|jobs)?$"
)

app.add_middleware(
    c2casgiutils.headers.ArmorHeaderMiddleware,
    headers_config={
        "http": {"headers": {"Strict-Transport-Security": None}}
        if c2casgiutils.config.settings.http
        else {"headers": {}},
        "ui": {
            "path_match": _ui_path_match,
            "headers": _ui_csp_headers,
            "status_code": 200,
        },
        "ui_302": {
            "path_match": _ui_path_match,
            "headers": _ui_csp_headers,
            "status_code": 302,
        },
    },
)

if c2casgiutils.config.settings.proxy_headers.type != "none":
    app.add_middleware(
        c2casgiutils.headers.ForwardedHeadersMiddleware,
        trusted_hosts=c2casgiutils.config.settings.proxy_headers.trusted_hosts,
        headers_type=c2casgiutils.config.settings.proxy_headers.type,
    )

app.mount(
    f"{route_prefix}static",
    StaticFiles(directory="/app/github_app_geo_project/static"),
    name="static",
)

instrumentator = Instrumentator(should_instrument_requests_inprogress=True)
instrumentator.instrument(app)


@app.get(f"{route_prefix}c2c")
async def redirect_c2c(request: Request) -> RedirectResponse:
    """Redirect to the mounted c2c app canonical path."""
    url = request.url
    redirect_url = url.path + "/"
    if url.query:
        redirect_url += f"?{url.query}"
    return RedirectResponse(url=redirect_url, status_code=307)


app.mount(f"{route_prefix}c2c", c2casgiutils.app)  # C2C utility routes


@app.get(f"{route_prefix}welcome")
async def welcome_route(request: Request, data: WelcomeData) -> HTMLResponse:
    """Render the welcome page."""
    return templates.TemplateResponse(request, "welcome.html", data)


@app.get(f"{route_prefix}")
async def home_route(request: Request, data: HomeData) -> HTMLResponse:
    """Render the home page."""
    return templates.TemplateResponse(request, "home.html", data)


@app.get(f"{route_prefix}project/{{owner}}/{{repository}}")
async def project_route(request: Request, data: ProjectData) -> HTMLResponse:
    """Render the project page."""
    return templates.TemplateResponse(request, "project.html", data)


@app.get(f"{route_prefix}jobs")
async def jobs_route(request: Request, data: JobsData) -> HTMLResponse:
    """Render the jobs page."""
    return templates.TemplateResponse(request, "jobs.html", data)


@app.post(f"{route_prefix}webhook/{{application}}")
async def webhook_route(data: WebhookData) -> dict[str, None]:
    """Handle incoming webhooks."""
    return data


@app.get(f"{route_prefix}dashboard/{{module_name}}")
async def dashboard_route(request: Request, data: DashboardData) -> HTMLResponse:
    """Render the dashboard for a module."""
    return templates.TemplateResponse(request, "dashboard.html", data)


@app.get(f"{route_prefix}schema.json")
async def schema_route(data: SchemaData) -> dict[str, Any]:
    """Return the JSON schema."""
    return data


@app.get(f"{route_prefix}output/{{owner}}/{{repository}}/{{name}}")
async def output_route(request: Request, data: OutputByNameData) -> HTMLResponse:
    """Render the output page by owner/repository/name."""
    renderer = data.pop("renderer", None)
    renderer_data = data.pop("renderer_data", None)
    template_kwargs = {**data}
    if renderer_data:
        template_kwargs["renderer_data"] = renderer_data
    return templates.TemplateResponse(
        request,
        renderer,
        template_kwargs,
    )


@app.get(f"{route_prefix}logs/{{logs_id}}")
async def logs_route(request: Request, data: LogsData) -> HTMLResponse:
    """Render the logs page."""
    return templates.TemplateResponse(request, "logs.html", data)
