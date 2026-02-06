from __future__ import annotations

import sys

import uvicorn

from app.config import get_settings
from app.web.app import create_app


def main() -> None:
    settings = get_settings()
    if not settings.admin_web_enabled:
        print("ADMIN_WEB_ENABLED=false. Включите, чтобы запустить панель.")
        return
    app = create_app()
    uvicorn.run(
        app,
        host=settings.admin_web_host,
        port=settings.admin_web_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
