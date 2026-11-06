"""Image-only app for local tunnels; contains no dashboard or account routes."""

from fastapi import APIRouter, FastAPI, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode

from app.services.instagram_media import read_signed_image

router = APIRouter(tags=["publishing media"])


@router.get("/media/instagram/{image_id}.jpg")
def fetch_image(image_id: str, expires: int = Query(...), signature: str = Query(...)):
    try:
        raw = read_signed_image(image_id, expires, signature)
    except ValueError as exc:
        raise HTTPException(404, "Image link is invalid, expired, or unavailable") from exc
    return Response(raw, media_type="image/jpeg", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer"})


@router.get("/instagram/oauth/callback")
def instagram_callback(code: str | None = Query(default=None, max_length=4096),
                       state: str | None = Query(default=None, max_length=200),
                       error: str | None = Query(default=None, max_length=200)):
    # Relay only; linking still requires the dashboard JWT and browser cookie.
    from app.services.instagram_oauth import frontend_origin, InstagramOAuthError
    try:
        destination = frontend_origin()
    except InstagramOAuthError as exc:
        raise HTTPException(503, "Dashboard return address is not configured") from exc
    fragment = urlencode({"instagram_oauth": "1", "state": state or "",
                          "code": code or "", "denied": "1" if error else "0"})
    return RedirectResponse(destination + "/#" + fragment, status_code=303,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)
