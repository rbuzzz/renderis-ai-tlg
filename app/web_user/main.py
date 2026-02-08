from __future__ import annotations

import uvicorn

from app.config import get_settings
from app.web_user.app import create_app


def main() -> None:
    settings = get_settings()
    if not settings.user_web_enabled:
        print("USER_WEB_ENABLED=false. Включите, чтобы запустить пользовательский сайт.")
        return
    app = create_app()
    uvicorn.run(
        app,
        host=settings.user_web_host,
        port=settings.user_web_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
