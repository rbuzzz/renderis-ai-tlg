from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db.models import CreditLedger, Generation, Order, Price, User
from app.db.session import create_sessionmaker
from app.modelspecs.registry import list_models
from app.services.app_settings import AppSettingsService


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _is_logged_in(request: Request) -> bool:
    return bool(request.session.get("admin_logged_in"))


def _model_name_map() -> dict[str, str]:
    return {m.key: m.display_name for m in list_models()}


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
                "title": "Renderis Admin — Дашборд",
                "active_tab": "dashboard",
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

    @app.get("/admin/products", response_class=HTMLResponse)
    async def admin_products(request: Request, saved: int | None = None):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        async with app.state.sessionmaker() as session:
            settings_service = AppSettingsService(session)
            stars_per_credit = await settings_service.get_float("stars_per_credit", 2.0)
            usd_per_star = await settings_service.get_float("usd_per_star", 0.013)
            usd_per_credit = stars_per_credit * usd_per_star

            prices = (
                await session.execute(
                    select(Price).where(Price.option_key == "base").order_by(Price.model_key)
                )
            ).scalars().all()

            names = _model_name_map()
            products = []
            for price in prices:
                renderis_credits = int(price.price_credits or 0)
                renderis_usd = round(renderis_credits * usd_per_credit, 4)
                provider_credits = "" if price.provider_credits is None else price.provider_credits
                provider_cost = "" if price.provider_cost_usd is None else float(price.provider_cost_usd)
                products.append(
                    {
                        "id": price.id,
                        "model_key": price.model_key,
                        "model_name": names.get(price.model_key, price.model_key),
                        "provider_credits": provider_credits,
                        "provider_cost_usd": provider_cost,
                        "renderis_credits": renderis_credits,
                        "renderis_usd": renderis_usd,
                    }
                )

        return app.state.templates.TemplateResponse(
            "products.html",
            {
                "request": request,
                "title": "Renderis Admin — Товары",
                "active_tab": "products",
                "products": products,
                "saved": bool(saved),
            },
        )

    @app.post("/admin/products/{price_id}")
    async def admin_products_update(
        request: Request,
        price_id: int,
        provider_credits: str = Form(""),
        provider_cost_usd: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        async with app.state.sessionmaker() as session:
            price = await session.get(Price, price_id)
            if not price:
                return RedirectResponse(url="/admin/products", status_code=302)

            parsed_credits = _parse_int(provider_credits.strip()) if provider_credits.strip() else None
            parsed_cost = _parse_float(provider_cost_usd.strip()) if provider_cost_usd.strip() else None
            price.provider_credits = parsed_credits
            price.provider_cost_usd = parsed_cost
            await session.commit()

        return RedirectResponse(url="/admin/products?saved=1", status_code=302)

    @app.get("/admin/settings", response_class=HTMLResponse)
    async def admin_settings(request: Request, saved: int | None = None):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        async with app.state.sessionmaker() as session:
            settings_service = AppSettingsService(session)
            stars_per_credit = await settings_service.get_float("stars_per_credit", 2.0)
            usd_per_star = await settings_service.get_float("usd_per_star", 0.013)
            usd_per_credit = round(stars_per_credit * usd_per_star, 6)

        return app.state.templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "title": "Renderis Admin — Настройки",
                "active_tab": "settings",
                "stars_per_credit": stars_per_credit,
                "usd_per_star": usd_per_star,
                "usd_per_credit": usd_per_credit,
                "saved": bool(saved),
            },
        )

    @app.post("/admin/settings")
    async def admin_settings_update(
        request: Request,
        stars_per_credit: str = Form(""),
        usd_per_star: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        async with app.state.sessionmaker() as session:
            settings_service = AppSettingsService(session)
            if stars_per_credit.strip():
                parsed = _parse_float(stars_per_credit.strip())
                if parsed is not None:
                    await settings_service.set("stars_per_credit", str(parsed))
            if usd_per_star.strip():
                parsed = _parse_float(usd_per_star.strip())
                if parsed is not None:
                    await settings_service.set("usd_per_star", str(parsed))
            await session.commit()

        return RedirectResponse(url="/admin/settings?saved=1", status_code=302)

    return app

