"""Security policy for the application."""

import hashlib
import hmac
import logging
import os
from typing import Optional, Union

import c2cwsgiutils.auth
import pyramid.request
from pyramid.security import Allowed, Denied

_LOGGER = logging.getLogger(__name__)


class User:
    """User object for the application."""

    auth_type: str
    login: Optional[str]
    name: Optional[str]
    url: Optional[str]
    is_auth: bool
    token: Optional[str]
    is_admin: bool
    request: pyramid.request.Request

    def __init__(
        self,
        auth_type: str,
        login: Optional[str],
        name: Optional[str],
        url: Optional[str],
        is_auth: bool,
        token: Optional[str],
        request: pyramid.request.Request,
    ) -> None:
        """Initialize the user."""
        self.auth_type = auth_type
        self.login = login
        self.name = name
        self.url = url
        self.is_auth = is_auth
        self.token = token
        self.request = request
        self.is_admin = c2cwsgiutils.auth.check_access(self.request) if token is not None else False

    def has_access(self, auth_config: c2cwsgiutils.auth.AuthConfig) -> bool:
        """Check if the user has access to the given configuration."""
        return self.is_admin or c2cwsgiutils.auth.check_access_config(self.request, auth_config)


class SecurityPolicy:
    """Security policy for the application."""

    def identity(self, request: pyramid.request.Request) -> User:
        """Return app-specific user object."""
        if not hasattr(request, "user"):
            user = None

            if "TEST_USER" in os.environ:
                user = User(
                    auth_type="test_user",
                    login=os.environ["TEST_USER"],
                    name=os.environ["TEST_USER"],
                    url="https://example.com/user",
                    is_auth=True,
                    token=None,
                    request=request,
                )
            elif "X-Hub-Signature-256" in request.headers and "GITHUB_SECRET" in os.environ:
                our_signature = hmac.new(
                    key=os.environ["GITHUB_SECRET"].encode("utf-8"),
                    msg=request.body,
                    digestmod=hashlib.sha256,
                ).hexdigest()
                if hmac.compare_digest(
                    our_signature, request.headers["X-Hub-Signature-256"].split("=", 1)[1]
                ):
                    user = User("github_webhook", None, None, None, True, None, request)
                else:
                    _LOGGER.warning("Invalid GitHub signature")
                    _LOGGER.debug(
                        """Incorrect GitHub signature
GitHub signature: %s
Our signature: %s
Content length: %i
body:
%s
---""",
                        request.headers["X-Hub-Signature-256"],
                        our_signature,
                        len(request.body),
                        request.body,
                    )

            else:
                is_auth, c2cuser = c2cwsgiutils.auth.is_auth_user(request)
                if is_auth:
                    user = User(
                        "github_oauth",
                        c2cuser.get("login"),
                        c2cuser.get("name"),
                        c2cuser.get("url"),
                        is_auth,
                        c2cuser.get("token"),
                        request,
                    )

            setattr(request, "user", user)

        return request.user  # type: ignore

    def authenticated_userid(self, request: pyramid.request.Request) -> Optional[str]:
        """Return a string ID for the user."""
        identity = self.identity(request)

        if identity is None:
            return None

        return identity.login

    def permits(
        self, request: pyramid.request.Request, auth_config: c2cwsgiutils.auth.AuthConfig, permission: str
    ) -> Union[Allowed, Denied]:
        """Allow access to everything if signed in."""
        identity = self.identity(request)

        if identity is None:
            return Denied("User is not signed in.")
        if identity.auth_type in ("github_webhook", "test_user"):
            return Allowed(f"All access auth type: {identity.auth_type}")
        if identity.is_admin:
            return Allowed("The User is admin.")
        if permission == "all":
            return Denied("Root access is required.")
        if identity.has_access(auth_config):
            return Allowed(f"The User has access to repo {permission}.")
        return Denied(f"The User has no access to repo {permission}.")
