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


def _option_label(option_key: str) -> str:
    if option_key == "base":
        return "База"
    if option_key.startswith("output_format_"):
        fmt = option_key.split("_", 2)[-1].upper()
        return f"Формат {fmt}"
    if option_key.startswith("aspect_"):
        ratio = option_key.replace("aspect_", "").replace("_", ":").upper()
        return f"Соотношение {ratio}"
    if option_key.startswith("resolution_"):
        size = option_key.replace("resolution_", "").upper()
        return f"Разрешение {size}"
    if option_key == "ref_none":
        return "Референсы: без"
    if option_key == "ref_has":
        return "Референсы: с"
    return option_key


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


def _get_price_value(price: Price | None, attr: str) -> int:
    if not price:
        return 0
    value = getattr(price, attr)
    return int(value or 0)


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
            kie_usd_per_credit = await settings_service.get_float("kie_usd_per_credit", 0.02)

            prices = (
                await session.execute(
                    select(Price).where(
                        Price.model_key.in_(["nano_banana", "nano_banana_edit", "nano_banana_pro"])
                    )
                )
            ).scalars().all()

            price_map = {(p.model_key, p.option_key): p for p in prices}
            names = _model_name_map()
            products = []

            base_nb = price_map.get(("nano_banana", "base"))
            base_edit = price_map.get(("nano_banana_edit", "base"))
            base_pro = price_map.get(("nano_banana_pro", "base"))
            ref_has = price_map.get(("nano_banana_pro", "ref_has"))
            res_4k = price_map.get(("nano_banana_pro", "resolution_4k"))

            base_nb_renderis = _get_price_value(base_nb, "price_credits")
            base_nb_kie = _get_price_value(base_nb, "provider_credits")
            base_edit_renderis = _get_price_value(base_edit, "price_credits")
            base_edit_kie = _get_price_value(base_edit, "provider_credits")

            base_pro_renderis = _get_price_value(base_pro, "price_credits")
            base_pro_kie = _get_price_value(base_pro, "provider_credits")
            ref_renderis = _get_price_value(ref_has, "price_credits")
            ref_kie = _get_price_value(ref_has, "provider_credits")
            res4_renderis = _get_price_value(res_4k, "price_credits")
            res4_kie = _get_price_value(res_4k, "provider_credits")

            rows = [
                {
                    "row_id": "nano_banana",
                    "label": names.get("nano_banana", "nano_banana"),
                    "kie_credits": base_nb_kie,
                    "renderis_credits": base_nb_renderis,
                },
                {
                    "row_id": "nano_banana_edit",
                    "label": names.get("nano_banana_edit", "nano_banana_edit"),
                    "kie_credits": base_edit_kie,
                    "renderis_credits": base_edit_renderis,
                },
                {
                    "row_id": "pro_base",
                    "label": f"{names.get('nano_banana_pro', 'nano_banana_pro')} (без референсов)",
                    "kie_credits": base_pro_kie,
                    "renderis_credits": base_pro_renderis,
                },
                {
                    "row_id": "pro_refs_1k",
                    "label": f"{names.get('nano_banana_pro', 'nano_banana_pro')} (с референсами 1K)",
                    "kie_credits": base_pro_kie + ref_kie,
                    "renderis_credits": base_pro_renderis + ref_renderis,
                },
                {
                    "row_id": "pro_refs_2k",
                    "label": f"{names.get('nano_banana_pro', 'nano_banana_pro')} (с референсами 2K)",
                    "kie_credits": base_pro_kie + ref_kie + _get_price_value(
                        price_map.get(("nano_banana_pro", "resolution_2k")), "provider_credits"
                    ),
                    "renderis_credits": base_pro_renderis
                    + ref_renderis
                    + _get_price_value(price_map.get(("nano_banana_pro", "resolution_2k")), "price_credits"),
                },
                {
                    "row_id": "pro_refs_4k",
                    "label": f"{names.get('nano_banana_pro', 'nano_banana_pro')} (с референсами 4K)",
                    "kie_credits": base_pro_kie + ref_kie + res4_kie,
                    "renderis_credits": base_pro_renderis + ref_renderis + res4_renderis,
                },
            ]

            for row in rows:
                kie_usd = round(row["kie_credits"] * kie_usd_per_credit, 4)
                renderis_usd = round(row["renderis_credits"] * usd_per_credit, 4)
                profit_pct = ""
                if kie_usd > 0:
                    profit_pct = round((renderis_usd / kie_usd) * 100, 1)
                products.append(
                    {
                        **row,
                        "kie_usd": kie_usd,
                        "renderis_usd": renderis_usd,
                        "profit_pct": profit_pct,
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

    @app.post("/admin/products/{row_id}")
    async def admin_products_update(
        request: Request,
        row_id: str,
        provider_credits: str = Form(""),
        renderis_credits: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        async with app.state.sessionmaker() as session:
            prices = (
                await session.execute(
                    select(Price).where(
                        Price.model_key.in_(["nano_banana", "nano_banana_edit", "nano_banana_pro"])
                    )
                )
            ).scalars().all()
            price_map = {(p.model_key, p.option_key): p for p in prices}

            def update_price(model_key: str, option_key: str, renderis_val: int | None, kie_val: int | None) -> None:
                row = price_map.get((model_key, option_key))
                if not row:
                    return
                if renderis_val is not None:
                    row.price_credits = renderis_val
                if kie_val is not None:
                    row.provider_credits = kie_val

            def get_val(model_key: str, option_key: str, attr: str) -> int:
                return _get_price_value(price_map.get((model_key, option_key)), attr)

            parsed_kie = _parse_int(provider_credits.strip()) if provider_credits.strip() else None
            parsed_renderis = _parse_int(renderis_credits.strip()) if renderis_credits.strip() else None

            base_pro_renderis = get_val("nano_banana_pro", "base", "price_credits")
            base_pro_kie = get_val("nano_banana_pro", "base", "provider_credits")
            ref_renderis = get_val("nano_banana_pro", "ref_has", "price_credits")
            ref_kie = get_val("nano_banana_pro", "ref_has", "provider_credits")

            if row_id == "nano_banana":
                update_price("nano_banana", "base", parsed_renderis, parsed_kie)
            elif row_id == "nano_banana_edit":
                update_price("nano_banana_edit", "base", parsed_renderis, parsed_kie)
            elif row_id == "pro_base":
                update_price("nano_banana_pro", "base", parsed_renderis, parsed_kie)
                update_price("nano_banana_pro", "resolution_1k", 0, 0)
                update_price("nano_banana_pro", "resolution_2k", 0, 0)
            elif row_id == "pro_refs_1k":
                target_renderis = parsed_renderis if parsed_renderis is not None else base_pro_renderis + ref_renderis
                target_kie = parsed_kie if parsed_kie is not None else base_pro_kie + ref_kie
                ref_renderis_new = max(0, target_renderis - base_pro_renderis)
                ref_kie_new = max(0, target_kie - base_pro_kie)
                update_price("nano_banana_pro", "ref_has", ref_renderis_new, ref_kie_new)
                update_price("nano_banana_pro", "resolution_1k", 0, 0)
                update_price("nano_banana_pro", "resolution_2k", 0, 0)
            elif row_id == "pro_refs_2k":
                target_renderis = parsed_renderis if parsed_renderis is not None else base_pro_renderis + ref_renderis + get_val("nano_banana_pro", "resolution_2k", "price_credits")
                target_kie = parsed_kie if parsed_kie is not None else base_pro_kie + ref_kie + get_val("nano_banana_pro", "resolution_2k", "provider_credits")
                res_renderis_new = max(0, target_renderis - base_pro_renderis - ref_renderis)
                res_kie_new = max(0, target_kie - base_pro_kie - ref_kie)
                update_price("nano_banana_pro", "resolution_2k", res_renderis_new, res_kie_new)
                update_price("nano_banana_pro", "resolution_1k", 0, 0)
            elif row_id == "pro_refs_4k":
                target_renderis = parsed_renderis if parsed_renderis is not None else base_pro_renderis + ref_renderis + get_val("nano_banana_pro", "resolution_4k", "price_credits")
                target_kie = parsed_kie if parsed_kie is not None else base_pro_kie + ref_kie + get_val("nano_banana_pro", "resolution_4k", "provider_credits")
                res_renderis_new = max(0, target_renderis - base_pro_renderis - ref_renderis)
                res_kie_new = max(0, target_kie - base_pro_kie - ref_kie)
                update_price("nano_banana_pro", "resolution_4k", res_renderis_new, res_kie_new)
                update_price("nano_banana_pro", "resolution_1k", 0, 0)
                update_price("nano_banana_pro", "resolution_2k", 0, 0)

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
            kie_usd_per_credit = await settings_service.get_float("kie_usd_per_credit", 0.02)

        return app.state.templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "title": "Renderis Admin — Настройки",
                "active_tab": "settings",
                "stars_per_credit": stars_per_credit,
                "usd_per_star": usd_per_star,
                "usd_per_credit": usd_per_credit,
                "kie_usd_per_credit": kie_usd_per_credit,
                "saved": bool(saved),
            },
        )

    @app.post("/admin/settings")
    async def admin_settings_update(
        request: Request,
        stars_per_credit: str = Form(""),
        usd_per_star: str = Form(""),
        kie_usd_per_credit: str = Form(""),
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
            if kie_usd_per_credit.strip():
                parsed = _parse_float(kie_usd_per_credit.strip())
                if parsed is not None:
                    await settings_service.set("kie_usd_per_credit", str(parsed))
            await session.commit()

        return RedirectResponse(url="/admin/settings?saved=1", status_code=302)

    return app

