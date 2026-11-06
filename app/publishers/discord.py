import httpx
import json

from app.publishers.base import PublisherError, PublishRequest, PublishResult, SocialPublisher


class DiscordPublisher(SocialPublisher):
    mode = "live"
    supports_images = True

    def __init__(self, webhook_url: str | None, timeout: float = 20, transport=None):
        self.webhook_url = webhook_url
        self.timeout = timeout
        self.transport = transport

    @property
    def configured(self):
        return bool(self.webhook_url)

    async def publish(self, request: PublishRequest) -> PublishResult:
        if not self.webhook_url:
            raise PublisherError("Discord publisher is not configured", retryable=False)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                payload = {"content": request.content, "allowed_mentions": {"parse": []}}
                if request.image:
                    payload["attachments"] = [{"id": 0, "filename": request.image.filename}]
                    response = await client.post(self.webhook_url, params={"wait": "true"},
                        data={"payload_json": json.dumps(payload)}, files={"files[0]": (
                            request.image.filename, request.image.content, request.image.content_type)})
                else:
                    response = await client.post(self.webhook_url, params={"wait": "true"}, json=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            raise PublisherError(f"Discord returned HTTP {code}", retryable=code == 429,
                                 outcome_uncertain=code >= 500) from exc
        except httpx.RequestError as exc:
            uncertain = not isinstance(exc, httpx.ConnectError)
            raise PublisherError("Discord request failed", retryable=not uncertain,
                                 outcome_uncertain=uncertain) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise PublisherError("Discord returned an invalid confirmation", retryable=False, outcome_uncertain=True) from exc
        if not isinstance(payload, dict) or "id" not in payload:
            raise PublisherError("Discord response did not contain a message ID", retryable=False, outcome_uncertain=True)
        if request.image and not payload.get("attachments"):
            raise PublisherError("Discord did not confirm the image attachment", retryable=False, outcome_uncertain=True)
        return PublishResult(external_post_id=str(payload["id"]))
