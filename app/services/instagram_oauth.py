"""Instagram Login protocol calls. Provider secrets never reach the browser."""

import logging
from urllib.parse import urlencode, urlsplit

import httpx

from app.config import settings

SCOPES = ("instagram_business_basic", "instagram_business_content_publish")


class _HideQuery(logging.Filter):
    def filter(self, record):
        # httpx INFO request logs otherwise include token-exchange GET parameters.
        if isinstance(record.args, tuple):
            record.args = tuple(str(arg).split("?", 1)[0] if str(arg).startswith("https://") else arg
                                for arg in record.args)
        return True


logging.getLogger("httpx").addFilter(_HideQuery())


class InstagramOAuthError(ValueError):
    pass


def oauth_configured():
    return bool(settings.instagram_app_id and settings.instagram_app_secret
                and settings.instagram_redirect_uri and settings.instagram_token_encryption_key)


def frontend_origin():
    value = settings.frontend_base_url.rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"})):
        raise InstagramOAuthError("Configure FRONTEND_BASE_URL with the dashboard origin")
    return value


def authorization_url(state):
    if not oauth_configured():
        raise InstagramOAuthError("Instagram login setup is incomplete. Ask the app administrator to finish configuration.")
    parsed = urlsplit(settings.instagram_redirect_uri)
    if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
        raise InstagramOAuthError("Instagram requires a configured HTTPS callback URL")
    frontend_origin()
    return "https://www.instagram.com/oauth/authorize?" + urlencode({
        "client_id": settings.instagram_app_id, "redirect_uri": settings.instagram_redirect_uri,
        "response_type": "code", "scope": ",".join(SCOPES), "state": state,
        "enable_fb_login": "0"})


class InstagramOAuth:
    def __init__(self, transport=None):
        self.transport = transport

    async def _call(self, client, method, url, **kwargs):
        try:
            response = await client.request(method, url, **kwargs)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise InstagramOAuthError("Instagram could not complete the connection. Please try connecting again.") from exc
        if not response.is_success or not isinstance(data, dict) or data.get("error"):
            raise InstagramOAuthError("Instagram rejected the authorization. Check the app settings and try connecting again.")
        return data

    async def exchange(self, code: str, redirect_uri: str):
        async with httpx.AsyncClient(timeout=20, transport=self.transport, follow_redirects=False) as client:
            short = await self._call(client, "POST", "https://api.instagram.com/oauth/access_token", data={
                "client_id": settings.instagram_app_id, "client_secret": settings.instagram_app_secret,
                "grant_type": "authorization_code", "redirect_uri": redirect_uri, "code": code})
            # Some responses wrap the token object in a one-element data array.
            if "data" in short:
                rows = short["data"]
                if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                    raise InstagramOAuthError("Instagram returned an invalid token response")
                short = rows[0]
            if not isinstance(short.get("access_token"), str) or not short["access_token"]:
                raise InstagramOAuthError("Instagram did not return an access token")
            granted = short.get("permissions")
            if isinstance(granted, str):
                granted = [scope.strip() for scope in granted.split(",")]
            if not isinstance(granted, list) or not set(SCOPES).issubset(granted):
                raise InstagramOAuthError("Approve both profile access and content publishing, then connect again.")
            long = await self._call(client, "GET", "https://graph.instagram.com/access_token", params={
                "grant_type": "ig_exchange_token", "client_secret": settings.instagram_app_secret,
                "access_token": short["access_token"]})
            token, lifetime = self._token(long)
            profile = await self._call(client, "GET", f"https://graph.instagram.com/{settings.instagram_api_version}/me",
                params={"fields": "user_id,username,account_type"}, headers={"Authorization": f"Bearer {token}"})
            account_id = str(profile.get("user_id", ""))
            if not account_id.isdigit() or not isinstance(profile.get("username"), str):
                raise InstagramOAuthError("Instagram did not return a valid account identity")
            if profile.get("account_type") not in {"BUSINESS", "MEDIA_CREATOR", "CREATOR"}:
                raise InstagramOAuthError("Connect an Instagram Business or Creator account")
            return {"access_token": token, "expires_in": lifetime, "user_id": account_id,
                    "username": profile["username"], "scopes": list(SCOPES)}

    @staticmethod
    def _token(data):
        token, lifetime = data.get("access_token"), data.get("expires_in")
        if not isinstance(token, str) or not token or isinstance(lifetime, bool) or not isinstance(lifetime, int) or lifetime <= 0:
            raise InstagramOAuthError("Instagram returned an invalid token lifetime")
        return token, lifetime

    async def refresh(self, token):
        async with httpx.AsyncClient(timeout=20, transport=self.transport, follow_redirects=False) as client:
            data = await self._call(client, "GET", "https://graph.instagram.com/refresh_access_token", params={
                "grant_type": "ig_refresh_token", "access_token": token})
            return self._token(data)


def get_instagram_oauth():
    return InstagramOAuth()
