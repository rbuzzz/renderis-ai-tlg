from __future__ import annotations

import secrets
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db.models import CreditLedger, Generation, Order, Price, PromoCode, User
from app.db.session import create_sessionmaker
from app.modelspecs.registry import list_models
from app.services.app_settings import AppSettingsService
from app.services.kie_balance import KieBalanceService
from app.services.promos import PromoService


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
            res_2k = price_map.get(("nano_banana_pro", "resolution_2k"))
            res_4k = price_map.get(("nano_banana_pro", "resolution_4k"))
            bundle_no_refs_1k = price_map.get(("nano_banana_pro", "bundle_no_refs_1k"))
            bundle_no_refs_2k = price_map.get(("nano_banana_pro", "bundle_no_refs_2k"))
            bundle_no_refs_4k = price_map.get(("nano_banana_pro", "bundle_no_refs_4k"))
            bundle_refs_1k = price_map.get(("nano_banana_pro", "bundle_refs_1k"))
            bundle_refs_2k = price_map.get(("nano_banana_pro", "bundle_refs_2k"))
            bundle_refs_4k = price_map.get(("nano_banana_pro", "bundle_refs_4k"))

            base_nb_renderis = _get_price_value(base_nb, "price_credits")
            base_nb_kie = _get_price_value(base_nb, "provider_credits")
            base_edit_renderis = _get_price_value(base_edit, "price_credits")
            base_edit_kie = _get_price_value(base_edit, "provider_credits")

            base_pro_renderis = _get_price_value(base_pro, "price_credits")
            base_pro_kie = _get_price_value(base_pro, "provider_credits")
            ref_renderis = _get_price_value(ref_has, "price_credits")
            ref_kie = _get_price_value(ref_has, "provider_credits")
            res2_renderis = _get_price_value(res_2k, "price_credits")
            res2_kie = _get_price_value(res_2k, "provider_credits")
            res4_renderis = _get_price_value(res_4k, "price_credits")
            res4_kie = _get_price_value(res_4k, "provider_credits")

            def _bundle_values(bundle: Price | None, fallback_renderis: int, fallback_kie: int) -> tuple[int, int]:
                if bundle:
                    return _get_price_value(bundle, "price_credits"), _get_price_value(bundle, "provider_credits")
                return fallback_renderis, fallback_kie

            pro_no_ref_1k_renderis, pro_no_ref_1k_kie = _bundle_values(
                bundle_no_refs_1k, base_pro_renderis, base_pro_kie
            )
            pro_no_ref_2k_renderis, pro_no_ref_2k_kie = _bundle_values(
                bundle_no_refs_2k, base_pro_renderis + res2_renderis, base_pro_kie + res2_kie
            )
            pro_no_ref_4k_renderis, pro_no_ref_4k_kie = _bundle_values(
                bundle_no_refs_4k, base_pro_renderis + res4_renderis, base_pro_kie + res4_kie
            )
            pro_ref_1k_renderis, pro_ref_1k_kie = _bundle_values(
                bundle_refs_1k, base_pro_renderis + ref_renderis, base_pro_kie + ref_kie
            )
            pro_ref_2k_renderis, pro_ref_2k_kie = _bundle_values(
                bundle_refs_2k, base_pro_renderis + ref_renderis + res2_renderis, base_pro_kie + ref_kie + res2_kie
            )
            pro_ref_4k_renderis, pro_ref_4k_kie = _bundle_values(
                bundle_refs_4k, base_pro_renderis + ref_renderis + res4_renderis, base_pro_kie + ref_kie + res4_kie
            )

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
                    "row_id": "pro_no_refs_1k",
                    "label": f"{names.get('nano_banana_pro', 'nano_banana_pro')} (без референсов 1K)",
                    "kie_credits": pro_no_ref_1k_kie,
                    "renderis_credits": pro_no_ref_1k_renderis,
                },
                {
                    "row_id": "pro_no_refs_2k",
                    "label": f"{names.get('nano_banana_pro', 'nano_banana_pro')} (без референсов 2K)",
                    "kie_credits": pro_no_ref_2k_kie,
                    "renderis_credits": pro_no_ref_2k_renderis,
                },
                {
                    "row_id": "pro_no_refs_4k",
                    "label": f"{names.get('nano_banana_pro', 'nano_banana_pro')} (без референсов 4K)",
                    "kie_credits": pro_no_ref_4k_kie,
                    "renderis_credits": pro_no_ref_4k_renderis,
                },
                {
                    "row_id": "pro_refs_1k",
                    "label": f"{names.get('nano_banana_pro', 'nano_banana_pro')} (с референсами 1K)",
                    "kie_credits": pro_ref_1k_kie,
                    "renderis_credits": pro_ref_1k_renderis,
                },
                {
                    "row_id": "pro_refs_2k",
                    "label": f"{names.get('nano_banana_pro', 'nano_banana_pro')} (с референсами 2K)",
                    "kie_credits": pro_ref_2k_kie,
                    "renderis_credits": pro_ref_2k_renderis,
                },
                {
                    "row_id": "pro_refs_4k",
                    "label": f"{names.get('nano_banana_pro', 'nano_banana_pro')} (с референсами 4K)",
                    "kie_credits": pro_ref_4k_kie,
                    "renderis_credits": pro_ref_4k_renderis,
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
                    row = Price(
                        model_key=model_key,
                        option_key=option_key,
                        price_credits=0,
                        provider_credits=None,
                        active=True,
                        model_type="image",
                        provider="kie",
                    )
                    session.add(row)
                    price_map[(model_key, option_key)] = row
                if renderis_val is not None:
                    row.price_credits = renderis_val
                if kie_val is not None:
                    row.provider_credits = kie_val

            def get_val(model_key: str, option_key: str, attr: str) -> int:
                return _get_price_value(price_map.get((model_key, option_key)), attr)

            parsed_kie = _parse_int(provider_credits.strip()) if provider_credits.strip() else None
            parsed_renderis = _parse_int(renderis_credits.strip()) if renderis_credits.strip() else None

            if row_id == "nano_banana":
                update_price("nano_banana", "base", parsed_renderis, parsed_kie)
            elif row_id == "nano_banana_edit":
                update_price("nano_banana_edit", "base", parsed_renderis, parsed_kie)
            elif row_id == "pro_no_refs_1k":
                update_price("nano_banana_pro", "bundle_no_refs_1k", parsed_renderis, parsed_kie)
            elif row_id == "pro_no_refs_2k":
                update_price("nano_banana_pro", "bundle_no_refs_2k", parsed_renderis, parsed_kie)
            elif row_id == "pro_no_refs_4k":
                update_price("nano_banana_pro", "bundle_no_refs_4k", parsed_renderis, parsed_kie)
            elif row_id == "pro_refs_1k":
                update_price("nano_banana_pro", "bundle_refs_1k", parsed_renderis, parsed_kie)
            elif row_id == "pro_refs_2k":
                update_price("nano_banana_pro", "bundle_refs_2k", parsed_renderis, parsed_kie)
            elif row_id == "pro_refs_4k":
                update_price("nano_banana_pro", "bundle_refs_4k", parsed_renderis, parsed_kie)

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
            kie_balance_service = KieBalanceService(session)
            kie_balance_credits = await kie_balance_service.get_balance()
            kie_balance_usd = round(kie_balance_credits * kie_usd_per_credit, 4)
            kie_warn_green = await settings_service.get("kie_warn_green", "1000")
            kie_warn_yellow = await settings_service.get("kie_warn_yellow", "500")
            kie_warn_red = await settings_service.get("kie_warn_red", "200")

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
                "kie_balance_credits": kie_balance_credits,
                "kie_balance_usd": kie_balance_usd,
                "kie_warn_green": kie_warn_green,
                "kie_warn_yellow": kie_warn_yellow,
                "kie_warn_red": kie_warn_red,
                "saved": bool(saved),
            },
        )

    @app.post("/admin/settings")
    async def admin_settings_update(
        request: Request,
        stars_per_credit: str = Form(""),
        usd_per_star: str = Form(""),
        kie_usd_per_credit: str = Form(""),
        set_kie_balance: str = Form(""),
        add_kie_credits: str = Form(""),
        kie_warn_green: str = Form(""),
        kie_warn_yellow: str = Form(""),
        kie_warn_red: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        async with app.state.sessionmaker() as session:
            settings_service = AppSettingsService(session)
            kie_balance_service = KieBalanceService(session)
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
            if set_kie_balance.strip():
                parsed = _parse_int(set_kie_balance.strip())
                if parsed is not None:
                    await kie_balance_service.set_balance(parsed)
            if add_kie_credits.strip() and not set_kie_balance.strip():
                parsed = _parse_int(add_kie_credits.strip())
                if parsed is not None and parsed > 0:
                    await kie_balance_service.add_credits(parsed)
            if kie_warn_green.strip():
                parsed = _parse_int(kie_warn_green.strip())
                if parsed is not None:
                    await settings_service.set("kie_warn_green", str(parsed))
            if kie_warn_yellow.strip():
                parsed = _parse_int(kie_warn_yellow.strip())
                if parsed is not None:
                    await settings_service.set("kie_warn_yellow", str(parsed))
            if kie_warn_red.strip():
                parsed = _parse_int(kie_warn_red.strip())
                if parsed is not None:
                    await settings_service.set("kie_warn_red", str(parsed))
            await session.commit()

        return RedirectResponse(url="/admin/settings?saved=1", status_code=302)

    @app.get("/admin/promos", response_class=HTMLResponse)
    async def admin_promos(request: Request, code: str | None = None, saved: int | None = None):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        search_code = (code or "").strip().upper()
        async with app.state.sessionmaker() as session:
            batch_rows = await session.execute(
                select(
                    PromoCode.batch_id,
                    func.min(PromoCode.created_at).label("created_at"),
                    func.count(PromoCode.code).label("count"),
                    func.max(PromoCode.credits_amount).label("credits_amount"),
                )
                .where(PromoCode.batch_id.is_not(None))
                .group_by(PromoCode.batch_id)
                .order_by(func.min(PromoCode.created_at).desc())
            )
            batches = [
                {
                    "batch_id": row.batch_id,
                    "created_at": row.created_at,
                    "count": row.count,
                    "credits_amount": row.credits_amount,
                }
                for row in batch_rows.all()
            ]

            promo = None
            if search_code:
                result = await session.execute(
                    select(PromoCode, User)
                    .outerjoin(User, PromoCode.redeemed_by_user_id == User.id)
                    .where(PromoCode.code == search_code)
                )
                row = result.first()
                if row:
                    promo_row, user = row
                    status = "Не активирован"
                    if promo_row.redeemed_by_user_id:
                        status = "Активирован"
                    elif not promo_row.active:
                        status = "Отключён"
                    redeemed_user = None
                    user_balance = None
                    if user:
                        name = user.username or "-"
                        redeemed_user = f"{user.telegram_id} ({name})"
                        user_balance = user.balance_credits
                    promo = {
                        "code": promo_row.code,
                        "credits_amount": promo_row.credits_amount,
                        "batch_id": promo_row.batch_id,
                        "status": status,
                        "redeemed_user": redeemed_user,
                        "user_balance": user_balance,
                        "can_deactivate": promo_row.active and not promo_row.redeemed_by_user_id,
                    }

        return app.state.templates.TemplateResponse(
            "promos.html",
            {
                "request": request,
                "title": "Renderis Admin — Промо-коды",
                "active_tab": "promos",
                "batches": batches,
                "promo": promo,
                "search_code": search_code,
                "saved": bool(saved),
            },
        )

    @app.post("/admin/promos/create")
    async def admin_promos_create(
        request: Request,
        amount: str = Form(""),
        credits: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        parsed_amount = _parse_int(amount.strip()) if amount.strip() else None
        parsed_credits = _parse_int(credits.strip()) if credits.strip() else None
        if not parsed_amount or not parsed_credits:
            return RedirectResponse(url="/admin/promos", status_code=302)

        batch_id = uuid.uuid4().hex
        async with app.state.sessionmaker() as session:
            service = PromoService(session)
            await service.create_batch(parsed_amount, parsed_credits, admin_id=0, batch_id=batch_id)
            await session.commit()

        return RedirectResponse(url=f"/admin/promos/batch/{batch_id}", status_code=302)

    @app.get("/admin/promos/batch/{batch_id}", response_class=HTMLResponse)
    async def admin_promos_batch(request: Request, batch_id: str):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        async with app.state.sessionmaker() as session:
            rows_data = await session.execute(
                select(PromoCode, User)
                .outerjoin(User, PromoCode.redeemed_by_user_id == User.id)
                .where(PromoCode.batch_id == batch_id)
                .order_by(PromoCode.code)
            )
            rows = []
            codes = []
            for promo_row, user in rows_data.all():
                status = "new"
                if promo_row.redeemed_by_user_id:
                    status = "redeemed"
                elif not promo_row.active:
                    status = "inactive"
                redeemed_user = None
                user_balance = None
                if user:
                    name = user.username or "-"
                    redeemed_user = f"{user.telegram_id} ({name})"
                    user_balance = user.balance_credits
                rows.append(
                    {
                        "code": promo_row.code,
                        "status": status,
                        "redeemed_user": redeemed_user,
                        "user_balance": user_balance,
                        "can_deactivate": promo_row.active and not promo_row.redeemed_by_user_id,
                    }
                )
                codes.append(promo_row.code)

            summary = await session.execute(
                select(
                    func.min(PromoCode.created_at).label("created_at"),
                    func.count(PromoCode.code).label("count"),
                    func.max(PromoCode.credits_amount).label("credits_amount"),
                ).where(PromoCode.batch_id == batch_id)
            )
            summary_row = summary.first()
            batch = {
                "batch_id": batch_id,
                "created_at": summary_row.created_at if summary_row else None,
                "count": summary_row.count if summary_row else 0,
                "credits_amount": summary_row.credits_amount if summary_row else 0,
            }

        return app.state.templates.TemplateResponse(
            "promo_batch.html",
            {
                "request": request,
                "title": "Renderis Admin — Батч промо-кодов",
                "active_tab": "promos",
                "batch": batch,
                "rows": rows,
                "codes": codes,
            },
        )

    @app.post("/admin/promos/code/{code}/deactivate")
    async def admin_promos_deactivate(request: Request, code: str):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        form = await request.form()
        next_url = str(form.get("next") or "/admin/promos")
        if not next_url.startswith("/"):
            next_url = "/admin/promos"

        async with app.state.sessionmaker() as session:
            promo = await session.get(PromoCode, code.strip().upper())
            if promo and not promo.redeemed_by_user_id:
                promo.active = False
                await session.commit()

        return RedirectResponse(url=next_url, status_code=302)

    return app

