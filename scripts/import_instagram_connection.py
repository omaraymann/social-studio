"""One-time import of the existing local token into encrypted owner storage."""

import asyncio

from dotenv import set_key
from sqlalchemy.engine import make_url

from app.config import settings


async def main():
    url = make_url(settings.database_url)
    if url.host == "db":
        settings.database_url = url.set(host="127.0.0.1").render_as_string(hide_password=False)
    from app.database import SessionLocal
    from app.models import InstagramConnection, User, ScheduleSlot, PlatformVariant, SourcePost, utcnow
    from app.services.instagram_connections import cipher, encrypt_token, decrypt_token
    from app.services.instagram_oauth import SCOPES
    from sqlalchemy import select
    import httpx

    cipher()
    if not settings.instagram_owner_user_id or not settings.instagram_access_token:
        print("No legacy account token to import.")
        return
    with SessionLocal() as db:
        if db.get(InstagramConnection, settings.instagram_owner_user_id):
            print("Connection already exists; retained without changes.")
            return
        if not db.get(User, settings.instagram_owner_user_id):
            raise SystemExit("The configured workspace owner does not exist.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"https://graph.instagram.com/{settings.instagram_api_version}/me",
            params={"fields": "user_id,username"},
            headers={"Authorization": f"Bearer {settings.instagram_access_token}"})
        if response.status_code != 200:
            raise SystemExit("Could not verify the existing token; import stopped.")
        identity = response.json()
    if str(identity.get("user_id")) != settings.instagram_user_id:
        raise SystemExit("Token identity differs from the configured account; import stopped.")
    with SessionLocal() as db:
        row = InstagramConnection(owner_id=settings.instagram_owner_user_id,
            instagram_user_id=settings.instagram_user_id, username=identity["username"],
            encrypted_token=encrypt_token(settings.instagram_owner_user_id, settings.instagram_user_id,
                                          settings.instagram_access_token),
            status="active", scopes=list(SCOPES), expires_at=None, token_issued_at=utcnow())
        db.add(row)
        schedules = db.scalars(select(ScheduleSlot).join(PlatformVariant).join(SourcePost).where(
            SourcePost.owner_id == row.owner_id, PlatformVariant.platform == "instagram",
            ScheduleSlot.destination_account_id.is_(None))).all()
        for schedule in schedules:
            schedule.destination_account_id, schedule.destination_account_name = row.instagram_user_id, row.username
        db.commit()
        assert decrypt_token(row) == settings.instagram_access_token
    set_key(".env", "INSTAGRAM_ACCESS_TOKEN", "", quote_mode="never")
    print("Existing Instagram account imported and encrypted; legacy token removed from .env.")
    print("Expiry is unknown for the imported token. Reconnect through OAuth to enable renewal.")


if __name__ == "__main__":
    asyncio.run(main())
