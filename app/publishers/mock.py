from hashlib import sha256

from app.publishers.base import PublishRequest, PublishResult, SocialPublisher


class MockPublisher(SocialPublisher):
    platform: str
    mode = "mock"
    supports_images = True

    async def publish(self, request: PublishRequest) -> PublishResult:
        stable_id = sha256(f"{self.platform}:{request.idempotency_key}".encode()).hexdigest()[:16]
        return PublishResult(external_post_id=f"mock-{self.platform}-{stable_id}")


class MockXPublisher(MockPublisher):
    platform = "x"


class MockLinkedInPublisher(MockPublisher):
    platform = "linkedin"
