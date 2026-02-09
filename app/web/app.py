from __future__ import annotations

import secrets
import base64
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import get_settings
from app.db.models import CreditLedger, Generation, Order, Price, PromoCode, SupportMessage, SupportThread, User
from app.db.session import create_sessionmaker
from app.modelspecs.registry import list_models
from app.services.app_settings import AppSettingsService
from app.services.kie_balance import KieBalanceService
from app.services.promos import PromoService
from app.services.support import SupportService


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico"}
MAX_LOGO_SIZE_BYTES = 5 * 1024 * 1024
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


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


def _format_msk(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if ZoneInfo is not None:
        msk = dt.astimezone(ZoneInfo("Europe/Moscow"))
    else:
        msk = dt.astimezone(timezone(timedelta(hours=3)))
    return msk.strftime("%d.%m.%Y %H:%M:%S")


def _site_assets_dir(storage_root: str) -> Path:
    return Path(storage_root) / "_site"


def _clear_site_logo_files(storage_root: str) -> None:
    assets_dir = _site_assets_dir(storage_root)
    if not assets_dir.exists():
        return
    for file in assets_dir.glob("logo.*"):
        if file.is_file():
            try:
                file.unlink()
            except OSError:
                continue


def _find_site_logo_file(storage_root: str) -> Path | None:
    assets_dir = _site_assets_dir(storage_root)
    if not assets_dir.exists():
        return None
    files = [f for f in assets_dir.glob("logo.*") if f.is_file() and f.suffix.lower() in LOGO_EXTENSIONS]
    if not files:
        return None
    return sorted(files, key=lambda f: f.name)[0]


def _site_logo_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".png":
        return "image/png"
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    if ext == ".svg":
        return "image/svg+xml"
    if ext == ".ico":
        return "image/x-icon"
    return "application/octet-stream"


def _rounded_favicon_svg(storage_root: str) -> str | None:
    logo_path = _find_site_logo_file(storage_root)
    if not logo_path:
        return None
    raw = logo_path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    data_uri = f"data:{_site_logo_mime(logo_path)};base64,{encoded}"
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'>"
        "<defs><clipPath id='r'><rect x='0' y='0' width='64' height='64' rx='14' ry='14'/></clipPath></defs>"
        "<rect x='0' y='0' width='64' height='64' fill='transparent'/>"
        f"<image href='{data_uri}' x='0' y='0' width='64' height='64' preserveAspectRatio='xMidYMid slice' clip-path='url(#r)'/>"
        "</svg>"
    )


