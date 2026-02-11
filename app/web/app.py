from __future__ import annotations

import secrets
import base64
import mimetypes
import time
import uuid
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from starlette.middleware.sessions import SessionMiddleware
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from app.config import get_settings
from app.db.models import CreditLedger, Generation, Order, Price, PromoCode, StarProduct, SupportMessage, SupportThread, User
from app.db.session import create_sessionmaker
from app.modelspecs.registry import list_models
from app.services.app_settings import AppSettingsService
from app.services.credits import CreditsService
from app.services.kie_balance import KieBalanceService
from app.services.promos import PromoService
from app.services.product_pricing import get_product_credits, get_product_stars_price, get_product_usd_price
from app.services.support import SupportService


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".gif", ".bmp", ".avif", ".heic", ".heif", ".jfif"}
MAX_LOGO_SIZE_BYTES = 15 * 1024 * 1024
SUPPORT_MEDIA_DIR = "_support_media"
SUPPORT_MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
MAX_SUPPORT_MEDIA_SIZE_BYTES = 20 * 1024 * 1024
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


def _parse_decimal(value: str) -> Decimal | None:
    raw = (value or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return parsed


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


def _find_asset_file(storage_root: str, stems: list[str]) -> Path | None:
    assets_dir = _site_assets_dir(storage_root)
    if not assets_dir.exists():
        return None
    for stem in stems:
        files = [f for f in assets_dir.glob(f"{stem}.*") if f.is_file() and f.suffix.lower() in LOGO_EXTENSIONS]
        if files:
            return sorted(files, key=lambda f: f.name)[0]
    return None


def _clear_asset_files(storage_root: str, stems: list[str]) -> None:
    assets_dir = _site_assets_dir(storage_root)
    if not assets_dir.exists():
        return
    for stem in stems:
        for file in assets_dir.glob(f"{stem}.*"):
            if file.is_file():
                try:
                    file.unlink()
                except OSError:
                    continue


def _clear_site_logo_files(storage_root: str) -> None:
    _clear_asset_files(storage_root, ["site_logo", "logo"])


def _find_site_logo_file(storage_root: str) -> Path | None:
    return _find_asset_file(storage_root, ["site_logo", "logo"])


def _clear_favicon_logo_files(storage_root: str) -> None:
    _clear_asset_files(storage_root, ["favicon"])


def _find_favicon_logo_file(storage_root: str) -> Path | None:
    return _find_asset_file(storage_root, ["favicon"])


def _site_logo_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".png":
        return "image/png"
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".jfif":
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    if ext == ".avif":
        return "image/avif"
    if ext in (".heic", ".heif"):
        return "image/heic"
    if ext == ".gif":
        return "image/gif"
    if ext == ".bmp":
        return "image/bmp"
    if ext == ".svg":
        return "image/svg+xml"
    if ext == ".ico":
        return "image/x-icon"
    return "application/octet-stream"


def _rounded_favicon_svg(storage_root: str) -> str | None:
    logo_path = _find_favicon_logo_file(storage_root) or _find_site_logo_file(storage_root)
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


def _normalize_logo_ext(upload: UploadFile) -> str:
    ext = Path(upload.filename or "").suffix.lower()
    if ext in LOGO_EXTENSIONS:
        return ext
    content_type = (upload.content_type or "").lower()
    mime_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/x-icon": ".ico",
        "image/vnd.microsoft.icon": ".ico",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
        "image/avif": ".avif",
        "image/heic": ".heic",
        "image/heif": ".heif",
    }
    return mime_map.get(content_type, "")


async def _save_site_logo(upload: UploadFile, storage_root: str) -> tuple[bool, str]:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in LOGO_EXTENSIONS:
        ext = _normalize_logo_ext(upload)
    if ext not in LOGO_EXTENSIONS:
        return False, "unsupported_format"

    content = await upload.read()
    if not content:
        return False, "empty_file"
    if len(content) > MAX_LOGO_SIZE_BYTES:
        return False, "file_too_large"

    assets_dir = _site_assets_dir(storage_root)
    assets_dir.mkdir(parents=True, exist_ok=True)
    _clear_site_logo_files(storage_root)

    filename = f"site_logo{ext}"
    file_path = assets_dir / filename
    file_path.write_bytes(content)
    return True, ""


