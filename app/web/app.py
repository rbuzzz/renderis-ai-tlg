from __future__ import annotations

import secrets
import base64
import logging
import mimetypes
import shutil
import time
import uuid
from html import escape
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, delete, func, or_, select
from starlette.middleware.sessions import SessionMiddleware
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import get_settings
from app.db.models import (
    AdminChangeComment,
    AdminChangeRequest,
    CreditLedger,
    Generation,
    GenerationTask,
    Order,
    Price,
    PromoCode,
    StarProduct,
    SupportMessage,
    SupportThread,
    User,
)
from app.db.session import create_sessionmaker
from app.modelspecs.registry import list_models
from app.services.app_settings import AppSettingsService
from app.services.brain import AIBrainService
from app.services.credits import CreditsService
from app.services.kie_balance import KieBalanceService
from app.services.promos import PromoService
from app.services.product_pricing import get_product_credits, get_product_stars_price, get_product_usd_price
from app.services.support import SupportService
from app.services.change_requests import (
    CHANGE_ADD_CREDITS,
    CHANGE_REVOKE_PROMO,
    CHANGE_SET_BALANCE,
    CHANGE_SUBTRACT_CREDITS,
    STATUS_APPLIED,
    STATUS_CANCELLED,
    STATUS_DRAFT,
    STATUS_NEEDS_INFO,
    STATUS_PENDING,
    STATUS_REJECTED,
    ChangeRequestService,
)


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".gif", ".bmp", ".avif", ".heic", ".heif", ".jfif"}
MAX_LOGO_SIZE_BYTES = 15 * 1024 * 1024
SUPPORT_MEDIA_DIR = "_support_media"
SUPPORT_MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
MAX_SUPPORT_MEDIA_SIZE_BYTES = 20 * 1024 * 1024
logger = logging.getLogger(__name__)
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


def _is_logged_in(request: Request) -> bool:
    return bool(request.session.get("admin_logged_in"))


def _session_role(request: Request) -> str:
    role = request.session.get("admin_role")
    if role == "subadmin":
        return "subadmin"
    return "admin"


def _is_subadmin(request: Request) -> bool:
    return _is_logged_in(request) and _session_role(request) == "subadmin"


def _can_manage(request: Request) -> bool:
    return _is_logged_in(request) and _session_role(request) == "admin"


def _session_login(request: Request) -> str:
    return str(request.session.get("admin_login") or "").strip()


def _forbidden_redirect() -> RedirectResponse:
    return RedirectResponse(url="/admin?error=forbidden", status_code=302)


def _change_type_title(change_type: str) -> str:
    if change_type == CHANGE_ADD_CREDITS:
        return "Начислить кредиты"
    if change_type == CHANGE_SUBTRACT_CREDITS:
        return "Списать кредиты"
    if change_type == CHANGE_SET_BALANCE:
        return "Установить баланс"
    if change_type == CHANGE_REVOKE_PROMO:
        return "Отозвать промо-код"
    return change_type


def _change_status_title(status: str) -> str:
    if status == STATUS_DRAFT:
        return "Черновик"
    if status == STATUS_PENDING:
        return "На согласовании"
    if status == STATUS_NEEDS_INFO:
        return "Нужен комментарий"
    if status == STATUS_REJECTED:
        return "Отклонено"
    if status == STATUS_CANCELLED:
        return "Отменено"
    if status == STATUS_APPLIED:
        return "Применено"
    return status


def _change_status_badge(status: str) -> str:
    if status == STATUS_APPLIED:
        return "ok"
    if status in {STATUS_REJECTED, STATUS_CANCELLED}:
        return "danger"
    if status in {STATUS_PENDING, STATUS_NEEDS_INFO}:
        return "warn"
    return ""


def _change_action_preview(
    *,
    change_type: str,
    credits_amount: int | None,
    balance_value: int | None,
    promo_code: str | None,
) -> str:
    if change_type in {CHANGE_ADD_CREDITS, CHANGE_SUBTRACT_CREDITS}:
        return str(int(credits_amount or 0))
    if change_type == CHANGE_SET_BALANCE:
        return str(int(balance_value or 0))
    if change_type == CHANGE_REVOKE_PROMO:
        return (promo_code or "").strip().upper() or "—"
    return "—"


def _admin_change_requests_url() -> str | None:
    settings = get_settings()
    if not settings.admin_web_public_url:
        return None

    base = settings.admin_web_public_url.rstrip("/")
    if base.endswith("/admin/change-requests"):
        return base
    if base.endswith("/admin"):
        return f"{base}/change-requests"
    return f"{base}/admin/change-requests"


def _change_request_action_line(item: dict[str, object]) -> str:
    change_type = str(item.get("change_type") or "")
    if change_type == CHANGE_ADD_CREDITS:
        return f"Начислить <b>+{int(item.get('credits_amount') or 0)}</b> кредитов"
    if change_type == CHANGE_SUBTRACT_CREDITS:
        return f"Списать <b>-{int(item.get('credits_amount') or 0)}</b> кредитов"
    if change_type == CHANGE_SET_BALANCE:
        return f"Установить баланс: <b>{int(item.get('balance_value') or 0)}</b>"
    if change_type == CHANGE_REVOKE_PROMO:
        code = (str(item.get("promo_code") or "")).strip().upper() or "—"
        return f"Отозвать промо-код: <code>{escape(code)}</code>"
    return escape(change_type)


def _build_change_request_notify_item(req: AdminChangeRequest, user: User) -> dict[str, object]:
    return {
        "id": req.id,
        "change_type": req.change_type,
        "credits_amount": req.credits_amount,
        "balance_value": req.balance_value,
        "promo_code": req.promo_code,
        "reason": req.reason,
        "created_by_login": req.created_by_login,
        "target_user_id": user.id,
        "target_telegram_id": user.telegram_id,
        "target_username": user.username or "",
    }


def _change_request_notify_text(item: dict[str, object]) -> str:
    request_id = int(item.get("id") or 0)
    tg_id = int(item.get("target_telegram_id") or 0)
    username = (str(item.get("target_username") or "")).strip()
    display_name = f"@{username}" if username else "—"
    reason = escape((str(item.get("reason") or "")).strip() or "—")
    created_by = escape((str(item.get("created_by_login") or "")).strip() or "subadmin")
    action_title = _change_type_title(str(item.get("change_type") or ""))

    lines = [
        "📝 Новое предложение на согласование",
        f"ID: <b>#{request_id}</b>",
        f"Тип: <b>{escape(action_title)}</b>",
        f"Пользователь: <code>{tg_id}</code> ({escape(display_name)})",
        f"Действие: {_change_request_action_line(item)}",
        f"Причина: {reason}",
        f"Автор: <b>{created_by}</b>",
    ]
    return "\n".join(lines)


