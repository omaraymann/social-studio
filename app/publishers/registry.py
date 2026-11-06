from app.config import settings
from app.publishers.base import SocialPublisher
from app.publishers.discord import DiscordPublisher
from app.publishers.mock import MockLinkedInPublisher, MockXPublisher
from app.publishers.instagram import InstagramPublisher


class PublisherRegistry:
    def __init__(self, publishers: dict[str, SocialPublisher]):
        self._publishers = publishers

    def get(self, platform: str) -> SocialPublisher:
        publisher = self._publishers.get(platform)
        if not publisher:
            raise LookupError(f"No publisher configured for {platform}")
        return publisher

    def capabilities(self, owner_user_id=None):
        return [{"platform": platform, "mode": getattr(publisher, "mode", "custom"),
                 "configured": publisher.available_for(owner_user_id) if hasattr(publisher, "available_for") else getattr(publisher, "configured", True),
                 "account": publisher.account_name(owner_user_id) if platform == "instagram" and publisher.available_for(owner_user_id) else None,
                 "account_id": publisher.account_id(owner_user_id) if platform == "instagram" and publisher.available_for(owner_user_id) else None,
                 "supports_images": getattr(publisher, "supports_images", False)}
                for platform, publisher in self._publishers.items()]


publisher_registry = PublisherRegistry(
    {
        "x": MockXPublisher(),
        "linkedin": MockLinkedInPublisher(),
        "discord": DiscordPublisher(settings.discord_webhook_url),
        "instagram": InstagramPublisher(),
    }
)


def get_publisher_registry() -> PublisherRegistry:
    return publisher_registry
