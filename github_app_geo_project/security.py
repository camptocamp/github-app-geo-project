"""Security dependencies for the FastAPI application."""

import enum
import hashlib
import hmac
import logging

import c2casgiutils.auth
import c2casgiutils.config
import githubkit
import jwt
from fastapi import Request
from githubkit.utils import Unset

from github_app_geo_project.settings import settings

_LOGGER = logging.getLogger(__name__)


class AuthType(enum.StrEnum):
    """Authentication types for users."""

    ANONYMOUS = "anonymous"
    GITHUB_WEBHOOK = "github_webhook"
    TEST_USER = "test_user"
    GITHUB_OAUTH = "github_oauth"


class User:
    """Application user object."""

    auth_type: AuthType
    login: str | None
    name: str | None
    url: str | None
    is_auth: bool
    token: str | None
    is_admin: bool

    def __init__(
        self,
        auth_type: AuthType | str,
        login: str | None = None,
        name: str | None = None,
        url: str | None = None,
        is_auth: bool = False,
        token: str | None = None,
        is_admin: bool = False,
    ) -> None:
        self.auth_type = AuthType(auth_type)
        self.login = login
        self.name = name
        self.url = url
        self.is_auth = is_auth
        self.token = token
        self.is_admin = is_admin


async def get_user(request: Request) -> User:
    """Get the current user from the request."""
    test_username = c2casgiutils.config.settings.auth.test.username

    if test_username:
        user = User(
            auth_type=AuthType.TEST_USER,
            login=test_username,
            name=test_username,
            url="https://example.com/user",
            is_auth=True,
            is_admin=True,
        )
    elif "X-Hub-Signature-256" in request.headers and settings.webhook.github_secret:
        body = await request.body()
        our_signature = hmac.new(
            key=settings.webhook.github_secret.encode("utf-8"),
            msg=body,
            digestmod=hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(
            our_signature,
            request.headers["X-Hub-Signature-256"].split("=", 1)[1],
        ):
            user = User(AuthType.GITHUB_WEBHOOK, is_auth=True)
        else:
            _LOGGER.warning("Invalid GitHub signature")
            user = User(AuthType.ANONYMOUS)
    else:
        secret = c2casgiutils.config.settings.auth.jwt.secret
        cookie_name = c2casgiutils.config.settings.auth.jwt.cookie.name
        if secret and cookie_name in request.cookies:
            try:
                user_payload = jwt.decode(
                    request.cookies[cookie_name],
                    secret,
                    algorithms=[c2casgiutils.config.settings.auth.jwt.algorithm],
                    options={"require": ["exp", "iat"]},
                )
                user = User(
                    auth_type=AuthType.GITHUB_OAUTH,
                    login=user_payload.get("login"),
                    name=user_payload.get("display_name"),
                    url=user_payload.get("url"),
                    token=user_payload.get("token"),
                    is_auth=True,
                )
            except jwt.ExpiredSignatureError:
                _LOGGER.warning("Expired JWT cookie")
                user = User(AuthType.ANONYMOUS)
            except jwt.InvalidTokenError:
                _LOGGER.warning("Invalid JWT cookie")
                user = User(AuthType.ANONYMOUS)
        else:
            user = User(AuthType.ANONYMOUS)

    return user


async def has_repo_access(user: User, owner: str | None, repository: str | None) -> bool:
    """Check if the user has admin access to the repository."""
    if user.is_admin or user.auth_type in (AuthType.GITHUB_WEBHOOK, AuthType.TEST_USER):
        return True
    if owner is None or repository is None or user.token is None:
        return False
    gh = githubkit.GitHub(githubkit.TokenAuthStrategy(user.token))
    try:
        repo = (await gh.rest.repos.async_get(owner=owner, repo=repository)).parsed_data
    except githubkit.exception.RequestFailed as exception:
        if exception.response.status_code == 404:
            return False
        _LOGGER.exception("Failed to check repository access for %s/%s", owner, repository)
        return False
    if repo.permissions is None or isinstance(repo.permissions, Unset):
        return False
    return repo.permissions.admin
