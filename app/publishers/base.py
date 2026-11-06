from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class PublishImage:
    content: bytes
    filename: str
    content_type: str = "image/png"


@dataclass(frozen=True)
class PublishRequest:
    content: str
    idempotency_key: str
    image: PublishImage | None = None
    owner_user_id: int | None = None
    provider_state: dict = field(default_factory=dict)
    save_state: Callable[[dict], None] | None = field(default=None, repr=False)
    destination_account_id: str | None = None


@dataclass(frozen=True)
class PublishResult:
    external_post_id: str
    external_url: str | None = None


class PublisherError(RuntimeError):
    """A safe, user-facing publisher failure that contains no credentials."""

    def __init__(self, message: str, *, retryable=True, outcome_uncertain=False):
        super().__init__(message)
        self.retryable = retryable
        self.outcome_uncertain = outcome_uncertain


class SocialPublisher(ABC):
    supports_images = False

    @abstractmethod
    async def publish(self, request: PublishRequest) -> PublishResult:
        """Publish one post or return the result of the same prior operation."""