def _change_request_update_text(
    item: dict[str, object],
    *,
    headline: str,
    status_title: str | None = None,
    comment: str | None = None,
    actor: str | None = None,
    context: str | None = None,
) -> str:
    request_id = int(item.get("id") or 0)
    tg_id = int(item.get("target_telegram_id") or 0)
    username = (str(item.get("target_username") or "")).strip()
    display_name = f"@{username}" if username else "—"
    reason = escape((str(item.get("reason") or "")).strip() or "—")
    action_title = _change_type_title(str(item.get("change_type") or ""))

    lines = [
        escape(headline),
        f"ID: <b>#{request_id}</b>",
        f"Тип: <b>{escape(action_title)}</b>",
        f"Пользователь: <code>{tg_id}</code> ({escape(display_name)})",
        f"Действие: {_change_request_action_line(item)}",
        f"Причина: {reason}",
    ]
    if status_title:
        lines.append(f"Статус: <b>{escape(status_title)}</b>")
    if actor:
        lines.append(f"Кто изменил: <b>{escape(actor)}</b>")
    if comment:
        lines.append(f"Комментарий: {escape(comment.strip())}")
    if context:
        lines.append("")
        lines.append("Контекст переписки:")
        lines.append(context)
    return "\n".join(lines)


def _change_request_review_keyboard(request_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="✅ Утвердить", callback_data=f"cr:approve:{request_id}")],
        [
            InlineKeyboardButton(text="❓ Уточнить", callback_data=f"cr:info:{request_id}"),
            InlineKeyboardButton(text="⛔ Отклонить", callback_data=f"cr:reject:{request_id}"),
        ],
    ]
    admin_url = _admin_change_requests_url()
    if admin_url:
        rows.append([InlineKeyboardButton(text="Открыть в админке", url=admin_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _change_request_open_keyboard() -> InlineKeyboardMarkup | None:
    admin_url = _admin_change_requests_url()
    if not admin_url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть предложения", url=admin_url)]]
    )


async def _notify_admins_about_change_request(item: dict[str, object]) -> None:
    settings = get_settings()
    if not settings.support_bot_token:
        return

    admin_ids = settings.admin_ids()
    if not admin_ids:
        return

    message_text = _change_request_notify_text(item)
    keyboard = _change_request_review_keyboard(int(item.get("id") or 0))
    bot = Bot(
        token=settings.support_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, message_text, reply_markup=keyboard)
            except Exception:
                logger.exception(
                    "Failed to notify admin about change request id=%s admin_id=%s",
                    item.get("id"),
                    admin_id,
                )
                continue
    finally:
        await bot.session.close()


async def _notify_admins_about_change_request_update(
    item: dict[str, object],
    *,
    headline: str,
    status_title: str | None = None,
    comment: str | None = None,
    actor: str | None = None,
    context: str | None = None,
    include_review_actions: bool = False,
) -> None:
    settings = get_settings()
    if not settings.support_bot_token:
        return

    admin_ids = settings.admin_ids()
    if not admin_ids:
        return

    message_text = _change_request_update_text(
        item,
        headline=headline,
        status_title=status_title,
        comment=comment,
        actor=actor,
        context=context,
    )
    request_id = int(item.get("id") or 0)
    if include_review_actions and request_id > 0:
        keyboard = _change_request_review_keyboard(request_id)
    else:
        keyboard = _change_request_open_keyboard()
    bot = Bot(
        token=settings.support_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, message_text, reply_markup=keyboard)
            except Exception:
                logger.exception(
                    "Failed to notify admin about change request update id=%s admin_id=%s",
                    item.get("id"),
                    admin_id,
                )
                continue
    finally:
        await bot.session.close()


async def _notify_subadmins_about_change_request_update(
    item: dict[str, object],
    *,
    headline: str,
    status_title: str | None = None,
    comment: str | None = None,
    actor: str | None = None,
    context: str | None = None,
) -> None:
    settings = get_settings()
    if not settings.support_bot_token:
        return

    subadmin_ids = settings.subadmin_ids()
    if not subadmin_ids:
        return

    message_text = _change_request_update_text(
        item,
        headline=headline,
        status_title=status_title,
        comment=comment,
        actor=actor,
        context=context,
    )
    keyboard = _change_request_open_keyboard()
    bot = Bot(
        token=settings.support_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        for subadmin_id in subadmin_ids:
            try:
                await bot.send_message(subadmin_id, message_text, reply_markup=keyboard)
            except Exception:
                logger.exception(
                    "Failed to notify subadmin about change request update id=%s subadmin_id=%s",
                    item.get("id"),
                    subadmin_id,
                )
                continue
    finally:
        await bot.session.close()


async def _build_change_request_context(session, request_id: int, *, limit: int = 10) -> str | None:
    rows = await session.execute(
        select(AdminChangeComment)
        .where(AdminChangeComment.request_id == request_id)
        .order_by(AdminChangeComment.created_at.asc(), AdminChangeComment.id.asc())
    )
    comments = rows.scalars().all()
    if not comments:
        return None

    lines: list[str] = []
    for comment in comments[-limit:]:
        message = (comment.message or "").strip()
        if not message:
            continue
        role_title = "Админ" if comment.author_role == "admin" else "Субадмин"
        author = (comment.author_login or "").strip() or comment.author_role
        lines.append(f"• <b>{escape(role_title)}</b> ({escape(author)}): {escape(message)}")

    if not lines:
        return None
    return "\n".join(lines)


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


def _format_duration_seconds(value: float | None) -> str:
    if value is None:
        return "—"
    total_seconds = max(0, int(round(value)))
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours}ч {minutes}м {seconds}с"
    if minutes > 0:
        return f"{minutes}м {seconds}с"
    return f"{seconds}с"


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


def _path_tree_size(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    total_bytes = 0
    total_files = 0
    stack: list[Path] = [path]
    while stack:
        current = stack.pop()
        try:
            if current.is_symlink():
                continue
            if current.is_file():
                total_bytes += int(current.stat().st_size)
                total_files += 1
                continue
            if current.is_dir():
                for child in current.iterdir():
                    stack.append(child)
        except OSError:
            continue
    return total_bytes, total_files


def _format_size_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(max(0, int(value)))
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024
    return "0 B"


def _storage_usage_summary(storage_root: str) -> dict[str, object]:
    root = Path(storage_root)
    summary: dict[str, object] = {
        "available": False,
        "root_path": str(root),
        "categories": [],
        "total_bytes": 0,
        "total_files": 0,
        "total_human": "0 B",
    }
    if not root.exists() or not root.is_dir():
        summary["error"] = "storage_not_found"
        return summary

    buckets: dict[str, dict[str, object]] = {
        "references": {"title": "Референсы генераций", "bytes": 0, "files": 0},
        "support_media": {"title": "Медиа чатов", "bytes": 0, "files": 0},
        "site_assets": {"title": "Логотипы и ассеты сайта", "bytes": 0, "files": 0},
        "other": {"title": "Прочее", "bytes": 0, "files": 0},
    }

    try:
        entries = list(root.iterdir())
    except OSError:
        summary["error"] = "storage_read_failed"
        return summary

    for entry in entries:
        bucket_key = "other"
        try:
            if entry.name == "_site":
                bucket_key = "site_assets"
            elif entry.name == SUPPORT_MEDIA_DIR:
                bucket_key = "support_media"
            elif entry.is_dir() and not entry.name.startswith("_"):
                bucket_key = "references"
        except OSError:
            bucket_key = "other"

        size_bytes, files_count = _path_tree_size(entry)
        bucket = buckets[bucket_key]
        bucket["bytes"] = int(bucket["bytes"]) + size_bytes
        bucket["files"] = int(bucket["files"]) + files_count

    order = ["references", "support_media", "site_assets", "other"]
    categories: list[dict[str, object]] = []
    total_bytes = 0
    total_files = 0
    for key in order:
        item = buckets[key]
        size_bytes = int(item["bytes"])
        files_count = int(item["files"])
        total_bytes += size_bytes
        total_files += files_count
        categories.append(
            {
                "key": key,
                "title": str(item["title"]),
                "size_bytes": size_bytes,
                "size_human": _format_size_bytes(size_bytes),
                "files_count": files_count,
            }
        )

    summary["available"] = True
    summary["categories"] = categories
    summary["total_bytes"] = total_bytes
    summary["total_files"] = total_files
    summary["total_human"] = _format_size_bytes(total_bytes)
    return summary


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


def _delete_support_thread_media(storage_root: str, thread_id: int) -> None:
    thread_dir = _support_media_root(storage_root) / str(thread_id)
    if thread_dir.exists():
        shutil.rmtree(thread_dir, ignore_errors=True)


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
        role: str | None = None
        admin_ok = secrets.compare_digest(username, settings.admin_web_username) and secrets.compare_digest(
            password, settings.admin_web_password
        )
        if admin_ok:
            role = "admin"
        elif settings.admin_web_subadmin_password:
            subadmin_ok = secrets.compare_digest(username, settings.admin_web_subadmin_username) and secrets.compare_digest(
                password, settings.admin_web_subadmin_password
            )
            if subadmin_ok:
                role = "subadmin"

        if role is None:
            return app.state.templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Неверный логин или пароль."},
            )
        request.session["admin_logged_in"] = True
        request.session["admin_role"] = role
        request.session["admin_login"] = username
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
    async def admin_dashboard(request: Request, error: str | None = None):
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
                "error": (error or "").strip().lower(),
                "can_manage": _can_manage(request),
                "is_subadmin": _is_subadmin(request),
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
                "can_manage": _can_manage(request),
                "is_subadmin": _is_subadmin(request),
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
                payment_kind = "Unknown"
                if (order.payload or "").startswith("stars:"):
                    payment_kind = "Stars"
                if (order.payload or "").startswith("crypto:") or (order.payload or "").startswith("cc:"):
                    payment_kind = "Crypto"
                if (order.payload or "").startswith("cp:"):
                    payment_kind = "Crypto Pay"
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
            active_promo_codes = []
            for promo in promo_rows.scalars().all():
                if promo.active:
                    active_promo_codes.append(promo.code)
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
                "active_promo_codes": active_promo_codes,
                "saved": (saved or "").strip().lower(),
                "error": (error or "").strip().lower(),
                "can_manage": _can_manage(request),
                "is_subadmin": _is_subadmin(request),
            },
        )

    @app.get("/admin/change-requests", response_class=HTMLResponse)
    async def admin_change_requests(
        request: Request,
        status: str = "active",
        user_id: int | None = None,
        saved: str | None = None,
        error: str | None = None,
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        status = (status or "active").strip().lower()
        if status not in {"active", "all"}:
            status = "active"

        is_admin = _can_manage(request)
        admin_login = _session_login(request) or ("admin" if is_admin else "subadmin")

        async with app.state.sessionmaker() as session:
            users_rows = await session.execute(select(User).order_by(User.last_seen_at.desc()).limit(300))
            users = [
                {
                    "id": item.id,
                    "telegram_id": item.telegram_id,
                    "username": item.username or "",
                    "label": f"{item.telegram_id} ({item.username or '-'})",
                }
                for item in users_rows.scalars().all()
            ]

            query = select(AdminChangeRequest, User).join(User, User.id == AdminChangeRequest.target_user_id)
            if not is_admin:
                query = query.where(AdminChangeRequest.created_by_login == admin_login)
            if user_id:
                query = query.where(AdminChangeRequest.target_user_id == user_id)
            if status == "active":
                active_statuses = (
                    [STATUS_PENDING, STATUS_NEEDS_INFO]
                    if is_admin
                    else [STATUS_DRAFT, STATUS_PENDING, STATUS_NEEDS_INFO]
                )
                query = query.where(
                    AdminChangeRequest.status.in_(active_statuses)
                )
            query = query.order_by(AdminChangeRequest.updated_at.desc(), AdminChangeRequest.id.desc())
            rows = await session.execute(query.limit(400))

            items = []
            request_ids: list[int] = []
            for req, user in rows.all():
                request_ids.append(req.id)
                items.append(
                    {
                        "id": req.id,
                        "status": req.status,
                        "status_title": _change_status_title(req.status),
                        "status_badge": _change_status_badge(req.status),
                        "change_type": req.change_type,
                        "change_type_title": _change_type_title(req.change_type),
                        "action_value": _change_action_preview(
                            change_type=req.change_type,
                            credits_amount=req.credits_amount,
                            balance_value=req.balance_value,
                            promo_code=req.promo_code,
                        ),
                        "reason": req.reason,
                        "target_user_id": user.id,
                        "target_telegram_id": user.telegram_id,
                        "target_username": user.username or "",
                        "created_by_login": req.created_by_login,
                        "created_at_msk": _format_msk(req.created_at) or "—",
                        "updated_at_msk": _format_msk(req.updated_at) or "—",
                        "submitted_at_msk": _format_msk(req.submitted_at) or "—",
                        "reviewed_at_msk": _format_msk(req.reviewed_at) or "—",
                        "reviewed_by_login": req.reviewed_by_login or "—",
                        "apply_error": req.apply_error or "",
                        "can_submit": (not is_admin) and req.status in {STATUS_DRAFT, STATUS_NEEDS_INFO},
                        "can_cancel": (not is_admin) and req.status in {STATUS_DRAFT, STATUS_PENDING, STATUS_NEEDS_INFO},
                        "can_comment": req.status in {STATUS_PENDING, STATUS_NEEDS_INFO},
                        "can_approve": is_admin and req.status in {STATUS_PENDING, STATUS_NEEDS_INFO},
                        "can_ask_info": is_admin and req.status == STATUS_PENDING,
                        "can_reject": is_admin and req.status in {STATUS_PENDING, STATUS_NEEDS_INFO},
                    }
                )

            comments_map: dict[int, list[dict]] = {}
            if request_ids:
                comment_rows = await session.execute(
                    select(AdminChangeComment)
                    .where(AdminChangeComment.request_id.in_(request_ids))
                    .order_by(AdminChangeComment.request_id.asc(), AdminChangeComment.created_at.asc())
                )
                for comment in comment_rows.scalars().all():
                    comments_map.setdefault(comment.request_id, []).append(
                        {
                            "author_role": comment.author_role,
                            "author_login": comment.author_login,
                            "message": comment.message,
                            "created_at_msk": _format_msk(comment.created_at) or "—",
                        }
                    )

        for item in items:
            item["comments"] = comments_map.get(item["id"], [])

        return app.state.templates.TemplateResponse(
            "change_requests.html",
            {
                "request": request,
                "title": "Renderis Admin — Предложения",
                "active_tab": "change_requests",
                "can_manage": is_admin,
                "is_subadmin": _is_subadmin(request),
                "is_admin_view": is_admin,
                "items": items,
                "users": users,
                "selected_user_id": user_id or 0,
                "status_filter": status,
                "saved": (saved or "").strip().lower(),
                "error": (error or "").strip().lower(),
            },
        )

    @app.post("/admin/change-requests/create")
    async def admin_change_requests_create(
        request: Request,
        target_user_id: str = Form(""),
        change_type: str = Form(""),
        credits_amount: str = Form(""),
        balance_value: str = Form(""),
        promo_code: str = Form(""),
        reason: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        if _can_manage(request):
            return _forbidden_redirect()

        if not reason.strip():
            return RedirectResponse(url="/admin/change-requests?error=reason_required", status_code=302)

        parsed_user_id = _parse_int(target_user_id.strip())
        parsed_credits = _parse_int(credits_amount.strip()) if credits_amount.strip() else None
        parsed_balance = _parse_int(balance_value.strip()) if balance_value.strip() else None
        login = _session_login(request) or "subadmin"

        async with app.state.sessionmaker() as session:
            if not parsed_user_id:
                return RedirectResponse(url="/admin/change-requests?error=user_not_found", status_code=302)
            user = await session.get(User, parsed_user_id)
            if not user:
                return RedirectResponse(url="/admin/change-requests?error=user_not_found", status_code=302)

            service = ChangeRequestService(session)
            req, err = await service.create_draft(
                change_type=(change_type or "").strip(),
                user=user,
                reason=reason,
                created_by_login=login,
                created_by_role="subadmin",
                credits_amount=parsed_credits,
                balance_value=parsed_balance,
                promo_code=promo_code,
            )
            if err or not req:
                return RedirectResponse(url=f"/admin/change-requests?error={err or 'invalid_data'}", status_code=302)

            await service.add_comment(
                req=req,
                author_role="subadmin",
                author_login=login,
                author_telegram_id=None,
                message=f"Создано предложение. Причина: {reason.strip()}",
            )
            await session.commit()

        return RedirectResponse(url=f"/admin/change-requests?saved=created&user_id={parsed_user_id}", status_code=302)

    @app.post("/admin/change-requests/{request_id}/submit")
    async def admin_change_requests_submit(request: Request, request_id: int):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        if _can_manage(request):
            return _forbidden_redirect()

        login = _session_login(request) or "subadmin"
        notify_item: dict[str, object] | None = None
        async with app.state.sessionmaker() as session:
            req = await session.get(AdminChangeRequest, request_id)
            if not req or req.created_by_login != login:
                return RedirectResponse(url="/admin/change-requests?error=not_found", status_code=302)
            service = ChangeRequestService(session)
            ok, err = await service.submit(req)
            if not ok:
                return RedirectResponse(url=f"/admin/change-requests?error={err or 'submit_failed'}", status_code=302)
            await service.add_comment(
                req=req,
                author_role="subadmin",
                author_login=login,
                author_telegram_id=None,
                message="Отправлено на согласование.",
            )
            target_user = await session.get(User, req.target_user_id)
            if target_user:
                notify_item = _build_change_request_notify_item(req, target_user)
            await session.commit()
        if notify_item:
            await _notify_admins_about_change_request(notify_item)
        return RedirectResponse(url="/admin/change-requests?saved=submitted", status_code=302)

    @app.post("/admin/change-requests/submit-all")
    async def admin_change_requests_submit_all(request: Request):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        if _can_manage(request):
            return _forbidden_redirect()

        login = _session_login(request) or "subadmin"
        notify_items: list[dict[str, object]] = []
        async with app.state.sessionmaker() as session:
            rows = await session.execute(
                select(AdminChangeRequest).where(
                    AdminChangeRequest.created_by_login == login,
                    AdminChangeRequest.status.in_([STATUS_DRAFT, STATUS_NEEDS_INFO]),
                )
            )
            service = ChangeRequestService(session)
            changed = 0
            for req in rows.scalars().all():
                ok, _ = await service.submit(req)
                if ok:
                    changed += 1
                    await service.add_comment(
                        req=req,
                        author_role="subadmin",
                        author_login=login,
                        author_telegram_id=None,
                        message="Отправлено на согласование.",
                    )
                    target_user = await session.get(User, req.target_user_id)
                    if target_user:
                        notify_items.append(_build_change_request_notify_item(req, target_user))
            await session.commit()
        for item in notify_items:
            await _notify_admins_about_change_request(item)
        return RedirectResponse(url=f"/admin/change-requests?saved=submitted_all_{changed}", status_code=302)

    @app.post("/admin/change-requests/{request_id}/cancel")
    async def admin_change_requests_cancel(
        request: Request,
        request_id: int,
        message: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        if _can_manage(request):
            return _forbidden_redirect()

        login = _session_login(request) or "subadmin"
        notify_item: dict[str, object] | None = None
        notify_comment: str | None = None
        notify_context: str | None = None
        async with app.state.sessionmaker() as session:
            req = await session.get(AdminChangeRequest, request_id)
            if not req or req.created_by_login != login:
                return RedirectResponse(url="/admin/change-requests?error=not_found", status_code=302)
            service = ChangeRequestService(session)
            ok, err = await service.cancel(req)
            if not ok:
                return RedirectResponse(url=f"/admin/change-requests?error={err or 'cancel_failed'}", status_code=302)
            comment_message = (message or "").strip() or "Предложение отменено субадмином."
            await service.add_comment(
                req=req,
                author_role="subadmin",
                author_login=login,
                author_telegram_id=None,
                message=comment_message,
            )
            if req.status == STATUS_CANCELLED:
                target_user = await session.get(User, req.target_user_id)
                if target_user:
                    notify_item = _build_change_request_notify_item(req, target_user)
                    notify_comment = comment_message
                    notify_context = await _build_change_request_context(session, req.id)
            await session.commit()
        if notify_item:
            await _notify_admins_about_change_request_update(
                notify_item,
                headline="🛑 Предложение отменено субадмином",
                status_title=_change_status_title(STATUS_CANCELLED),
                comment=notify_comment,
                actor=login,
                context=notify_context,
            )
        return RedirectResponse(url="/admin/change-requests?saved=cancelled", status_code=302)

    @app.post("/admin/change-requests/{request_id}/comment")
    async def admin_change_requests_comment(
        request: Request,
        request_id: int,
        message: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        text = (message or "").strip()
        if not text:
            return RedirectResponse(url="/admin/change-requests?error=comment_required", status_code=302)

        is_admin = _can_manage(request)
        login = _session_login(request) or ("admin" if is_admin else "subadmin")
        notify_admins_item: dict[str, object] | None = None
        notify_admins_headline: str | None = None
        notify_admins_status: str | None = None
        notify_admins_context: str | None = None
        notify_subadmins_item: dict[str, object] | None = None
        notify_subadmins_status: str | None = None
        notify_subadmins_context: str | None = None
        async with app.state.sessionmaker() as session:
            req = await session.get(AdminChangeRequest, request_id)
            if not req:
                return RedirectResponse(url="/admin/change-requests?error=not_found", status_code=302)
            if (not is_admin) and req.created_by_login != login:
                return RedirectResponse(url="/admin/change-requests?error=not_found", status_code=302)
            if req.status not in {STATUS_PENDING, STATUS_NEEDS_INFO}:
                return RedirectResponse(url="/admin/change-requests?error=wrong_status", status_code=302)
            service = ChangeRequestService(session)
            await service.add_comment(
                req=req,
                author_role="admin" if is_admin else "subadmin",
                author_login=login,
                author_telegram_id=None,
                message=text,
            )
            if not is_admin:
                if req.status == STATUS_NEEDS_INFO:
                    ok, _ = await service.submit(req)
                    if ok:
                        notify_admins_headline = "💬 Субадмин ответил и повторно отправил предложение"
                    else:
                        notify_admins_headline = "💬 Субадмин оставил комментарий к предложению"
                else:
                    notify_admins_headline = "💬 Субадмин оставил комментарий к предложению"

                target_user = await session.get(User, req.target_user_id)
                if target_user:
                    notify_admins_item = _build_change_request_notify_item(req, target_user)
                    notify_admins_status = req.status
                    notify_admins_context = await _build_change_request_context(session, req.id)
            if is_admin:
                target_user = await session.get(User, req.target_user_id)
                if target_user:
                    notify_subadmins_item = _build_change_request_notify_item(req, target_user)
                    notify_subadmins_status = req.status
                    notify_subadmins_context = await _build_change_request_context(session, req.id)
            await session.commit()
        if notify_admins_item and notify_admins_headline:
            await _notify_admins_about_change_request_update(
                notify_admins_item,
                headline=notify_admins_headline,
                status_title=_change_status_title(notify_admins_status or STATUS_PENDING),
                comment=text,
                actor=login,
                context=notify_admins_context,
                include_review_actions=True,
            )
        if notify_subadmins_item:
            await _notify_subadmins_about_change_request_update(
                notify_subadmins_item,
                headline="💬 Администратор оставил комментарий к предложению",
                status_title=_change_status_title(notify_subadmins_status or ""),
                comment=text,
                actor=login,
                context=notify_subadmins_context,
            )
        return RedirectResponse(url="/admin/change-requests?saved=comment_added", status_code=302)

    @app.post("/admin/change-requests/{request_id}/needs-info")
    async def admin_change_requests_needs_info(
        request: Request,
        request_id: int,
        message: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        if not _can_manage(request):
            return _forbidden_redirect()

        text = (message or "").strip()
        if not text:
            return RedirectResponse(url="/admin/change-requests?error=comment_required", status_code=302)

        login = _session_login(request) or "admin"
        notify_item: dict[str, object] | None = None
        notify_context: str | None = None
        async with app.state.sessionmaker() as session:
            req = await session.get(AdminChangeRequest, request_id)
            if not req:
                return RedirectResponse(url="/admin/change-requests?error=not_found", status_code=302)
            service = ChangeRequestService(session)
            ok, err = await service.mark_needs_info(req, reviewer_login=login, reviewer_telegram_id=None)
            if not ok:
                return RedirectResponse(url=f"/admin/change-requests?error={err or 'status_error'}", status_code=302)
            await service.add_comment(
                req=req,
                author_role="admin",
                author_login=login,
                author_telegram_id=None,
                message=text,
            )
            target_user = await session.get(User, req.target_user_id)
            if target_user:
                notify_item = _build_change_request_notify_item(req, target_user)
                notify_context = await _build_change_request_context(session, req.id)
            await session.commit()
        if notify_item:
            await _notify_subadmins_about_change_request_update(
                notify_item,
                headline="❓ Требуется уточнение по предложению",
                status_title=_change_status_title(STATUS_NEEDS_INFO),
                comment=text,
                actor=login,
                context=notify_context,
            )
        return RedirectResponse(url="/admin/change-requests?saved=needs_info", status_code=302)

    @app.post("/admin/change-requests/{request_id}/reject")
    async def admin_change_requests_reject(
        request: Request,
        request_id: int,
        message: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        if not _can_manage(request):
            return _forbidden_redirect()

        text = (message or "").strip()
        if not text:
            return RedirectResponse(url="/admin/change-requests?error=comment_required", status_code=302)

        login = _session_login(request) or "admin"
        notify_item: dict[str, object] | None = None
        notify_context: str | None = None
        async with app.state.sessionmaker() as session:
            req = await session.get(AdminChangeRequest, request_id)
            if not req:
                return RedirectResponse(url="/admin/change-requests?error=not_found", status_code=302)
            service = ChangeRequestService(session)
            ok, err = await service.reject(req, reviewer_login=login, reviewer_telegram_id=None)
            if not ok:
                return RedirectResponse(url=f"/admin/change-requests?error={err or 'status_error'}", status_code=302)
            await service.add_comment(
                req=req,
                author_role="admin",
                author_login=login,
                author_telegram_id=None,
                message=text,
            )
            target_user = await session.get(User, req.target_user_id)
            if target_user:
                notify_item = _build_change_request_notify_item(req, target_user)
                notify_context = await _build_change_request_context(session, req.id)
            await session.commit()
        if notify_item:
            await _notify_subadmins_about_change_request_update(
                notify_item,
                headline="⛔ Предложение отклонено администратором",
                status_title=_change_status_title(STATUS_REJECTED),
                comment=text,
                actor=login,
                context=notify_context,
            )
        return RedirectResponse(url="/admin/change-requests?saved=rejected", status_code=302)

    @app.post("/admin/change-requests/{request_id}/approve")
    async def admin_change_requests_approve(request: Request, request_id: int):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        if not _can_manage(request):
            return _forbidden_redirect()

        login = _session_login(request) or "admin"
        notify_item: dict[str, object] | None = None
        notify_context: str | None = None
        async with app.state.sessionmaker() as session:
            req = await session.get(AdminChangeRequest, request_id)
            if not req:
                return RedirectResponse(url="/admin/change-requests?error=not_found", status_code=302)
            service = ChangeRequestService(session)
            ok, err = await service.apply_request(req, reviewer_login=login, reviewer_telegram_id=None)
            if not ok:
                req.apply_error = err
                req.updated_at = datetime.now(timezone.utc)
                await session.commit()
                return RedirectResponse(url=f"/admin/change-requests?error={err or 'apply_failed'}", status_code=302)
            await service.add_comment(
                req=req,
                author_role="admin",
                author_login=login,
                author_telegram_id=None,
                message="Предложение утверждено и применено.",
            )
            target_user = await session.get(User, req.target_user_id)
            if target_user:
                notify_item = _build_change_request_notify_item(req, target_user)
                notify_context = await _build_change_request_context(session, req.id)
            await session.commit()
        if notify_item:
            await _notify_subadmins_about_change_request_update(
                notify_item,
                headline="✅ Предложение утверждено и применено",
                status_title=_change_status_title(STATUS_APPLIED),
                actor=login,
                context=notify_context,
            )
        return RedirectResponse(url="/admin/change-requests?saved=approved", status_code=302)

    @app.post("/admin/users/{user_id}/ban")
    async def admin_user_ban(request: Request, user_id: int):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        if not _can_manage(request):
            return _forbidden_redirect()
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
        if not _can_manage(request):
            return _forbidden_redirect()
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
        if not _can_manage(request):
            return _forbidden_redirect()
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
        if not _can_manage(request):
            return _forbidden_redirect()
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
        if not _can_manage(request):
            return _forbidden_redirect()
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
        if not _can_manage(request):
            return _forbidden_redirect()
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
    async def admin_products(request: Request, saved: int | None = None, avg_reset: int | None = None):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        async with app.state.sessionmaker() as session:
            settings_service = AppSettingsService(session)
            stars_per_credit = await settings_service.get_float("stars_per_credit", 2.0)
            usd_per_star = await settings_service.get_float("usd_per_star", 0.013)
            usd_per_credit = stars_per_credit * usd_per_star
            kie_usd_per_credit = await settings_service.get_float("kie_usd_per_credit", 0.02)
            avg_since: datetime | None = None
            avg_reset_raw = (await settings_service.get("products_avg_started_at", "")) or ""
            if avg_reset_raw.strip():
                try:
                    avg_reset_epoch = int(avg_reset_raw.strip())
                    if avg_reset_epoch > 0:
                        avg_since = datetime.fromtimestamp(avg_reset_epoch, tz=timezone.utc)
                except ValueError:
                    avg_since = None

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

            task_finished_subq = (
                select(
                    GenerationTask.generation_id.label("generation_id"),
                    func.max(GenerationTask.finished_at).label("finished_at"),
                )
                .where(GenerationTask.finished_at.is_not(None))
                .group_by(GenerationTask.generation_id)
                .subquery()
            )

            ref_mode_expr = func.coalesce(Generation.options["reference_images"].astext, "none")
            resolution_expr = func.coalesce(Generation.options["resolution"].astext, "1K")
            row_id_expr = case(
                (Generation.model == "nano_banana", "nano_banana"),
                (Generation.model == "nano_banana_edit", "nano_banana_edit"),
                (
                    (Generation.model == "nano_banana_pro")
                    & (ref_mode_expr == "none")
                    & (resolution_expr == "1K"),
                    "pro_no_refs_1k",
                ),
                (
                    (Generation.model == "nano_banana_pro")
                    & (ref_mode_expr == "none")
                    & (resolution_expr == "2K"),
                    "pro_no_refs_2k",
                ),
                (
                    (Generation.model == "nano_banana_pro")
                    & (ref_mode_expr == "none")
                    & (resolution_expr == "4K"),
                    "pro_no_refs_4k",
                ),
                (
                    (Generation.model == "nano_banana_pro")
                    & (ref_mode_expr == "has")
                    & (resolution_expr == "1K"),
                    "pro_refs_1k",
                ),
                (
                    (Generation.model == "nano_banana_pro")
                    & (ref_mode_expr == "has")
                    & (resolution_expr == "2K"),
                    "pro_refs_2k",
                ),
                (
                    (Generation.model == "nano_banana_pro")
                    & (ref_mode_expr == "has")
                    & (resolution_expr == "4K"),
                    "pro_refs_4k",
                ),
                else_=None,
            ).label("row_id")
            duration_seconds_expr = func.extract("epoch", task_finished_subq.c.finished_at - Generation.created_at)
            avg_query = (
                select(
                    row_id_expr,
                    func.avg(duration_seconds_expr).label("avg_seconds"),
                    func.count(Generation.id).label("samples"),
                )
                .join(task_finished_subq, task_finished_subq.c.generation_id == Generation.id)
                .where(Generation.status == "success")
                .where(row_id_expr.is_not(None))
            )
            if avg_since is not None:
                avg_query = avg_query.where(Generation.created_at >= avg_since)
            avg_query = avg_query.group_by(row_id_expr)
            averages_rows = await session.execute(avg_query)
            row_avg_map: dict[str, dict[str, float | int | None]] = {}
            for avg_row in averages_rows:
                row_id = avg_row.row_id
                if not row_id:
                    continue
                row_avg_map[row_id] = {
                    "avg_seconds": float(avg_row.avg_seconds) if avg_row.avg_seconds is not None else None,
                    "samples": int(avg_row.samples or 0),
                }

            for row in rows:
                kie_usd = round(row["kie_credits"] * kie_usd_per_credit, 4)
                renderis_usd = round(row["renderis_credits"] * usd_per_credit, 4)
                profit_pct = ""
                if kie_usd > 0:
                    profit_pct = round((renderis_usd / kie_usd) * 100, 1)
                avg_meta = row_avg_map.get(row["row_id"], {})
                avg_seconds = avg_meta.get("avg_seconds")
                avg_samples = int(avg_meta.get("samples") or 0)
                avg_duration = _format_duration_seconds(float(avg_seconds) if avg_seconds is not None else None)
                if avg_samples > 0 and avg_seconds is not None:
                    avg_duration = f"{avg_duration} ({avg_samples})"
                products.append(
                    {
                        **row,
                        "kie_usd": kie_usd,
                        "renderis_usd": renderis_usd,
                        "profit_pct": profit_pct,
                        "avg_duration": avg_duration,
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
                "avg_reset": bool(avg_reset),
                "avg_since_msk": _format_msk(avg_since),
                "can_manage": _can_manage(request),
                "is_subadmin": _is_subadmin(request),
            },
        )

    @app.post("/admin/products/avg-reset")
    async def admin_products_avg_reset(request: Request):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        if not _can_manage(request):
            return _forbidden_redirect()

        async with app.state.sessionmaker() as session:
            settings_service = AppSettingsService(session)
            await settings_service.set("products_avg_started_at", str(int(time.time())))
            await session.commit()

        return RedirectResponse(url="/admin/products?saved=1&avg_reset=1", status_code=302)

    @app.post("/admin/products/{row_id}")
    async def admin_products_update(
        request: Request,
        row_id: str,
        provider_credits: str = Form(""),
        renderis_credits: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        if not _can_manage(request):
            return _forbidden_redirect()

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
        if not _can_manage(request):
            return _forbidden_redirect()

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
        if not _can_manage(request):
            return _forbidden_redirect()

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
    async def admin_settings(
        request: Request,
        saved: int | None = None,
        error: str | None = None,
        brain_error: int | None = None,
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)

        storage_usage = _storage_usage_summary(settings.reference_storage_path)
        brain_ctx = {
            "ai_brain_enabled": False,
            "ai_brain_model": "gpt-4o-mini",
            "ai_brain_temperature": 0.7,
            "ai_brain_max_tokens": 600,
            "ai_brain_price_per_improve": 1,
            "ai_brain_daily_limit_per_user": 20,
            "ai_brain_pack_price_credits": 3,
            "ai_brain_pack_size_improvements": 10,
            "ai_brain_system_prompt": (
                "You are a professional AI prompt engineer. Improve the user's prompt to be more detailed, "
                "cinematic, structured, and optimized for image generation models. Do not add unrelated "
                "concepts. Keep original intent."
            ),
        }
        brain_runtime_error = bool(brain_error)

        async with app.state.sessionmaker() as session:
            settings_service = AppSettingsService(session)
            stars_per_credit = await settings_service.get_float("stars_per_credit", 2.0)
            usd_per_star = await settings_service.get_float("usd_per_star", 0.013)
            signup_bonus_credits = await settings_service.get_int("signup_bonus_credits", settings.signup_bonus_credits)
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
            try:
                brain_service = AIBrainService(
                    session,
                    openai_api_key=settings.openai_api_key,
                    openai_base_url=settings.openai_base_url,
                )
                brain_cfg = await brain_service.get_config()
                brain_ctx.update(
                    {
                        "ai_brain_enabled": bool(brain_cfg.enabled),
                        "ai_brain_model": brain_cfg.openai_model,
                        "ai_brain_temperature": float(brain_cfg.temperature or 0.7),
                        "ai_brain_max_tokens": int(brain_cfg.max_tokens or 600),
                        "ai_brain_price_per_improve": int(brain_cfg.price_per_improve or 1),
                        "ai_brain_daily_limit_per_user": int(brain_cfg.daily_limit_per_user or 20),
                        "ai_brain_pack_price_credits": int(brain_cfg.pack_price_credits or 3),
                        "ai_brain_pack_size_improvements": int(brain_cfg.pack_size_improvements or 10),
                        "ai_brain_system_prompt": brain_cfg.system_prompt,
                    }
                )
            except Exception:
                logger.exception("admin_settings_ai_brain_unavailable")
                brain_runtime_error = True

        return app.state.templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "title": "Renderis Admin — Настройки",
                "active_tab": "settings",
                "stars_per_credit": stars_per_credit,
                "usd_per_star": usd_per_star,
                "signup_bonus_credits": signup_bonus_credits,
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
                "openai_key_configured": bool(settings.openai_api_key.strip()),
                "openai_base_url": settings.openai_base_url,
                "ai_brain_enabled": brain_ctx["ai_brain_enabled"],
                "ai_brain_model": brain_ctx["ai_brain_model"],
                "ai_brain_temperature": brain_ctx["ai_brain_temperature"],
                "ai_brain_max_tokens": brain_ctx["ai_brain_max_tokens"],
                "ai_brain_price_per_improve": brain_ctx["ai_brain_price_per_improve"],
                "ai_brain_daily_limit_per_user": brain_ctx["ai_brain_daily_limit_per_user"],
                "ai_brain_pack_price_credits": brain_ctx["ai_brain_pack_price_credits"],
                "ai_brain_pack_size_improvements": brain_ctx["ai_brain_pack_size_improvements"],
                "ai_brain_system_prompt": brain_ctx["ai_brain_system_prompt"],
                "brain_runtime_error": brain_runtime_error,
                "storage_usage": storage_usage,
                "saved": bool(saved),
                "error": (error or "").strip(),
                "can_manage": _can_manage(request),
                "is_subadmin": _is_subadmin(request),
            },
        )

    @app.post("/admin/settings")
    async def admin_settings_update(
        request: Request,
        stars_per_credit: str = Form(""),
        usd_per_star: str = Form(""),
        signup_bonus_credits: str = Form(""),
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
        ai_brain_enabled: str = Form(""),
        ai_brain_model: str = Form(""),
        ai_brain_temperature: str = Form(""),
        ai_brain_max_tokens: str = Form(""),
        ai_brain_price_per_improve: str = Form(""),
        ai_brain_daily_limit_per_user: str = Form(""),
        ai_brain_pack_price_credits: str = Form(""),
        ai_brain_pack_size_improvements: str = Form(""),
        ai_brain_system_prompt: str = Form(""),
    ):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        if not _can_manage(request):
            return _forbidden_redirect()

        async with app.state.sessionmaker() as session:
            settings_service = AppSettingsService(session)
            kie_balance_service = KieBalanceService(session)
            brain_service = AIBrainService(
                session,
                openai_api_key=settings.openai_api_key,
                openai_base_url=settings.openai_base_url,
            )
            errors: list[str] = []
            if stars_per_credit.strip():
                parsed = _parse_float(stars_per_credit.strip())
                if parsed is not None:
                    await settings_service.set("stars_per_credit", str(parsed))
            if usd_per_star.strip():
                parsed = _parse_float(usd_per_star.strip())
                if parsed is not None:
                    await settings_service.set("usd_per_star", str(parsed))
            if signup_bonus_credits.strip():
                parsed = _parse_int(signup_bonus_credits.strip())
                if parsed is not None:
                    await settings_service.set("signup_bonus_credits", str(max(0, parsed)))
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

            brain_update_failed = False
            try:
                current_brain_cfg = await brain_service.get_config()
                parsed_ai_brain_temperature = _parse_float(ai_brain_temperature.strip())
                if parsed_ai_brain_temperature is None:
                    parsed_ai_brain_temperature = float(current_brain_cfg.temperature or 0.7)
                parsed_ai_brain_max_tokens = _parse_int(ai_brain_max_tokens.strip())
                if parsed_ai_brain_max_tokens is None:
                    parsed_ai_brain_max_tokens = int(current_brain_cfg.max_tokens or 600)
                parsed_ai_brain_price_per_improve = _parse_int(ai_brain_price_per_improve.strip())
                if parsed_ai_brain_price_per_improve is None:
                    parsed_ai_brain_price_per_improve = int(current_brain_cfg.price_per_improve or 1)
                parsed_ai_brain_daily_limit = _parse_int(ai_brain_daily_limit_per_user.strip())
                if parsed_ai_brain_daily_limit is None:
                    parsed_ai_brain_daily_limit = int(current_brain_cfg.daily_limit_per_user or 20)
                parsed_ai_brain_pack_price = _parse_int(ai_brain_pack_price_credits.strip())
                if parsed_ai_brain_pack_price is None:
                    parsed_ai_brain_pack_price = int(current_brain_cfg.pack_price_credits or 3)
                parsed_ai_brain_pack_size = _parse_int(ai_brain_pack_size_improvements.strip())
                if parsed_ai_brain_pack_size is None:
                    parsed_ai_brain_pack_size = int(current_brain_cfg.pack_size_improvements or 10)

                await brain_service.update_config(
                    enabled=ai_brain_enabled.strip() == "1",
                    openai_model=(ai_brain_model or "").strip() or current_brain_cfg.openai_model,
                    temperature=float(parsed_ai_brain_temperature),
                    max_tokens=int(parsed_ai_brain_max_tokens),
                    price_per_improve=int(parsed_ai_brain_price_per_improve),
                    daily_limit_per_user=int(parsed_ai_brain_daily_limit),
                    pack_price_credits=int(parsed_ai_brain_pack_price),
                    pack_size_improvements=int(parsed_ai_brain_pack_size),
                    system_prompt=(ai_brain_system_prompt or "").strip() or current_brain_cfg.system_prompt,
                )
            except Exception:
                logger.exception("admin_settings_ai_brain_update_failed")
                brain_update_failed = True
            await session.commit()
        if errors:
            return RedirectResponse(url=f"/admin/settings?error={'|'.join(errors)}", status_code=302)
        if brain_update_failed:
            return RedirectResponse(url="/admin/settings?saved=1&brain_error=1", status_code=302)
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
                "can_manage": _can_manage(request),
                "is_subadmin": _is_subadmin(request),
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

    @app.get("/admin/api/chats/{thread_id}/summary")
    async def admin_chats_summary(request: Request, thread_id: int):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        async with app.state.sessionmaker() as session:
            thread = await session.get(SupportThread, thread_id)
            if not thread:
                return JSONResponse({"error": "not_found"}, status_code=404)
            user = await session.get(User, thread.user_id)
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)

            generations_total_q = await session.execute(select(func.count(Generation.id)).where(Generation.user_id == user.id))
            generations_success_q = await session.execute(
                select(func.count(Generation.id)).where(Generation.user_id == user.id, Generation.status == "success")
            )
            generations_fail_q = await session.execute(
                select(func.count(Generation.id)).where(Generation.user_id == user.id, Generation.status == "fail")
            )
            last_generation_at_q = await session.execute(
                select(func.max(Generation.created_at)).where(Generation.user_id == user.id)
            )
            paid_orders_count_q = await session.execute(
                select(func.count(Order.id)).where(Order.user_id == user.id, Order.status == "paid")
            )
            paid_credits_sum_q = await session.execute(
                select(func.coalesce(func.sum(Order.credits_amount), 0)).where(Order.user_id == user.id, Order.status == "paid")
            )
            last_paid_order_at_q = await session.execute(
                select(func.max(Order.created_at)).where(Order.user_id == user.id, Order.status == "paid")
            )
            active_promos_q = await session.execute(
                select(func.count(PromoCode.code)).where(PromoCode.redeemed_by_user_id == user.id, PromoCode.active.is_(True))
            )
            thread_messages_q = await session.execute(
                select(func.count(SupportMessage.id)).where(SupportMessage.thread_id == thread.id)
            )

            return {
                "user_id": user.id,
                "telegram_id": user.telegram_id,
                "username": user.username or "",
                "balance_credits": int(user.balance_credits or 0),
                "is_banned": bool(user.is_banned),
                "referral_discount_pct": int(user.referral_discount_pct or 0),
                "first_seen_at_msk": _format_msk(user.first_seen_at) or "—",
                "last_seen_at_msk": _format_msk(user.last_seen_at) or "—",
                "generations_total": int(generations_total_q.scalar_one() or 0),
                "generations_success": int(generations_success_q.scalar_one() or 0),
                "generations_fail": int(generations_fail_q.scalar_one() or 0),
                "last_generation_at_msk": _format_msk(last_generation_at_q.scalar_one()),
                "paid_orders_count": int(paid_orders_count_q.scalar_one() or 0),
                "paid_credits_total": int(paid_credits_sum_q.scalar_one() or 0),
                "last_paid_order_at_msk": _format_msk(last_paid_order_at_q.scalar_one()),
                "active_promos_count": int(active_promos_q.scalar_one() or 0),
                "thread_messages_count": int(thread_messages_q.scalar_one() or 0),
                "thread_last_message_at_msk": _format_msk(thread.last_message_at) or "—",
            }

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

    @app.delete("/admin/api/chats/{thread_id}")
    async def admin_chats_delete(request: Request, thread_id: int):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not _can_manage(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)

        settings = get_settings()
        async with app.state.sessionmaker() as session:
            thread = await session.get(SupportThread, thread_id)
            if not thread:
                return JSONResponse({"error": "not_found"}, status_code=404)
            await session.execute(delete(SupportMessage).where(SupportMessage.thread_id == thread_id))
            await session.delete(thread)
            await session.commit()

        _delete_support_thread_media(settings.reference_storage_path, thread_id)
        return {"ok": True}

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
                "can_manage": _can_manage(request),
                "is_subadmin": _is_subadmin(request),
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
        if not _can_manage(request):
            return _forbidden_redirect()

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
                "can_manage": _can_manage(request),
                "is_subadmin": _is_subadmin(request),
            },
        )

    @app.post("/admin/promos/code/{code}/deactivate")
    async def admin_promos_deactivate(request: Request, code: str):
        if not _is_logged_in(request):
            return RedirectResponse(url="/login", status_code=302)
        if not _can_manage(request):
            return _forbidden_redirect()

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