async def _save_favicon_logo(upload: UploadFile, storage_root: str) -> tuple[bool, str]:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in LOGO_EXTENSIONS:
        ext = _normalize_logo_ext(upload)
    if ext not in LOGO_EXTENSIONS:
        return False, "unsupported_format"

    content = await upload.read()
    if not content:
        return False, "empty_file"
    if len(content) > MAX_LOGO_SIZE_BYTES:
        return False, "file_too_large"

    assets_dir = _site_assets_dir(storage_root)
    assets_dir.mkdir(parents=True, exist_ok=True)
    _clear_favicon_logo_files(storage_root)

    filename = f"favicon{ext}"
    file_path = assets_dir / filename
    file_path.write_bytes(content)
    return True, ""


def _support_media_root(storage_root: str) -> Path:
    return Path(storage_root) / SUPPORT_MEDIA_DIR


def _support_media_mime(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0]
    return guessed or "application/octet-stream"


def _resolve_support_media_path(storage_root: str, rel_path: str) -> Path | None:
    rel = (rel_path or "").strip().replace("\\", "/")
    if not rel:
        return None
    root = _support_media_root(storage_root).resolve()
    full = (Path(storage_root) / rel).resolve()
    try:
        full.relative_to(root)
    except ValueError:
        return None
    if not full.is_file():
        return None
    return full


def _normalize_support_media_ext(upload: UploadFile) -> str:
    ext = Path(upload.filename or "").suffix.lower()
    if ext in SUPPORT_MEDIA_EXTENSIONS:
        return ext
    mime_ext = mimetypes.guess_extension((upload.content_type or "").lower())
    if mime_ext and mime_ext.lower() in SUPPORT_MEDIA_EXTENSIONS:
        return mime_ext.lower()
    return ""


