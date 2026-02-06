from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db.models import CreditLedger, Generation, Order, User
from app.db.session import create_sessionmaker


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _is_logged_in(request: Request) -> bool:
    return bool(request.session.get("admin_logged_in"))


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Renderis Admin")
    app.add_middleware(SessionMiddleware, secret_key=settings.admin_web_secret)
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.sessionmaker = create_sessionmaker()

    @app.get("/", response_class=HTMLResponse)
    async def root(request: Request):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        return RedirectResponse(url="/admin", status_code=302)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if _is_logged_in(request):
            return RedirectResponse(url="/admin", status_code=302)
        return app.state.templates.TemplateResponse(
            "login.html",
            {"request": request, "error": None},
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login_action(
        request: Request,
        username: str = Form(""),
        password: str = Form(""),
    ):
        if not settings.admin_web_password:
            return app.state.templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Пароль не задан в ADMIN_WEB_PASSWORD."},
            )
        ok = secrets.compare_digest(username, settings.admin_web_username) and secrets.compare_digest(
            password, settings.admin_web_password
        )
        if not ok:
            return app.state.templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Неверный логин или пароль."},
            )
        request.session["admin_logged_in"] = True
        return RedirectResponse(url="/admin", status_code=302)

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=302)

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_dashboard(request: Request):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        async with app.state.sessionmaker() as session:
            users_count = await session.scalar(select(func.count(User.id)))
            generations_count = await session.scalar(select(func.count(Generation.id)))
            orders_count = await session.scalar(select(func.count(Order.id)))
            stars_revenue = await session.scalar(select(func.coalesce(func.sum(Order.stars_amount), 0)))
            credits_issued = await session.scalar(
                select(func.coalesce(func.sum(CreditLedger.delta_credits), 0)).where(CreditLedger.delta_credits > 0)
            )
            credits_spent_raw = await session.scalar(
                select(func.coalesce(func.sum(CreditLedger.delta_credits), 0)).where(
                    CreditLedger.reason == "generation_charge"
                )
            )

            recent_orders = (
                await session.execute(select(Order).order_by(Order.created_at.desc()).limit(10))
            ).scalars().all()
            recent_gens = (
                await session.execute(select(Generation).order_by(Generation.created_at.desc()).limit(10))
            ).scalars().all()

        credits_spent = abs(int(credits_spent_raw or 0))

        return app.state.templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "users_count": users_count or 0,
                "generations_count": generations_count or 0,
                "orders_count": orders_count or 0,
                "stars_revenue": stars_revenue or 0,
                "credits_issued": credits_issued or 0,
                "credits_spent": credits_spent,
                "recent_orders": recent_orders,
                "recent_gens": recent_gens,
            },
        )

    return app