async def _save_site_logo(upload: UploadFile, storage_root: str) -> bool:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in LOGO_EXTENSIONS:
        return False

    content = await upload.read()
    if not content or len(content) > MAX_LOGO_SIZE_BYTES:
        return False

    assets_dir = _site_assets_dir(storage_root)
    assets_dir.mkdir(parents=True, exist_ok=True)
    _clear_site_logo_files(storage_root)

    filename = f"logo{ext}"
    file_path = assets_dir / filename
    file_path.write_bytes(content)
    return True


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

    @app.api_route("/assets/site-logo", methods=["GET", "HEAD"])
    async def admin_site_logo():
        logo_path = _find_site_logo_file(settings.reference_storage_path)
        if not logo_path:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return FileResponse(path=str(logo_path))

    @app.api_route("/favicon.svg", methods=["GET", "HEAD"])
    async def admin_favicon_svg():
        svg = _rounded_favicon_svg(settings.reference_storage_path)
        if not svg:
            return Response(status_code=404)
        return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})

    @app.api_route("/favicon.ico", methods=["GET", "HEAD"])
    async def admin_favicon():
        return await admin_favicon_svg()

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

            recent_orders_rows = await session.execute(
                select(Order, User)
                .outerjoin(User, Order.user_id == User.id)
                .order_by(Order.created_at.desc())
                .limit(10)
            )
            recent_orders = []
            for order_row, user_row in recent_orders_rows.all():
                username = user_row.username if user_row and user_row.username else "-"
                user_label = f"{order_row.user_id}"
                if user_row:
                    user_label = f"{user_row.telegram_id} ({username})"
                recent_orders.append(
                    {
                        "id": order_row.id,
                        "user_label": user_label,
                        "stars_amount": order_row.stars_amount,
                        "credits_amount": order_row.credits_amount,
                        "status": order_row.status,
                        "created_at_msk": _format_msk(order_row.created_at) or "—",
                    }
                )
            recent_gens_rows = await session.execute(
                select(Generation, User)
                .outerjoin(User, Generation.user_id == User.id)
                .order_by(Generation.created_at.desc())
                .limit(10)
            )
            recent_gens = []
            for gen_row, user_row in recent_gens_rows.all():
                username = user_row.username if user_row and user_row.username else "-"
                user_label = f"{gen_row.user_id}"
                if user_row:
                    user_label = f"{user_row.telegram_id} ({username})"
                recent_gens.append(
                    {
                        "id": gen_row.id,
                        "user_label": user_label,
                        "model": gen_row.model,
                        "status": gen_row.status,
                        "created_at_msk": _format_msk(gen_row.created_at) or "—",
                    }
                )

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
            site_logo_url = ""
            logo_path = _find_site_logo_file(settings.reference_storage_path)
            if logo_path:
                logo_version = (await settings_service.get("site_logo_version")) or str(int(logo_path.stat().st_mtime_ns))
                site_logo_url = f"/assets/site-logo?v={logo_version}"

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
                "site_logo_url": site_logo_url,
                "logo_max_size_mb": int(MAX_LOGO_SIZE_BYTES / 1024 / 1024),
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
        logo_file: UploadFile | None = File(default=None),
        remove_logo: str = Form(""),
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
            if remove_logo.strip() == "1":
                _clear_site_logo_files(settings.reference_storage_path)
                await settings_service.set("site_logo_url", "")
                await settings_service.set("site_logo_version", str(int(time.time())))
            elif logo_file and (logo_file.filename or "").strip():
                logo_saved = await _save_site_logo(logo_file, settings.reference_storage_path)
                if logo_saved:
                    await settings_service.set("site_logo_url", "/assets/site-logo")
                    await settings_service.set("site_logo_version", str(int(time.time())))
            await session.commit()

        return RedirectResponse(url="/admin/settings?saved=1", status_code=302)

    @app.get("/admin/chats", response_class=HTMLResponse)
    async def admin_chats(request: Request):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        return app.state.templates.TemplateResponse(
            "chats.html",
            {
                "request": request,
                "title": "Renderis Admin — Чаты",
                "active_tab": "chats",
            },
        )

    @app.get("/admin/api/chats")
    async def admin_chats_list(request: Request):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        async with app.state.sessionmaker() as session:
            rows = await session.execute(
                select(SupportThread, User)
                .join(User, SupportThread.user_id == User.id)
                .order_by(SupportThread.last_message_at.desc())
            )
            threads = []
            for thread, user in rows.all():
                label = user.username or str(user.telegram_id)
                threads.append(
                    {
                        "id": thread.id,
                        "user_label": label,
                        "last_message_at": thread.last_message_at.isoformat(),
                        "status": thread.status,
                    }
                )
        return {"threads": threads}

    @app.get("/admin/api/chats/{thread_id}/messages")
    async def admin_chats_messages(request: Request, thread_id: int):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        async with app.state.sessionmaker() as session:
            rows = await session.execute(
                select(SupportMessage)
                .where(SupportMessage.thread_id == thread_id)
                .order_by(SupportMessage.id)
            )
            messages = [
                {
                    "id": msg.id,
                    "sender_type": msg.sender_type,
                    "text": msg.text,
                    "created_at": msg.created_at.isoformat(),
                }
                for msg in rows.scalars().all()
            ]
        return {"messages": messages}

    @app.post("/admin/api/chats/{thread_id}/send")
    async def admin_chats_send(request: Request, thread_id: int, payload: dict = Body(...)):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        text = (payload.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "empty"}, status_code=400)

        settings = get_settings()
        if not settings.support_bot_token:
            return JSONResponse({"error": "support_bot_missing"}, status_code=400)

        async with app.state.sessionmaker() as session:
            support = SupportService(session)
            thread = await support.get_thread(thread_id)
            if not thread:
                return JSONResponse({"error": "not_found"}, status_code=404)
            user = await session.get(User, thread.user_id)
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)

            bot = Bot(
                token=settings.support_bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            try:
                sent = await bot.send_message(user.telegram_id, text)
            finally:
                await bot.session.close()

            await support.add_message(thread, "admin", text, sender_admin_id=0, tg_message_id=sent.message_id)
            await session.commit()

        return {"ok": True}

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
                        "redeemed_at_msk": _format_msk(promo_row.redeemed_at),
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
                        "redeemed_at_msk": _format_msk(promo_row.redeemed_at),
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