async def _save_support_media_upload(upload: UploadFile, storage_root: str, thread_id: int) -> tuple[bool, dict, str]:
    ext = _normalize_support_media_ext(upload)
    if ext not in SUPPORT_MEDIA_EXTENSIONS:
        return False, {}, "unsupported_format"

    content = await upload.read()
    if not content:
        return False, {}, "empty_file"
    if len(content) > MAX_SUPPORT_MEDIA_SIZE_BYTES:
        return False, {}, "file_too_large"

    thread_dir = _support_media_root(storage_root) / str(thread_id)
    thread_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    abs_path = thread_dir / filename
    abs_path.write_bytes(content)

    rel_path = str(Path(SUPPORT_MEDIA_DIR) / str(thread_id) / filename).replace("\\", "/")
    source_name = (upload.filename or "").strip() or filename
    mime_type = (upload.content_type or "").strip() or _support_media_mime(abs_path)
    return True, {"path": rel_path, "name": source_name, "mime": mime_type}, ""


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

    @app.api_route("/assets/favicon-logo", methods=["GET", "HEAD"])
    async def admin_favicon_logo():
        logo_path = _find_favicon_logo_file(settings.reference_storage_path)
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

    @app.get("/admin/users", response_class=HTMLResponse)
    async def admin_users(
        request: Request,
        q: str | None = None,
        status: str = "all",
        page: int = 1,
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        search = (q or "").strip()
        status = (status or "all").strip().lower()
        if status not in {"all", "banned", "active"}:
            status = "all"
        page = max(1, int(page or 1))
        per_page = 40

        filters = []
        if search:
            search_filters = [func.coalesce(User.username, "").ilike(f"%{search}%")]
            if search.isdigit():
                search_filters.append(User.telegram_id == int(search))
            filters.append(or_(*search_filters))
        if status == "banned":
            filters.append(User.is_banned.is_(True))
        elif status == "active":
            filters.append(User.is_banned.is_(False))

        async with app.state.sessionmaker() as session:
            count_query = select(func.count(User.id))
            list_query = select(User)
            for condition in filters:
                count_query = count_query.where(condition)
                list_query = list_query.where(condition)

            total = int((await session.scalar(count_query)) or 0)
            total_pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, total_pages)
            offset = (page - 1) * per_page
            rows = await session.execute(
                list_query.order_by(User.last_seen_at.desc(), User.id.desc()).offset(offset).limit(per_page)
            )
            users = []
            for user in rows.scalars().all():
                users.append(
                    {
                        "id": user.id,
                        "telegram_id": user.telegram_id,
                        "username": user.username or "—",
                        "balance_credits": user.balance_credits,
                        "is_banned": bool(user.is_banned),
                        "first_seen_at_msk": _format_msk(user.first_seen_at) or "—",
                        "last_seen_at_msk": _format_msk(user.last_seen_at) or "—",
                    }
                )

        return app.state.templates.TemplateResponse(
            "users.html",
            {
                "request": request,
                "title": "Renderis Admin — Пользователи",
                "active_tab": "users",
                "users": users,
                "search": search,
                "status": status,
                "page": page,
                "total": total,
                "per_page": per_page,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "prev_page": page - 1,
                "next_page": page + 1,
            },
        )

    @app.get("/admin/users/{user_id}", response_class=HTMLResponse)
    async def admin_user_profile(
        request: Request,
        user_id: int,
        saved: str | None = None,
        error: str | None = None,
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        async with app.state.sessionmaker() as session:
            user = await session.get(User, user_id)
            if not user:
                return Response(status_code=404, content="User not found")

            orders_rows = await session.execute(
                select(Order)
                .where(Order.user_id == user.id)
                .order_by(Order.created_at.desc())
                .limit(100)
            )
            orders = []
            for order in orders_rows.scalars().all():
                payment_kind = "Stars"
                if (order.payload or "").startswith("crypto:"):
                    payment_kind = "Crypto"
                if (order.payload or "").startswith("wallet:"):
                    payment_kind = "Wallet Pay"
                orders.append(
                    {
                        "id": order.id,
                        "payment_kind": payment_kind,
                        "stars_amount": int(order.stars_amount or 0),
                        "credits_amount": int(order.credits_amount or 0),
                        "status": order.status,
                        "created_at_msk": _format_msk(order.created_at) or "—",
                        "payload": order.payload,
                    }
                )

            ledger_rows = await session.execute(
                select(CreditLedger)
                .where(CreditLedger.user_id == user.id)
                .order_by(CreditLedger.created_at.desc(), CreditLedger.id.desc())
                .limit(200)
            )
            ledger = []
            for item in ledger_rows.scalars().all():
                ledger.append(
                    {
                        "id": item.id,
                        "delta": int(item.delta_credits or 0),
                        "reason": item.reason,
                        "meta": item.meta or {},
                        "created_at_msk": _format_msk(item.created_at) or "—",
                    }
                )

            promo_rows = await session.execute(
                select(PromoCode)
                .where(PromoCode.redeemed_by_user_id == user.id)
                .order_by(PromoCode.redeemed_at.desc(), PromoCode.code.asc())
                .limit(100)
            )
            promo_codes = []
            for promo in promo_rows.scalars().all():
                promo_codes.append(
                    {
                        "code": promo.code,
                        "credits_amount": int(promo.credits_amount or 0),
                        "batch_id": promo.batch_id or "—",
                        "redeemed_at_msk": _format_msk(promo.redeemed_at) or "—",
                        "active": bool(promo.active),
                    }
                )

        return app.state.templates.TemplateResponse(
            "user_profile.html",
            {
                "request": request,
                "title": f"Renderis Admin — Пользователь {user.telegram_id}",
                "active_tab": "users",
                "user": {
                    "id": user.id,
                    "telegram_id": user.telegram_id,
                    "username": user.username or "—",
                    "balance_credits": user.balance_credits,
                    "is_banned": bool(user.is_banned),
                    "first_seen_at_msk": _format_msk(user.first_seen_at) or "—",
                    "last_seen_at_msk": _format_msk(user.last_seen_at) or "—",
                },
                "orders": orders,
                "ledger": ledger,
                "promo_codes": promo_codes,
                "saved": (saved or "").strip().lower(),
                "error": (error or "").strip().lower(),
            },
        )

    @app.post("/admin/users/{user_id}/ban")
    async def admin_user_ban(request: Request, user_id: int):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        async with app.state.sessionmaker() as session:
            user = await session.get(User, user_id)
            if not user:
                return RedirectResponse(url="/admin/users?error=user_not_found", status_code=302)
            user.is_banned = True
            await session.commit()
        return RedirectResponse(url=f"/admin/users/{user_id}?saved=banned", status_code=302)

    @app.post("/admin/users/{user_id}/unban")
    async def admin_user_unban(request: Request, user_id: int):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        async with app.state.sessionmaker() as session:
            user = await session.get(User, user_id)
            if not user:
                return RedirectResponse(url="/admin/users?error=user_not_found", status_code=302)
            user.is_banned = False
            await session.commit()
        return RedirectResponse(url=f"/admin/users/{user_id}?saved=unbanned", status_code=302)

    @app.post("/admin/users/{user_id}/credits/add")
    async def admin_user_credits_add(
        request: Request,
        user_id: int,
        amount: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        parsed_amount = _parse_int(amount.strip())
        if parsed_amount is None or parsed_amount <= 0:
            return RedirectResponse(url=f"/admin/users/{user_id}?error=invalid_amount", status_code=302)
        async with app.state.sessionmaker() as session:
            user = await session.get(User, user_id)
            if not user:
                return RedirectResponse(url="/admin/users?error=user_not_found", status_code=302)
            credits = CreditsService(session)
            await credits.add_ledger(
                user,
                parsed_amount,
                "admin_adjust_add",
                meta={"source": "admin_web", "action": "add", "amount": parsed_amount},
            )
            await session.commit()
        return RedirectResponse(url=f"/admin/users/{user_id}?saved=credits_added", status_code=302)

    @app.post("/admin/users/{user_id}/credits/subtract")
    async def admin_user_credits_subtract(
        request: Request,
        user_id: int,
        amount: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        parsed_amount = _parse_int(amount.strip())
        if parsed_amount is None or parsed_amount <= 0:
            return RedirectResponse(url=f"/admin/users/{user_id}?error=invalid_amount", status_code=302)
        async with app.state.sessionmaker() as session:
            user = await session.get(User, user_id)
            if not user:
                return RedirectResponse(url="/admin/users?error=user_not_found", status_code=302)
            if int(user.balance_credits or 0) < parsed_amount:
                return RedirectResponse(url=f"/admin/users/{user_id}?error=insufficient_balance", status_code=302)
            credits = CreditsService(session)
            await credits.add_ledger(
                user,
                -parsed_amount,
                "admin_adjust_subtract",
                meta={"source": "admin_web", "action": "subtract", "amount": parsed_amount},
            )
            await session.commit()
        return RedirectResponse(url=f"/admin/users/{user_id}?saved=credits_subtracted", status_code=302)

    @app.post("/admin/users/{user_id}/credits/set")
    async def admin_user_credits_set(
        request: Request,
        user_id: int,
        balance: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        parsed_balance = _parse_int(balance.strip())
        if parsed_balance is None or parsed_balance < 0:
            return RedirectResponse(url=f"/admin/users/{user_id}?error=invalid_balance", status_code=302)
        async with app.state.sessionmaker() as session:
            user = await session.get(User, user_id)
            if not user:
                return RedirectResponse(url="/admin/users?error=user_not_found", status_code=302)
            current_balance = int(user.balance_credits or 0)
            delta = parsed_balance - current_balance
            if delta != 0:
                credits = CreditsService(session)
                await credits.add_ledger(
                    user,
                    delta,
                    "admin_set_balance",
                    meta={
                        "source": "admin_web",
                        "action": "set_balance",
                        "from": current_balance,
                        "to": parsed_balance,
                    },
                )
                await session.commit()
                return RedirectResponse(url=f"/admin/users/{user_id}?saved=balance_set", status_code=302)
        return RedirectResponse(url=f"/admin/users/{user_id}?saved=balance_unchanged", status_code=302)

    @app.post("/admin/users/{user_id}/promos/{code}/revoke")
    async def admin_user_promo_revoke(request: Request, user_id: int, code: str):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        promo_code = (code or "").strip().upper()
        if not promo_code:
            return RedirectResponse(url=f"/admin/users/{user_id}?error=promo_not_found", status_code=302)
        async with app.state.sessionmaker() as session:
            user = await session.get(User, user_id)
            if not user:
                return RedirectResponse(url="/admin/users?error=user_not_found", status_code=302)

            promo = await session.get(PromoCode, promo_code)
            if not promo or promo.redeemed_by_user_id != user.id:
                return RedirectResponse(url=f"/admin/users/{user_id}?error=promo_not_found", status_code=302)
            if not promo.active:
                return RedirectResponse(url=f"/admin/users/{user_id}?error=promo_already_revoked", status_code=302)

            credits_delta = int(promo.credits_amount or 0)
            if credits_delta > 0:
                credits_service = CreditsService(session)
                await credits_service.add_ledger(
                    user,
                    -credits_delta,
                    "admin_promo_revoke",
                    meta={"source": "admin_web", "action": "promo_revoke", "code": promo.code, "credits": credits_delta},
                )
            promo.active = False
            await session.commit()

        return RedirectResponse(url=f"/admin/users/{user_id}?saved=promo_revoked", status_code=302)

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

            topup_rows = (
                await session.execute(
                    select(StarProduct).order_by(StarProduct.sort_order.asc(), StarProduct.id.asc())
                )
            ).scalars().all()
            topup_products: list[dict] = []
            for row in topup_rows:
                credits_base = int(row.credits_base if row.credits_base is not None else row.credits_amount)
                credits_bonus = int(row.credits_bonus or 0)
                credits_total = get_product_credits(row)
                stars_effective = get_product_stars_price(row)
                usd_effective = float(get_product_usd_price(row, stars_per_credit, usd_per_star))
                stars_per_credit_eff = round(stars_effective / credits_total, 4) if credits_total > 0 else 0.0
                usd_per_credit_eff = round(usd_effective / credits_total, 4) if credits_total > 0 else 0.0
                price_usd_value = ""
                if row.price_usd is not None:
                    price_usd_value = f"{Decimal(str(row.price_usd)):.2f}"
                topup_products.append(
                    {
                        "id": row.id,
                        "title": row.title,
                        "credits_base": credits_base,
                        "credits_bonus": credits_bonus,
                        "credits_total": credits_total,
                        "price_stars": int(row.price_stars if row.price_stars is not None else stars_effective),
                        "price_usd": price_usd_value,
                        "sort_order": row.sort_order,
                        "active": bool(row.active),
                        "stars_effective": stars_effective,
                        "usd_effective": round(usd_effective, 2),
                        "stars_per_credit": stars_per_credit_eff,
                        "usd_per_credit": usd_per_credit_eff,
                    }
                )

        return app.state.templates.TemplateResponse(
            "products.html",
            {
                "request": request,
                "title": "Renderis Admin — Товары",
                "active_tab": "products",
                "products": products,
                "topup_products": topup_products,
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

    @app.post("/admin/topup-products/{product_id}")
    async def admin_topup_product_update(
        request: Request,
        product_id: int,
        title: str = Form(""),
        credits_base: str = Form(""),
        credits_bonus: str = Form(""),
        price_stars: str = Form(""),
        price_usd: str = Form(""),
        sort_order: str = Form(""),
        active: str = Form("0"),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        async with app.state.sessionmaker() as session:
            product = await session.get(StarProduct, product_id)
            if not product:
                return RedirectResponse(url="/admin/products", status_code=302)

            title_clean = title.strip()
            if title_clean:
                product.title = title_clean

            parsed_base = _parse_int(credits_base.strip())
            parsed_bonus = _parse_int(credits_bonus.strip())
            parsed_stars = _parse_int(price_stars.strip())
            parsed_sort = _parse_int(sort_order.strip())
            parsed_usd = _parse_decimal(price_usd.strip()) if price_usd.strip() else None

            if parsed_base is not None:
                product.credits_base = max(0, parsed_base)
            if parsed_bonus is not None:
                product.credits_bonus = max(0, parsed_bonus)
            if parsed_sort is not None:
                product.sort_order = parsed_sort

            credits_total = max(0, int(product.credits_base or 0) + int(product.credits_bonus or 0))
            product.credits_amount = credits_total

            if parsed_stars is not None and parsed_stars > 0:
                product.price_stars = parsed_stars
                # Keep legacy field in sync for old integrations.
                product.stars_amount = parsed_stars

            if price_usd.strip():
                if parsed_usd is not None and parsed_usd > 0:
                    product.price_usd = parsed_usd.quantize(Decimal("0.01"))
            else:
                product.price_usd = None

            product.active = active.strip() == "1"
            await session.commit()

        return RedirectResponse(url="/admin/products?saved=1", status_code=302)

    @app.post("/admin/topup-products/create")
    async def admin_topup_product_create(
        request: Request,
        title: str = Form(""),
        credits_base: str = Form(""),
        credits_bonus: str = Form(""),
        price_stars: str = Form(""),
        price_usd: str = Form(""),
        sort_order: str = Form("0"),
        active: str = Form("1"),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        title_clean = title.strip() or "Новый пакет"
        parsed_base = _parse_int(credits_base.strip())
        parsed_bonus = _parse_int(credits_bonus.strip())
        parsed_stars = _parse_int(price_stars.strip())
        parsed_sort = _parse_int(sort_order.strip())
        parsed_usd = _parse_decimal(price_usd.strip()) if price_usd.strip() else None

        base_val = max(0, parsed_base or 0)
        bonus_val = max(0, parsed_bonus or 0)
        stars_val = max(1, parsed_stars or 1)
        credits_total = base_val + bonus_val
        if credits_total <= 0:
            credits_total = base_val or 1
            base_val = credits_total
            bonus_val = 0

        async with app.state.sessionmaker() as session:
            product = StarProduct(
                title=title_clean,
                stars_amount=stars_val,
                credits_amount=credits_total,
                credits_base=base_val,
                credits_bonus=bonus_val,
                price_stars=stars_val,
                price_usd=parsed_usd.quantize(Decimal("0.01")) if parsed_usd and parsed_usd > 0 else None,
                active=active.strip() == "1",
                sort_order=parsed_sort or 0,
            )
            session.add(product)
            await session.commit()

        return RedirectResponse(url="/admin/products?saved=1", status_code=302)

    @app.get("/admin/settings", response_class=HTMLResponse)
    async def admin_settings(request: Request, saved: int | None = None, error: str | None = None):
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
            favicon_logo_url = ""
            favicon_path = _find_favicon_logo_file(settings.reference_storage_path)
            if favicon_path:
                favicon_version = (await settings_service.get("favicon_logo_version")) or str(
                    int(favicon_path.stat().st_mtime_ns)
                )
                favicon_logo_url = f"/assets/favicon-logo?v={favicon_version}"

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
                "favicon_logo_url": favicon_logo_url,
                "logo_max_size_mb": int(MAX_LOGO_SIZE_BYTES / 1024 / 1024),
                "saved": bool(saved),
                "error": (error or "").strip(),
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
        favicon_file: UploadFile | None = File(default=None),
        remove_favicon_logo: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        async with app.state.sessionmaker() as session:
            settings_service = AppSettingsService(session)
            kie_balance_service = KieBalanceService(session)
            errors: list[str] = []
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
                logo_saved, logo_error = await _save_site_logo(logo_file, settings.reference_storage_path)
                if logo_saved:
                    await settings_service.set("site_logo_url", "/assets/site-logo")
                    await settings_service.set("site_logo_version", str(int(time.time())))
                elif logo_error:
                    errors.append(f"site_logo:{logo_error}")
            if remove_favicon_logo.strip() == "1":
                _clear_favicon_logo_files(settings.reference_storage_path)
                await settings_service.set("favicon_logo_version", str(int(time.time())))
            elif favicon_file and (favicon_file.filename or "").strip():
                favicon_saved, favicon_error = await _save_favicon_logo(favicon_file, settings.reference_storage_path)
                if favicon_saved:
                    await settings_service.set("favicon_logo_version", str(int(time.time())))
                elif favicon_error:
                    errors.append(f"favicon_logo:{favicon_error}")
            await session.commit()
        if errors:
            return RedirectResponse(url=f"/admin/settings?error={'|'.join(errors)}", status_code=302)
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
        settings = get_settings()
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
                        "user_id": user.id,
                        "telegram_id": user.telegram_id,
                        "username": user.username or "",
                        "user_label": label,
                        "last_message_at": thread.last_message_at.isoformat(),
                        "status": thread.status,
                    }
                )
        return {"threads": threads, "support_enabled": bool(settings.support_bot_token)}

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
                    "media_type": msg.media_type,
                    "media_url": f"/admin/api/chats/media/{msg.id}" if msg.media_path and msg.media_type == "image" else None,
                    "media_file_name": msg.media_file_name,
                    "created_at": msg.created_at.isoformat(),
                }
                for msg in rows.scalars().all()
            ]
        return {"messages": messages}

    @app.get("/admin/api/chats/media/{message_id}")
    async def admin_chat_media(request: Request, message_id: int):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        settings = get_settings()
        async with app.state.sessionmaker() as session:
            msg = await session.get(SupportMessage, message_id)
            if not msg or not msg.media_path:
                return Response(status_code=404)
            media_file = _resolve_support_media_path(settings.reference_storage_path, msg.media_path)
            if not media_file:
                return Response(status_code=404)
            media_type = (msg.media_mime_type or "").strip() or _support_media_mime(media_file)
            return FileResponse(path=str(media_file), media_type=media_type)

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
                sent = await bot.send_message(user.telegram_id, text, parse_mode=None)
            finally:
                await bot.session.close()

            await support.add_message(thread, "admin", text, sender_admin_id=0, tg_message_id=sent.message_id)
            await session.commit()

        return {"ok": True}

    @app.post("/admin/api/chats/{thread_id}/send-media")
    async def admin_chats_send_media(
        request: Request,
        thread_id: int,
        file: UploadFile | None = File(default=None),
        caption: str = Form(""),
    ):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if file is None:
            return JSONResponse({"error": "missing_file"}, status_code=400)

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

            ok, media_meta, media_error = await _save_support_media_upload(file, settings.reference_storage_path, thread_id)
            if not ok:
                return JSONResponse({"error": media_error or "upload_failed"}, status_code=400)

            media_file = _resolve_support_media_path(settings.reference_storage_path, media_meta["path"])
            if not media_file:
                return JSONResponse({"error": "upload_failed"}, status_code=400)

            bot = Bot(
                token=settings.support_bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            try:
                sent = await bot.send_photo(
                    user.telegram_id,
                    FSInputFile(str(media_file)),
                    caption=(caption or "").strip() or None,
                    parse_mode=None,
                )
            finally:
                await bot.session.close()

            text = (caption or "").strip() or "📷 Изображение"
            await support.add_message(
                thread,
                "admin",
                text,
                sender_admin_id=0,
                tg_message_id=sent.message_id,
                media_type="image",
                media_path=media_meta["path"],
                media_file_name=media_meta.get("name"),
                media_mime_type=media_meta.get("mime"),
            )
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

