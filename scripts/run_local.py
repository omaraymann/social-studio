"""Run local services against the Docker database without changing .env."""

import argparse
import asyncio

from sqlalchemy.engine import make_url

from app.config import settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("api", "worker", "migrate", "media"))
    parser.add_argument("--platform", choices=("instagram", "discord", "x", "linkedin"), help="Limit publishing worker to one platform")
    args = parser.parse_args()
    service = args.service
    url = make_url(settings.database_url)
    if url.host == "db":
        settings.database_url = url.set(host="127.0.0.1").render_as_string(hide_password=False)
    if service == "media":
        import uvicorn
        uvicorn.run("app.media_gateway:app", host="127.0.0.1", port=8001, access_log=False)
    elif service == "api":
        import uvicorn
        uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
    elif service == "worker":
        from app.worker import main as run_worker
        asyncio.run(run_worker(platform=args.platform))
    else:
        from alembic import command
        from alembic.config import Config
        command.upgrade(Config("alembic.ini"), "head")


if __name__ == "__main__":
    main()
