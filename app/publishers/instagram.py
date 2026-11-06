"""Single-image publishing through Instagram Login, with durable checkpoints."""

import asyncio
import hashlib
import re
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.publishers.base import PublisherError, PublishRequest, PublishResult, SocialPublisher
from app.services.instagram_media import create_image_link_from_bytes, validate_instagram_image
from app.services.variants import validate_variant
from app.services.instagram_connections import load_credentials, available_connection, assert_credentials_current, mark_reconnect
from app.services.instagram_oauth import InstagramOAuthError


class InstagramPublisher(SocialPublisher):
    mode = "live"
    supports_images = True

    def __init__(self, *, transport=None, media_transport=None, poll_seconds=5, poll_attempts=12, credentials=None):
        self.credentials = credentials
        self.transport = transport
        self.media_transport = media_transport
        self.poll_seconds = poll_seconds
        self.poll_attempts = poll_attempts

    @property
    def configured(self):
        return bool(self.credentials and settings.public_media_base_url)

    def available_for(self, owner_user_id):
        if self.credentials:
            return self.configured and self.credentials.owner_id == owner_user_id
        return bool(settings.public_media_base_url and available_connection(owner_user_id))

    def account_name(self, owner_user_id):
        info = available_connection(owner_user_id)
        return info["username"] if info else None

    def account_id(self, owner_user_id):
        info = available_connection(owner_user_id)
        return info["user_id"] if info else None

    async def bound(self, owner_user_id):
        if self.credentials:
            if self.credentials.owner_id != owner_user_id:
                raise PublisherError("Instagram is not configured for this workspace", retryable=False)
            return self
        try:
            credentials = await load_credentials(owner_user_id)
        except InstagramOAuthError as exc:
            raise PublisherError(str(exc), retryable=False) from exc
        return InstagramPublisher(transport=self.transport, media_transport=self.media_transport,
            poll_seconds=self.poll_seconds, poll_attempts=self.poll_attempts, credentials=credentials)

    def assert_current(self):
        try:
            assert_credentials_current(self.credentials)
        except InstagramOAuthError as exc:
            raise PublisherError(str(exc), retryable=False) from exc

    @property
    def base_url(self):
        if not re.fullmatch(r"v\d+\.0", settings.instagram_api_version):
            raise PublisherError("Invalid Instagram API version configuration", retryable=False)
        return f"https://graph.instagram.com/{settings.instagram_api_version}"

    async def _request(self, client, method, path, *, publish=False, **kwargs):
        try:
            response = await client.request(method, self.base_url + path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise PublisherError("Could not connect to Instagram", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise PublisherError("Instagram request timed out or was interrupted",
                                 retryable=not publish, outcome_uncertain=publish) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise PublisherError("Instagram returned an invalid response", retryable=not publish,
                                 outcome_uncertain=publish) from exc
        if not isinstance(data, dict):
            raise PublisherError("Instagram returned an invalid response", retryable=False,
                                 outcome_uncertain=publish)
        if not response.is_success or "error" in data:
            code = data.get("error", {}).get("code")
            if code == 190:
                message = "Instagram token expired or was revoked; reconnect the account"
                if self.credentials:
                    mark_reconnect(self.credentials.owner_id, self.credentials.account_id, self.credentials.revision)
            elif code in (10, 200):
                message = "Instagram denied access; check the account role and content publishing permission"
            else:
                message = f"Instagram rejected the request (HTTP {response.status_code}, code {code})"
            uncertain = publish and response.status_code >= 500
            raise PublisherError(message, retryable=response.status_code == 429 or response.status_code >= 500,
                                 outcome_uncertain=uncertain)
        return data

    async def connection(self, owner_user_id):
        if self.credentials is None:
            return await (await self.bound(owner_user_id)).connection(owner_user_id)
        if not self.available_for(owner_user_id):
            raise PublisherError("Instagram is not configured for this workspace", retryable=False)
        async with self._client() as client:
            return await self._identity(client)

    def _client(self):
        return httpx.AsyncClient(headers={"Authorization": f"Bearer {self.credentials.access_token}"},
                                 timeout=15, transport=self.transport, follow_redirects=False)

    async def _identity(self, client):
        data = await self._request(client, "GET", "/me", params={"fields": "user_id,username"})
        if str(data.get("user_id")) != self.credentials.account_id:
            raise PublisherError("Instagram token belongs to a different account; publishing stopped", retryable=False)
        return {"user_id": str(data["user_id"]), "username": data.get("username")}

    async def create_container(self, client, image_url, caption):
        data = await self._request(client, "POST", f"/{self.credentials.account_id}/media",
                                   data={"image_url": image_url, "caption": caption})
        if not str(data.get("id", "")).isdigit():
            raise PublisherError("Instagram did not return a container ID", retryable=True)
        return str(data["id"])

    async def container_status(self, client, container_id):
        data = await self._request(client, "GET", f"/{container_id}", params={"fields": "status_code"})
        return data.get("status_code")

    async def publish(self, request: PublishRequest) -> PublishResult:
        if self.credentials is None:
            return await (await self.bound(request.owner_user_id)).publish(request)
        if request.destination_account_id and request.destination_account_id != self.credentials.account_id:
            raise PublisherError("Scheduled Instagram destination differs from the connected account. Reconnect the original account.", retryable=False)
        if not self.available_for(request.owner_user_id):
            raise PublisherError("Instagram is not configured for this workspace", retryable=False)
        if not request.image:
            raise PublisherError("Instagram requires an image", retryable=False)
        if request.save_state is None:
            raise PublisherError("Instagram requires durable delivery checkpoints", retryable=False)
        try:
            validate_variant("instagram", request.content)
            validate_instagram_image(request.image.content)
        except ValueError as exc:
            raise PublisherError(str(exc), retryable=False) from exc
        state = dict(request.provider_state)
        fingerprint = hashlib.sha256(request.image.content + b"\0" + request.content.encode()).hexdigest()
        if state and (state.get("account_id") != self.credentials.account_id or state.get("fingerprint") != fingerprint):
            raise PublisherError("Instagram delivery snapshot or destination changed", retryable=False)
        if state.get("stage") == "publishing" and not state.get("media_id"):
            raise PublisherError("Previous publish outcome needs manual reconciliation", retryable=False, outcome_uncertain=True)
        state.update(account_id=self.credentials.account_id, fingerprint=fingerprint)

        def checkpoint(**values):
            state.update(values)
            request.save_state(dict(state))

        try:
            async with asyncio.timeout(180), self._client() as client:
                await self._identity(client)
                if not state.get("media_id"):
                    if not state.get("container_id"):
                        try:
                            link = create_image_link_from_bytes(request.image.content)
                        except ValueError as exc:
                            raise PublisherError(str(exc), retryable=False) from exc
                        # Separate client: never send the Instagram bearer token to image hosting.
                        try:
                            async with httpx.AsyncClient(timeout=15, transport=self.media_transport,
                                                         follow_redirects=False) as media_client:
                                image = await media_client.get(link["image_url"])
                            expected = urlsplit(link["image_url"]).path.rsplit("/", 1)[-1].removesuffix(".jpg")
                            if image.status_code != 200 or hashlib.sha256(image.content).hexdigest() != expected:
                                raise PublisherError("Public campaign image is unavailable; check the image gateway and tunnel", retryable=True)
                        except httpx.HTTPError as exc:
                            raise PublisherError("Cannot reach public campaign image; check the tunnel", retryable=True) from exc
                        self.assert_current()
                        checkpoint(stage="creating_container")
                        container_id = await self.create_container(client, link["image_url"], request.content)
                        checkpoint(stage="processing_image", container_id=container_id)
                    for _ in range(self.poll_attempts):
                        status = await self.container_status(client, state["container_id"])
                        if status == "FINISHED":
                            break
                        if status == "PUBLISHED":
                            raise PublisherError("Instagram container was already published; reconcile its post ID", retryable=False,
                                                 outcome_uncertain=True)
                        if status in {"ERROR", "EXPIRED"}:
                            # Known unpublished container: next explicit retry gets a fresh image link.
                            checkpoint(stage="container_failed", container_id=None)
                            raise PublisherError(f"Instagram image processing {status.lower()}; retry after checking the image", retryable=False)
                        if status != "IN_PROGRESS":
                            raise PublisherError("Instagram returned an unknown image processing status", retryable=True)
                        await asyncio.sleep(self.poll_seconds)
                    else:
                        raise PublisherError("Instagram is still processing the image; retry will reuse its container", retryable=True)
                    self.assert_current()
                    checkpoint(stage="publishing")
                    try:
                        published = await self._request(client, "POST", f"/{self.credentials.account_id}/media_publish",
                                                        data={"creation_id": state["container_id"]}, publish=True)
                    except PublisherError as exc:
                        if not exc.outcome_uncertain:
                            checkpoint(stage="processing_image")
                        raise
                    media_id = str(published.get("id", ""))
                    if not media_id.isdigit():
                        raise PublisherError("Instagram did not confirm the published post ID", retryable=False, outcome_uncertain=True)
                    checkpoint(stage="published", media_id=media_id)
                # A missing permalink cannot turn a confirmed published post into a retry.
                permalink = state.get("permalink")
                if not permalink:
                    try:
                        data = await self._request(client, "GET", f"/{state['media_id']}", params={"fields": "permalink"})
                        candidate = data.get("permalink", "")
                        parsed = urlsplit(candidate)
                        if parsed.scheme == "https" and parsed.hostname in {"www.instagram.com", "instagram.com"}:
                            permalink = candidate
                            checkpoint(permalink=permalink)
                    except PublisherError:
                        pass
                return PublishResult(external_post_id=state["media_id"], external_url=permalink)
        except TimeoutError as exc:
            if state.get("media_id"):
                return PublishResult(external_post_id=state["media_id"], external_url=state.get("permalink"))
            uncertain = state.get("stage") == "publishing"
            raise PublisherError("Instagram publishing operation timed out", retryable=not uncertain,
                                 outcome_uncertain=uncertain) from exc
