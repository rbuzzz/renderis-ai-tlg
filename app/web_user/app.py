from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from urllib.parse import parse_qsl, unquote, unquote_plus, urlparse
from pathlib import Path
from typing import Any, Dict, List

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Generation, GenerationTask, Order, PromoCode, StarProduct, User
from app.db.session import create_sessionmaker
from app.i18n import SUPPORTED_LANGS, normalize_lang, t, tf
from app.modelspecs.registry import get_model, list_models
from app.services.credits import CreditsService
from app.services.cryptopay import CryptoPayClient, CryptoPayError
from app.services.cryptocloud import CryptoCloudClient, CryptoCloudError
from app.services.pricing import PricingService
from app.services.generation import GenerationService
from app.services.kie_client import KieClient, KieError
from app.services.payments import PaymentsService
from app.services.poller import PollManager
from app.services.promos import PromoService
from app.services.app_settings import AppSettingsService
from app.services.brain import AIBrainService, BrainProviderError
from app.services.product_pricing import get_product_credits, get_product_stars_price, get_product_usd_price
from app.services.rate_limit import RateLimiter
from app.utils.text import clamp_text
from app.utils.time import utcnow


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico"}
LANGUAGE_LABELS: Dict[str, str] = {
    "en": "English",
    "es": "Spanish",
    "ru": "Russian",
}
CRYPTO_SUCCESS_STATUSES = {"paid", "overpaid"}
logger = logging.getLogger(__name__)


def _is_cryptocloud_enabled(settings) -> bool:
    return bool(settings.cryptocloud_api_key.strip() and settings.cryptocloud_shop_id.strip())


def _is_cryptopay_enabled(settings) -> bool:
    return bool(settings.cryptopay_api_token.strip())


def _crypto_locale(lang: str) -> str:
    return "ru" if normalize_lang(lang) == "ru" else "en"


def _normalize_invoice_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _invoice_status_from_info(info: dict[str, Any] | None) -> str:
    if not info:
        return ""
    return _normalize_invoice_status(info.get("status") or info.get("invoice_status") or info.get("status_invoice"))


def _cryptopay_payload_order_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("cp_"):
        return raw

    normalized = raw.replace("&", ";")
    for chunk in normalized.split(";"):
        piece = chunk.strip()
        if not piece or "=" not in piece:
            continue
        key, data = piece.split("=", 1)
        if key.strip().lower() == "order":
            candidate = data.strip()
            if candidate.startswith("cp_"):
                return candidate
    return ""


async def _cryptocloud_pricing(session) -> tuple[float, float]:
    settings_service = AppSettingsService(session)
    stars_per_credit = await settings_service.get_float("stars_per_credit", 2.0)
    usd_per_star = await settings_service.get_float("usd_per_star", 0.013)
    return stars_per_credit, usd_per_star


def _is_logged_in(request: Request) -> bool:
    return bool(request.session.get("user_id"))


def _get_lang(request: Request) -> str:
    session_lang = request.session.get("lang")
    if session_lang:
        return normalize_lang(session_lang)
    return "en"


def _safe_next_url(value: str | None) -> str:
    path = (value or "/").strip()
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path


def _verify_telegram_auth(data: Dict[str, Any], token: str) -> bool:
    received_hash = data.get("hash", "")
    if not received_hash:
        return False
    auth_date = int(data.get("auth_date") or 0)
    if auth_date and int(time.time()) - auth_date > 86400:
        return False
    data_check = {k: v for k, v in data.items() if k != "hash"}
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data_check.items()))
    secret_key = hashlib.sha256(token.encode()).digest()
    calculated = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(calculated, received_hash)


def _verify_telegram_webapp_init_data(init_data: str, token: str) -> tuple[bool, Dict[str, Any], str]:
    raw = (init_data or "").strip()
    if not raw:
        return False, {}, "empty_init_data"
    token_clean = (token or "").strip()
    if not token_clean:
        return False, {}, "empty_token"

    try:
        pairs = parse_qsl(raw, keep_blank_values=True)
    except Exception:
        return False, {}, "parse_failed"
    if not pairs:
        return False, {}, "parse_empty"

    data_check: Dict[str, str] = {}
    received_hash = ""
    for key, value in pairs:
        if key == "hash":
            received_hash = value
            continue
        data_check[key] = value

    if not received_hash:
        return False, {}, "hash_missing"
    received_hash = received_hash.strip().lower()

    auth_date_raw = data_check.get("auth_date", "")
    try:
        auth_date = int(auth_date_raw) if auth_date_raw else 0
    except (TypeError, ValueError):
        return False, {}, "auth_date_invalid"
    if auth_date and int(time.time()) - auth_date > 86400:
        return False, {}, "auth_date_expired"

    # Primary candidate: URL-decoded values (recommended by Telegram docs).
    check_maps: list[Dict[str, str]] = [dict(data_check)]
    # Fallback candidates for client/server encoding edge-cases.
    raw_map_plus: Dict[str, str] = {}
    raw_map_plain: Dict[str, str] = {}
    for chunk in raw.split("&"):
        if not chunk:
            continue
        key_raw, _, val_raw = chunk.partition("=")
        key_dec = unquote_plus(key_raw)
        if key_dec == "hash":
            continue
        raw_map_plus[key_dec] = unquote_plus(val_raw)
        raw_map_plain[key_dec] = unquote(val_raw)
    if raw_map_plus:
        check_maps.append(raw_map_plus)
    if raw_map_plain:
        check_maps.append(raw_map_plain)

    secrets = [
        hmac.new(b"WebAppData", token_clean.encode(), hashlib.sha256).digest(),
        hashlib.sha256(token_clean.encode()).digest(),
    ]

    valid = False
    for secret in secrets:
        for data_candidate in check_maps:
            check_string = "\n".join(f"{k}={v}" for k, v in sorted(data_candidate.items()))
            calculated = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest().lower()
            if hmac.compare_digest(calculated, received_hash):
                valid = True
                break
        if valid:
            break
    if not valid:
        return False, {}, "hash_mismatch"

    payload: Dict[str, Any] = dict(data_check)
    user_raw = payload.get("user", "")
    if user_raw:
        try:
            payload["user"] = json.loads(user_raw)
        except json.JSONDecodeError:
            return False, {}, "user_json_invalid"

    return True, payload, ""


def _option_label(lang: str, key: str) -> str:
    mapping = {
        "output_format": t(lang, "output_format"),
        "image_size": t(lang, "aspect_ratio"),
        "aspect_ratio": t(lang, "aspect_ratio"),
        "resolution": t(lang, "resolution"),
        "outputs": t(lang, "outputs"),
    }
    return mapping.get(key, key)


def _ratio_label(lang: str, value: str) -> str:
    key = value.replace(":", "_")
    return t(lang, f"ratio_{key}")


def _resolution_label(lang: str, value: str) -> str:
    key = value.lower()
    return t(lang, f"res_{key}")


def _value_label(lang: str, opt_key: str, value: str) -> str:
    if opt_key in ("image_size", "aspect_ratio"):
        return _ratio_label(lang, value)
    if opt_key == "resolution":
        return _resolution_label(lang, value)
    if opt_key == "output_format":
        return value.upper()
    return value


def _model_tagline(lang: str, model_key: str, fallback: str) -> str:
    key = f"model_tagline_{model_key}"
    value = t(lang, key)
    if value == key:
        return fallback
    return value


def _is_allowed_download(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    allowed_hosts = {
        "tempfile.aiquickdraw.com",
        "file.aiquickdraw.com",
        "static.aiquickdraw.com",
        "aiquickdraw.com",
    }
    return host in allowed_hosts or host.endswith(".aiquickdraw.com")


def _filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = os.path.basename(parsed.path) or "renderis-result"
    return name


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


def _find_site_logo_file(storage_root: str) -> Path | None:
    return _find_asset_file(storage_root, ["site_logo", "logo"])


def _find_favicon_logo_file(storage_root: str) -> Path | None:
    return _find_asset_file(storage_root, ["favicon"])


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


def _base_public_url(url: str) -> str:
    base = (url or "").rstrip("/")
    if base.endswith("/admin/chats"):
        return base[: -len("/admin/chats")]
    if base.endswith("/admin"):
        return base[: -len("/admin")]
    return base


async def _proxy_admin_asset(settings, path: str, is_head: bool) -> Response | None:
    if not settings.admin_web_public_url:
        return None
    base = _base_public_url(settings.admin_web_public_url)
    if not base:
        return None
    url = f"{base}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.head(url) if is_head else await client.get(url)
    except Exception:
        return None
    if resp.status_code != 200:
        return None

    content_type = resp.headers.get("content-type", "application/octet-stream")
    cache_control = resp.headers.get("cache-control")
    headers = {}
    if cache_control:
        headers["Cache-Control"] = cache_control
    if is_head:
        return Response(status_code=200, media_type=content_type, headers=headers)
    return Response(content=resp.content, status_code=200, media_type=content_type, headers=headers)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Renderis User")
    app.add_middleware(SessionMiddleware, secret_key=settings.user_web_secret)
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.sessionmaker = create_sessionmaker()
    app.state.user_web_poller = None
    app.state.user_web_poller_task = None
    app.state.brain_rate_limiter = RateLimiter(2)

    @app.on_event("startup")
    async def startup() -> None:
        bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        kie_client = KieClient()
        poller = PollManager(bot, app.state.sessionmaker, kie_client)
        app.state.user_web_poller = poller
        if settings.user_web_poll_enabled:
            await poller.restore_pending()
            app.state.user_web_poller_task = asyncio.create_task(poller.watch_pending())

    @app.on_event("shutdown")
    async def shutdown() -> None:
        task = app.state.user_web_poller_task
        if task:
            task.cancel()
        poller = app.state.user_web_poller
        if poller:
            try:
                await poller.kie.close()
            except Exception:
                pass
            try:
                await poller.bot.session.close()
            except Exception:
                pass

    async def apply_cryptocloud_settlement(
        session,
        order: Order,
        invoice_info: dict[str, Any] | None,
    ) -> tuple[str, bool, int]:
        invoice_status = _invoice_status_from_info(invoice_info)
        credits_added = 0
        is_paid = order.status == "paid"

        if invoice_status in CRYPTO_SUCCESS_STATUSES and not is_paid:
            user = await session.get(User, order.user_id)
            if user:
                credits = CreditsService(session)
                await credits.add_ledger(
                    user,
                    order.credits_amount,
                    "purchase",
                    meta={
                        "provider": "cryptocloud",
                        "invoice_uuid": order.provider_payment_charge_id,
                        "payload": order.payload,
                    },
                    idempotency_key=f"crypto_invoice:{order.provider_payment_charge_id}",
                )
                credits_added = order.credits_amount
            order.status = "paid"
            is_paid = True
        elif invoice_status and not is_paid:
            order.status = f"cc_{invoice_status}"[:32]

        return invoice_status, is_paid, credits_added

    async def apply_cryptopay_settlement(
        session,
        order: Order,
        invoice_info: dict[str, Any] | None,
    ) -> tuple[str, bool, int]:
        invoice_status = _normalize_invoice_status((invoice_info or {}).get("status"))
        payments = PaymentsService(session)

        if invoice_status == "paid":
            paid_now, credits_added = await payments.settle_cryptopay_order(order)
            return invoice_status, order.status == "paid", credits_added if paid_now else 0

        if invoice_status and order.status != "paid":
            order.status = f"cp_{invoice_status}"[:32]
        return invoice_status, order.status == "paid", 0

    async def notify_payment_success(telegram_id: int, lang: str, credits_added: int) -> None:
        if credits_added <= 0:
            return
        bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            await bot.send_message(
                chat_id=telegram_id,
                text=tf(lang, "cryptopay_paid_notify", credits=credits_added),
            )
        except Exception:
            pass
        finally:
            await bot.session.close()

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        lang = _get_lang(request)
        site_logo_url = ""
        logo_version = ""
        async with app.state.sessionmaker() as session:
            settings_service = AppSettingsService(session)
            raw_logo_url = ((await settings_service.get("site_logo_url")) or "").strip()
            logo_version = ((await settings_service.get("site_logo_version")) or "").strip()
            if raw_logo_url:
                if logo_version:
                    sep = "&" if "?" in raw_logo_url else "?"
                    site_logo_url = f"{raw_logo_url}{sep}v={logo_version}"
                else:
                    site_logo_url = raw_logo_url
        if not site_logo_url:
            logo_path = _find_site_logo_file(settings.reference_storage_path)
            if logo_path:
                site_logo_url = f"/assets/site-logo?v={int(logo_path.stat().st_mtime_ns)}"
            elif settings.admin_web_public_url:
                if logo_version:
                    site_logo_url = f"/assets/site-logo?v={logo_version}"
                else:
                    site_logo_url = "/assets/site-logo"
        lang_options = [{"code": code, "label": LANGUAGE_LABELS.get(code, code.upper())} for code in SUPPORTED_LANGS]
        current_lang_label = LANGUAGE_LABELS.get(lang, lang.upper())
        return app.state.templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "title": t(lang, "site_title"),
                "lang": lang,
                "labels": {key: t(lang, key) for key in app.i18n_keys},
                "logged_in": _is_logged_in(request),
                "bot_username": settings.bot_username,
                "site_logo_url": site_logo_url,
                "lang_options": lang_options,
                "current_lang_label": current_lang_label,
                "max_prompt_length": settings.max_prompt_length,
                "miniapp_analytics_token": settings.miniapp_analytics_token,
                "miniapp_analytics_app_name": settings.miniapp_analytics_app_name,
            },
        )

    @app.get("/set-lang")
    async def set_lang(request: Request):
        lang = normalize_lang(request.query_params.get("lang"))
        request.session["lang"] = lang
        if _is_logged_in(request):
            async with app.state.sessionmaker() as session:
                credits = CreditsService(session)
                user = await credits.get_user(int(request.session["user_id"]))
                if user:
                    settings_payload = dict(user.settings or {})
                    settings_payload["lang"] = lang
                    user.settings = settings_payload
                    await session.commit()

        next_url = _safe_next_url(request.query_params.get("next"))
        return RedirectResponse(url=next_url, status_code=302)

    @app.api_route("/assets/site-logo", methods=["GET", "HEAD"])
    async def user_site_logo(request: Request):
        logo_path = _find_site_logo_file(settings.reference_storage_path)
        if logo_path:
            return FileResponse(path=str(logo_path))
        proxied = await _proxy_admin_asset(settings, "/assets/site-logo", request.method == "HEAD")
        if proxied is not None:
            return proxied
        return JSONResponse({"error": "not_found"}, status_code=404)

    @app.api_route("/assets/favicon-logo", methods=["GET", "HEAD"])
    async def user_favicon_logo(request: Request):
        logo_path = _find_favicon_logo_file(settings.reference_storage_path)
        if logo_path:
            return FileResponse(path=str(logo_path))
        proxied = await _proxy_admin_asset(settings, "/assets/favicon-logo", request.method == "HEAD")
        if proxied is not None:
            return proxied
        return JSONResponse({"error": "not_found"}, status_code=404)

    @app.api_route("/favicon.svg", methods=["GET", "HEAD"])
    async def user_favicon_svg(request: Request):
        is_head = request.method == "HEAD"
        svg = _rounded_favicon_svg(settings.reference_storage_path)
        if svg:
            if is_head:
                return Response(status_code=200, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})
            return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})
        proxied = await _proxy_admin_asset(settings, "/favicon.svg", is_head)
        if proxied is not None:
            return proxied
        return Response(status_code=404)

    @app.api_route("/favicon.ico", methods=["GET", "HEAD"])
    async def user_favicon():
        return await user_favicon_svg()

    @app.post("/auth/telegram")
    async def auth_telegram(request: Request):
        data = await request.json()
        if not _verify_telegram_auth(data, settings.bot_token):
            return JSONResponse({"error": "invalid"}, status_code=400)

        lang = normalize_lang(data.get("language_code"))
        first_name = (data.get("first_name") or "").strip()
        last_name = (data.get("last_name") or "").strip()
        photo_url = (data.get("photo_url") or "").strip()
        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            settings_service = AppSettingsService(session)
            signup_bonus_credits = await settings_service.get_int("signup_bonus_credits", settings.signup_bonus_credits)
            is_admin = int(data["id"]) in settings.admin_ids()
            user = await credits.ensure_user(int(data["id"]), data.get("username"), is_admin)
            settings_payload = dict(user.settings or {})
            settings_payload["lang"] = lang
            if first_name:
                settings_payload["first_name"] = first_name
            if last_name:
                settings_payload["last_name"] = last_name
            if photo_url:
                settings_payload["photo_url"] = photo_url
            user.settings = settings_payload
            await credits.apply_signup_bonus(user, signup_bonus_credits)
            await session.commit()

        request.session["user_id"] = int(data["id"])
        request.session["lang"] = lang
        request.session["username"] = data.get("username")
        return {"ok": True}

    @app.post("/auth/miniapp")
    async def auth_miniapp(request: Request):
        payload = await request.json()
        init_data = str(payload.get("init_data") or "").strip()
        if not init_data:
            return JSONResponse({"error": "init_data_required"}, status_code=400)

        valid, parsed, reason = _verify_telegram_webapp_init_data(init_data, settings.bot_token)
        if not valid:
            logger.warning("miniapp_auth_invalid", extra={"reason": reason})
            return JSONResponse({"error": "invalid", "reason": reason}, status_code=400)

        user_data = parsed.get("user")
        if not isinstance(user_data, dict):
            return JSONResponse({"error": "user_required"}, status_code=400)

        try:
            telegram_id = int(user_data.get("id") or 0)
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid_user_id"}, status_code=400)
        if telegram_id <= 0:
            return JSONResponse({"error": "invalid_user_id"}, status_code=400)

        lang = normalize_lang(user_data.get("language_code") or parsed.get("language_code"))
        first_name = str(user_data.get("first_name") or "").strip()
        last_name = str(user_data.get("last_name") or "").strip()
        photo_url = str(user_data.get("photo_url") or "").strip()
        username = str(user_data.get("username") or "").strip() or None

        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            settings_service = AppSettingsService(session)
            signup_bonus_credits = await settings_service.get_int("signup_bonus_credits", settings.signup_bonus_credits)
            is_admin = telegram_id in settings.admin_ids()
            user = await credits.ensure_user(telegram_id, username, is_admin)
            settings_payload = dict(user.settings or {})
            settings_payload["lang"] = lang
            if first_name:
                settings_payload["first_name"] = first_name
            if last_name:
                settings_payload["last_name"] = last_name
            if photo_url:
                settings_payload["photo_url"] = photo_url
            user.settings = settings_payload
            await credits.apply_signup_bonus(user, signup_bonus_credits)
            await session.commit()

        request.session["user_id"] = telegram_id
        request.session["lang"] = lang
        request.session["username"] = username
        return {"ok": True}

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse(url="/", status_code=302)

    @app.get("/api/me")
    async def api_me(request: Request):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)
            count_row = await session.execute(
                select(func.count(Generation.id)).where(Generation.user_id == user.id)
            )
            generation_count = int(count_row.scalar_one() or 0)
            first_name = (user.settings.get("first_name") or "").strip()
            last_name = (user.settings.get("last_name") or "").strip()
            photo_url = (user.settings.get("photo_url") or "").strip()
            display_name = " ".join([part for part in [first_name, last_name] if part]) or user.username or str(
                user.telegram_id
            )
            ai_brain = {
                "enabled": False,
                "openai_ready": bool(settings.openai_api_key.strip()),
                "price_per_improve": 0,
                "daily_limit_per_user": 0,
                "daily_used": 0,
                "daily_remaining": None,
                "pack_remaining": 0,
                "pack_price_credits": 0,
                "pack_size_improvements": 10,
            }
            try:
                brain_service = AIBrainService(
                    session,
                    openai_api_key=settings.openai_api_key,
                    openai_base_url=settings.openai_base_url,
                )
                brain_cfg = await brain_service.get_config()
                brain_daily_used = await brain_service.get_daily_success_count(user.id)
                brain_pack_remaining = await brain_service.get_remaining_improvements(user.id)
                brain_daily_limit = max(0, int(brain_cfg.daily_limit_per_user or 0))
                if brain_daily_limit > 0:
                    brain_daily_remaining = max(0, brain_daily_limit - brain_daily_used)
                else:
                    brain_daily_remaining = None
                ai_brain = {
                    "enabled": bool(brain_cfg.enabled) and bool(settings.openai_api_key.strip()),
                    "openai_ready": bool(settings.openai_api_key.strip()),
                    "price_per_improve": max(0, int(brain_cfg.price_per_improve or 0)),
                    "daily_limit_per_user": brain_daily_limit,
                    "daily_used": brain_daily_used,
                    "daily_remaining": brain_daily_remaining,
                    "pack_remaining": brain_pack_remaining,
                    "pack_price_credits": max(0, int(brain_cfg.pack_price_credits or 0)),
                    "pack_size_improvements": max(1, int(brain_cfg.pack_size_improvements or 1)),
                }
            except Exception:
                logger.exception("ai_brain_state_unavailable", extra={"user_id": user.id})
            return {
                "telegram_id": user.telegram_id,
                "username": user.username,
                "display_name": display_name,
                "photo_url": photo_url,
                "balance": user.balance_credits,
                "lang": user.settings.get("lang", "ru"),
                "max_outputs": settings.max_outputs_per_request,
                "generation_count": generation_count,
                "ai_brain": ai_brain,
            }

    @app.post("/api/providers/kie/webhook")
    async def api_kie_webhook(request: Request):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid_payload"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "invalid_payload"}, status_code=400)

        task_id = KieClient.extract_task_id(payload)
        if not task_id:
            return JSONResponse({"ok": False, "error": "task_id_missing"}, status_code=400)

        timestamp = (request.headers.get("x-webhook-timestamp") or "").strip()
        signature = (request.headers.get("x-webhook-signature") or "").strip()
        require_signature = bool(settings.kie_webhook_require_signature)
        webhook_hmac_key = settings.kie_webhook_hmac_key.strip()

        if require_signature and not webhook_hmac_key:
            return JSONResponse({"ok": False, "error": "webhook_hmac_key_not_configured"}, status_code=503)

        if require_signature or (timestamp and signature and webhook_hmac_key):
            if not timestamp or not signature:
                return JSONResponse({"ok": False, "error": "missing_signature_headers"}, status_code=401)
            try:
                timestamp_int = int(timestamp)
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "invalid_timestamp"}, status_code=401)

            now = int(time.time())
            max_skew = max(1, int(settings.kie_webhook_max_skew_seconds))
            if abs(now - timestamp_int) > max_skew:
                return JSONResponse({"ok": False, "error": "timestamp_out_of_range"}, status_code=401)

            is_valid = KieClient.verify_webhook_signature(
                task_id=task_id,
                timestamp_seconds=timestamp,
                received_signature=signature,
                webhook_hmac_key=webhook_hmac_key,
            )
            if not is_valid:
                return JSONResponse({"ok": False, "error": "invalid_signature"}, status_code=401)

        poller = app.state.user_web_poller
        if not poller:
            return JSONResponse({"ok": False, "error": "poller_unavailable"}, status_code=503)

        try:
            status = await poller.process_provider_webhook("kie", payload)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except KieError as exc:
            return JSONResponse(
                {"ok": False, "error": "kie_status_failed", "detail": str(exc)},
                status_code=502,
            )

        return {"ok": True, "task_id": task_id, "status": status}

    @app.api_route("/kie/webhook", methods=["POST", "GET", "HEAD"])
    async def kie_webhook_alias(request: Request):
        if request.method in {"GET", "HEAD"}:
            return {"ok": True}
        return await api_kie_webhook(request)

    @app.get("/api/payments/packages")
    async def api_payment_packages(request: Request):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not _is_cryptopay_enabled(settings):
            return {"enabled": False, "packages": []}

        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)

            stars_per_credit, usd_per_star = await _cryptocloud_pricing(session)
            rows = await session.execute(
                select(StarProduct)
                .where(StarProduct.active.is_(True))
                .order_by(StarProduct.sort_order.asc(), StarProduct.id.asc())
            )
            products = rows.scalars().all()
            packages = []
            for product in products:
                credits_total = get_product_credits(product)
                stars_total = get_product_stars_price(product)
                amount = get_product_usd_price(product, stars_per_credit, usd_per_star)
                usd_per_credit = round(float(amount) / credits_total, 6) if credits_total > 0 else 0.0
                packages.append(
                    {
                        "id": product.id,
                        "title": product.title,
                        "credits_amount": credits_total,
                        "credits_base": int(product.credits_base if product.credits_base is not None else product.credits_amount),
                        "credits_bonus": int(product.credits_bonus or 0),
                        "stars_amount": stars_total,
                        "amount": float(amount),
                        "usd_per_credit": usd_per_credit,
                        "currency": settings.cryptopay_fiat.upper(),
                    }
                )
        return {"enabled": True, "packages": packages}

    @app.post("/api/payments/cryptopay/create")
    async def api_cryptopay_create_invoice(request: Request):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not _is_cryptopay_enabled(settings):
            return JSONResponse({"error": "cryptopay_not_configured"}, status_code=503)

        try:
            data = await request.json()
        except Exception:
            data = {}
        product_id_raw = data.get("product_id") if isinstance(data, dict) else None
        try:
            product_id = int(product_id_raw)
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid_product_id"}, status_code=400)

        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)

            product_row = await session.execute(
                select(StarProduct).where(StarProduct.id == product_id, StarProduct.active.is_(True))
            )
            product = product_row.scalar_one_or_none()
            if not product:
                return JSONResponse({"error": "product_not_found"}, status_code=404)

            stars_per_credit, usd_per_star = await _cryptocloud_pricing(session)
            credits_total = get_product_credits(product)
            amount = get_product_usd_price(product, stars_per_credit, usd_per_star)
            local_order_id = f"cp_web_{uuid.uuid4().hex[:20]}"
            order_payload = f"cp:{product.id}:{local_order_id}"

            client = CryptoPayClient(
                api_token=settings.cryptopay_api_token,
                base_url=settings.cryptopay_base_url,
            )
            description = f"{product.title} - {credits_total} credits"
            try:
                invoice = await client.create_invoice(
                    amount=amount,
                    currency_type="fiat",
                    fiat=settings.cryptopay_fiat,
                    accepted_assets=settings.cryptopay_accepted_assets or None,
                    description=description,
                    payload=local_order_id,
                    expires_in=settings.cryptopay_expires_in,
                    allow_comments=False,
                    allow_anonymous=True,
                )
            except CryptoPayError as exc:
                return JSONResponse(
                    {"error": "cryptopay_create_failed", "detail": str(exc)},
                    status_code=502,
                )

            invoice_id = str(invoice.get("invoice_id") or "").strip()
            pay_url = str(
                invoice.get("bot_invoice_url")
                or invoice.get("mini_app_invoice_url")
                or invoice.get("web_app_invoice_url")
                or ""
            ).strip()
            invoice_status = _normalize_invoice_status(invoice.get("status")) or "active"
            if not invoice_id or not pay_url:
                return JSONResponse({"error": "cryptopay_invalid_response"}, status_code=502)

            order = Order(
                user_id=user.id,
                telegram_payment_charge_id=local_order_id,
                provider_payment_charge_id=invoice_id,
                payload=order_payload,
                stars_amount=0,
                credits_amount=credits_total,
                status=f"cp_{invoice_status}"[:32],
                created_at=utcnow(),
            )
            session.add(order)
            await session.commit()

        return {
            "ok": True,
            "invoice_id": invoice_id,
            "pay_url": pay_url,
            "invoice_status": invoice_status,
            "credits_amount": credits_total,
            "amount": float(amount),
            "currency": settings.cryptopay_fiat.upper(),
        }

    @app.get("/api/payments/cryptopay/status/{invoice_id}")
    async def api_cryptopay_invoice_status(request: Request, invoice_id: str):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not _is_cryptopay_enabled(settings):
            return JSONResponse({"error": "cryptopay_not_configured"}, status_code=503)

        invoice_id = invoice_id.strip()
        if not invoice_id:
            return JSONResponse({"error": "invalid_invoice_id"}, status_code=400)

        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)

            order_row = await session.execute(
                select(Order).where(
                    Order.provider_payment_charge_id == invoice_id,
                    Order.user_id == user.id,
                )
            )
            order = order_row.scalar_one_or_none()
            if not order:
                return JSONResponse({"error": "order_not_found"}, status_code=404)

            client = CryptoPayClient(
                api_token=settings.cryptopay_api_token,
                base_url=settings.cryptopay_base_url,
            )
            try:
                invoice_info = await client.get_invoice(invoice_id)
            except CryptoPayError as exc:
                return JSONResponse(
                    {"error": "cryptopay_status_failed", "detail": str(exc)},
                    status_code=502,
                )
            if not invoice_info:
                return JSONResponse({"error": "invoice_not_found"}, status_code=404)

            invoice_status, is_paid, credits_added = await apply_cryptopay_settlement(session, order, invoice_info)
            await session.commit()
            return {
                "ok": True,
                "invoice_status": invoice_status,
                "order_status": order.status,
                "paid": is_paid,
                "credits_added": credits_added,
                "balance": user.balance_credits,
            }

    @app.post("/api/payments/cryptopay/postback")
    async def api_cryptopay_postback(request: Request):
        if not _is_cryptopay_enabled(settings):
            return {"ok": True}

        raw_body = await request.body()
        signature = (request.headers.get("crypto-pay-api-signature") or "").strip()
        is_valid = CryptoPayClient.verify_webhook_signature(
            api_token=settings.cryptopay_api_token,
            raw_body=raw_body,
            signature=signature,
        )
        if not is_valid:
            return JSONResponse({"ok": False, "error": "invalid_signature"}, status_code=401)

        try:
            update = json.loads(raw_body.decode("utf-8"))
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid_payload"}, status_code=400)

        if not isinstance(update, dict):
            return JSONResponse({"ok": False, "error": "invalid_payload"}, status_code=400)

        update_type = str(update.get("update_type") or "").strip().lower()
        if update_type != "invoice_paid":
            return {"ok": True}

        payload = update.get("payload") or {}
        if not isinstance(payload, dict):
            return {"ok": True}

        invoice_id = str(payload.get("invoice_id") or "").strip()
        local_order_id = _cryptopay_payload_order_id(payload.get("payload"))

        async with app.state.sessionmaker() as session:
            order: Order | None = None
            if invoice_id:
                order_row = await session.execute(
                    select(Order).where(Order.provider_payment_charge_id == invoice_id)
                )
                order = order_row.scalar_one_or_none()
            if not order and local_order_id:
                order_row = await session.execute(
                    select(Order).where(Order.telegram_payment_charge_id == local_order_id)
                )
                order = order_row.scalar_one_or_none()
            if not order:
                return {"ok": True}

            invoice_status, is_paid, credits_added = await apply_cryptopay_settlement(session, order, payload)
            user = await session.get(User, order.user_id)
            await session.commit()

        if user and credits_added > 0:
            lang = normalize_lang((user.settings or {}).get("lang"))
            await notify_payment_success(user.telegram_id, lang, credits_added)

        return {
            "ok": True,
            "invoice_status": invoice_status,
            "order_status": order.status,
            "paid": is_paid,
            "credits_added": credits_added,
        }

    @app.api_route("/cryptopay/callback", methods=["POST", "GET", "HEAD"])
    async def cryptopay_callback_alias(request: Request):
        if request.method in {"GET", "HEAD"}:
            return {"ok": True}
        return await api_cryptopay_postback(request)

    @app.post("/api/payments/cryptocloud/create")
    async def api_cryptocloud_create_invoice(request: Request):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not _is_cryptocloud_enabled(settings):
            return JSONResponse({"error": "cryptocloud_not_configured"}, status_code=503)

        try:
            data = await request.json()
        except Exception:
            data = {}
        product_id_raw = data.get("product_id") if isinstance(data, dict) else None
        try:
            product_id = int(product_id_raw)
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid_product_id"}, status_code=400)

        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)

            product_row = await session.execute(
                select(StarProduct).where(StarProduct.id == product_id, StarProduct.active.is_(True))
            )
            product = product_row.scalar_one_or_none()
            if not product:
                return JSONResponse({"error": "product_not_found"}, status_code=404)

            stars_per_credit, usd_per_star = await _cryptocloud_pricing(session)
            credits_total = get_product_credits(product)
            amount = get_product_usd_price(product, stars_per_credit, usd_per_star)
            local_order_id = f"cc_{uuid.uuid4().hex}"
            order_payload = f"cc:{product.id}:{local_order_id}"
            client = CryptoCloudClient(
                api_key=settings.cryptocloud_api_key,
                shop_id=settings.cryptocloud_shop_id,
            )
            try:
                invoice = await client.create_invoice(
                    amount=float(amount),
                    currency=settings.cryptocloud_currency.upper(),
                    order_id=local_order_id,
                    locale=_crypto_locale(_get_lang(request)),
                )
            except CryptoCloudError as exc:
                return JSONResponse(
                    {"error": "cryptocloud_create_failed", "detail": str(exc)},
                    status_code=502,
                )

            invoice_uuid = str(invoice.get("uuid") or "").strip()
            pay_url = str(invoice.get("link") or "").strip()
            invoice_status = _normalize_invoice_status(invoice.get("status")) or "created"
            if not invoice_uuid or not pay_url:
                return JSONResponse({"error": "cryptocloud_invalid_response"}, status_code=502)

            order = Order(
                user_id=user.id,
                telegram_payment_charge_id=local_order_id,
                provider_payment_charge_id=invoice_uuid,
                payload=order_payload,
                stars_amount=0,
                credits_amount=credits_total,
                status=f"cc_{invoice_status}"[:32],
                created_at=utcnow(),
            )
            session.add(order)
            await session.commit()

        return {
            "ok": True,
            "invoice_uuid": invoice_uuid,
            "pay_url": pay_url,
            "invoice_status": invoice_status,
            "credits_amount": credits_total,
            "amount": float(amount),
            "currency": settings.cryptocloud_currency.upper(),
        }

    @app.get("/api/payments/cryptocloud/status/{invoice_uuid}")
    async def api_cryptocloud_invoice_status(request: Request, invoice_uuid: str):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not _is_cryptocloud_enabled(settings):
            return JSONResponse({"error": "cryptocloud_not_configured"}, status_code=503)

        invoice_uuid = invoice_uuid.strip()
        if not invoice_uuid:
            return JSONResponse({"error": "invalid_invoice_uuid"}, status_code=400)

        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)

            order_row = await session.execute(
                select(Order).where(
                    Order.provider_payment_charge_id == invoice_uuid,
                    Order.user_id == user.id,
                )
            )
            order = order_row.scalar_one_or_none()
            if not order:
                return JSONResponse({"error": "order_not_found"}, status_code=404)

            client = CryptoCloudClient(
                api_key=settings.cryptocloud_api_key,
                shop_id=settings.cryptocloud_shop_id,
            )
            try:
                invoice_info = await client.invoice_status(invoice_uuid)
            except CryptoCloudError as exc:
                return JSONResponse(
                    {"error": "cryptocloud_status_failed", "detail": str(exc)},
                    status_code=502,
                )
            if not invoice_info:
                return JSONResponse({"error": "invoice_not_found"}, status_code=404)

            invoice_status, is_paid, credits_added = await apply_cryptocloud_settlement(session, order, invoice_info)
            await session.commit()
            return {
                "ok": True,
                "invoice_status": invoice_status,
                "order_status": order.status,
                "paid": is_paid,
                "credits_added": credits_added,
                "balance": user.balance_credits,
            }

    @app.post("/api/payments/cryptocloud/postback")
    async def api_cryptocloud_postback(request: Request):
        if not _is_cryptocloud_enabled(settings):
            return {"ok": True}

        payload: dict[str, Any] = {}
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                payload = raw
        except Exception:
            try:
                form_data = await request.form()
                payload = dict(form_data)
            except Exception:
                payload = {}

        invoice_uuid = str(
            payload.get("invoice_id")
            or payload.get("uuid")
            or payload.get("invoice_uuid")
            or ""
        ).strip()
        order_id = str(payload.get("order_id") or "").strip()

        async with app.state.sessionmaker() as session:
            order: Order | None = None
            if invoice_uuid:
                order_row = await session.execute(
                    select(Order).where(Order.provider_payment_charge_id == invoice_uuid)
                )
                order = order_row.scalar_one_or_none()
            if not order and order_id:
                order_row = await session.execute(
                    select(Order).where(Order.telegram_payment_charge_id == order_id)
                )
                order = order_row.scalar_one_or_none()
            if not order:
                return {"ok": True}

            invoice_uuid = order.provider_payment_charge_id
            client = CryptoCloudClient(
                api_key=settings.cryptocloud_api_key,
                shop_id=settings.cryptocloud_shop_id,
            )
            try:
                invoice_info = await client.invoice_status(invoice_uuid)
            except CryptoCloudError as exc:
                return JSONResponse(
                    {"ok": False, "error": "cryptocloud_status_failed", "detail": str(exc)},
                    status_code=502,
                )
            if not invoice_info:
                return {"ok": True}

            invoice_status, is_paid, credits_added = await apply_cryptocloud_settlement(session, order, invoice_info)
            await session.commit()

        return {
            "ok": True,
            "invoice_status": invoice_status,
            "order_status": order.status,
            "paid": is_paid,
            "credits_added": credits_added,
        }

    @app.api_route("/callback", methods=["POST", "GET", "HEAD"])
    async def cryptocloud_callback_alias(request: Request):
        if request.method in {"GET", "HEAD"}:
            return {"ok": True}
        return await api_cryptocloud_postback(request)

    @app.api_route("/successful-payment", methods=["GET", "HEAD"])
    async def cryptocloud_successful_payment(request: Request):
        if request.method == "HEAD":
            return Response(status_code=200)
        return RedirectResponse(url="/?payment=success", status_code=302)

    @app.api_route("/failed-payment", methods=["GET", "HEAD"])
    async def cryptocloud_failed_payment(request: Request):
        if request.method == "HEAD":
            return Response(status_code=200)
        return RedirectResponse(url="/?payment=failed", status_code=302)

    @app.get("/api/models")
    async def api_models(request: Request):
        lang = _get_lang(request)
        models_payload = []
        for model in list_models():
            if model.model_type != "image":
                continue
            options = []
            for opt in model.options:
                if opt.ui_hidden:
                    continue
                options.append(
                    {
                        "key": opt.key,
                        "label": _option_label(lang, opt.key),
                        "default": opt.default,
                        "values": [
                            {"value": v.value, "label": _value_label(lang, opt.key, v.value)} for v in opt.values
                        ],
                    }
                )
            models_payload.append(
                {
                    "key": model.key,
                    "display_name": model.display_name,
                    "tagline": _model_tagline(lang, model.key, model.tagline),
                    "supports_reference_images": model.supports_reference_images,
                    "supportsReference": model.supports_reference_images,
                    "requires_reference_images": model.requires_reference_images,
                    "requiresReference": model.requires_reference_images,
                    "max_reference_images": model.max_reference_images,
                    "options": options,
                }
            )
        return {"models": models_payload}

    @app.post("/api/quote")
    async def api_quote(request: Request):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        data = await request.json()
        model_key = (data.get("model_key") or "").strip()
        model = get_model(model_key)
        if not model:
            return JSONResponse({"error": "model_not_found"}, status_code=404)
        try:
            outputs = int(data.get("outputs") or 1)
        except (ValueError, TypeError):
            outputs = 1
        options_payload = data.get("options") or {}
        if not isinstance(options_payload, dict):
            options_payload = {}
        options_payload = model.validate_options(options_payload)
        if model.key == "nano_banana_pro":
            options_payload.setdefault("reference_images", "none")

        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)
            pricing = PricingService(session)
            breakdown = await pricing.resolve_cost(model, options_payload, outputs, user.referral_discount_pct or 0)
            subtotal = breakdown.per_output * breakdown.outputs
            return {
                "per_output": breakdown.per_output,
                "outputs": breakdown.outputs,
                "discount_pct": breakdown.discount_pct,
                "total": breakdown.total,
                "breakdown": {
                    "base": breakdown.base,
                    "modifiers": [
                        {"key": modifier_key, "amount": modifier_amount}
                        for modifier_key, modifier_amount in breakdown.modifiers
                    ],
                    "per_output": breakdown.per_output,
                    "outputs": breakdown.outputs,
                    "outputs_extra": max(0, subtotal - breakdown.per_output),
                    "subtotal": subtotal,
                },
            }

    @app.post("/api/brain/improve")
    async def api_brain_improve(request: Request):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            data = {}
        raw_prompt = str((data or {}).get("prompt") or "")
        prompt = raw_prompt.strip()

        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)

            brain = AIBrainService(
                session,
                openai_api_key=settings.openai_api_key,
                openai_base_url=settings.openai_base_url,
            )
            try:
                config = await brain.get_config()
            except Exception:
                logger.exception("ai_brain_config_unavailable_improve", extra={"user_id": user.id})
                return JSONResponse({"error": "brain_unavailable"}, status_code=503)
            action_id = uuid.uuid4().hex
            model_name = (config.openai_model or "gpt-4o-mini").strip()
            temperature = float(config.temperature or 0.7)
            max_tokens = max(1, int(config.max_tokens or 600))
            price_per_improve = max(0, int(config.price_per_improve or 0))
            if not prompt:
                await brain.log_improve(
                    user_id=user.id,
                    action="improve_prompt",
                    status="rejected",
                    source="none",
                    spent_credits=0,
                    prompt_original=raw_prompt,
                    prompt_result=None,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    error_code="prompt_required",
                    error_message="Prompt is empty",
                    meta={},
                )
                await session.commit()
                return JSONResponse({"error": "prompt_required"}, status_code=400)
            if settings.max_prompt_length > 0 and len(raw_prompt) > settings.max_prompt_length:
                await brain.log_improve(
                    user_id=user.id,
                    action="improve_prompt",
                    status="rejected",
                    source="none",
                    spent_credits=0,
                    prompt_original=raw_prompt,
                    prompt_result=None,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    error_code="prompt_too_long",
                    error_message=f"Prompt length exceeds limit: {settings.max_prompt_length}",
                    meta={"max_prompt_length": settings.max_prompt_length},
                )
                await session.commit()
                return JSONResponse({"error": "prompt_too_long"}, status_code=400)
            meta_base = {
                "request_id": action_id,
                "ip": request.client.host if request.client else "",
                "user_agent": str(request.headers.get("user-agent") or "")[:255],
            }

            if not config.enabled or not settings.openai_api_key.strip():
                await brain.log_improve(
                    user_id=user.id,
                    action="improve_prompt",
                    status="rejected",
                    source="none",
                    spent_credits=0,
                    prompt_original=prompt,
                    prompt_result=None,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    error_code="brain_disabled",
                    error_message="AI Brain feature is disabled or OpenAI key is not configured",
                    meta=meta_base,
                )
                await session.commit()
                return JSONResponse({"error": "brain_disabled"}, status_code=503)

            rate_limiter = app.state.brain_rate_limiter
            if rate_limiter and not rate_limiter.allow(int(user.id)):
                await brain.log_improve(
                    user_id=user.id,
                    action="improve_prompt",
                    status="rejected",
                    source="none",
                    spent_credits=0,
                    prompt_original=prompt,
                    prompt_result=None,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    error_code="rate_limited",
                    error_message="Too many requests",
                    meta=meta_base,
                )
                await session.commit()
                return JSONResponse({"error": "rate_limited"}, status_code=429)

            daily_used = await brain.get_daily_success_count(user.id)
            daily_limit = max(0, int(config.daily_limit_per_user or 0))
            if daily_limit > 0 and daily_used >= daily_limit:
                await brain.log_improve(
                    user_id=user.id,
                    action="improve_prompt",
                    status="rejected",
                    source="none",
                    spent_credits=0,
                    prompt_original=prompt,
                    prompt_result=None,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    error_code="daily_limit_reached",
                    error_message=f"Daily limit reached: {daily_limit}",
                    meta={**meta_base, "daily_used": daily_used, "daily_limit": daily_limit},
                )
                await session.commit()
                return JSONResponse({"error": "daily_limit_reached"}, status_code=429)

            pack_remaining_before = await brain.get_remaining_improvements(user.id)
            if pack_remaining_before <= 0 and price_per_improve > 0 and int(user.balance_credits or 0) < price_per_improve:
                await brain.log_improve(
                    user_id=user.id,
                    action="improve_prompt",
                    status="rejected",
                    source="none",
                    spent_credits=0,
                    prompt_original=prompt,
                    prompt_result=None,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    error_code="insufficient_credits",
                    error_message="Not enough credits for prompt improvement",
                    meta=meta_base,
                )
                await session.commit()
                return JSONResponse({"error": "insufficient_credits"}, status_code=400)

            try:
                improved_prompt = await brain.improvePrompt(prompt, config)
            except BrainProviderError as exc:
                await brain.log_improve(
                    user_id=user.id,
                    action="improve_prompt",
                    status="error",
                    source="none",
                    spent_credits=0,
                    prompt_original=prompt,
                    prompt_result=None,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    error_code=exc.code,
                    error_message=exc.message,
                    meta=meta_base,
                )
                await session.commit()
                return JSONResponse({"error": "brain_provider_error"}, status_code=502)

            improved_prompt = clamp_text(improved_prompt or "", settings.max_prompt_length).strip()
            if not improved_prompt:
                await brain.log_improve(
                    user_id=user.id,
                    action="improve_prompt",
                    status="error",
                    source="none",
                    spent_credits=0,
                    prompt_original=prompt,
                    prompt_result=None,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    error_code="empty_improved_prompt",
                    error_message="Provider returned empty prompt",
                    meta=meta_base,
                )
                await session.commit()
                return JSONResponse({"error": "brain_provider_error"}, status_code=502)

            try:
                charge = await brain.consume_for_improvement(
                    user,
                    price_per_improve=price_per_improve,
                    request_id=action_id,
                )
            except ValueError:
                await brain.log_improve(
                    user_id=user.id,
                    action="improve_prompt",
                    status="rejected",
                    source="none",
                    spent_credits=0,
                    prompt_original=prompt,
                    prompt_result=None,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    error_code="insufficient_credits",
                    error_message="Not enough credits after provider response",
                    meta=meta_base,
                )
                await session.commit()
                return JSONResponse({"error": "insufficient_credits"}, status_code=400)

            await brain.log_improve(
                user_id=user.id,
                action="improve_prompt",
                status="success",
                source=charge.source,
                spent_credits=charge.spent_credits,
                prompt_original=prompt,
                prompt_result=improved_prompt,
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                meta={
                    **meta_base,
                    "daily_used_after": daily_used + 1,
                    "daily_limit": daily_limit,
                    "pack_remaining_before": pack_remaining_before,
                    "pack_remaining_after": charge.remaining_improvements,
                },
            )
            await session.commit()
            return {
                "ok": True,
                "improved_prompt": improved_prompt,
                "spent_credits": charge.spent_credits,
                "charged_from": charge.source,
                "balance": charge.balance_credits,
                "remaining_improvements": charge.remaining_improvements,
                "daily_used": daily_used + 1,
                "daily_limit_per_user": daily_limit,
                "price_per_improve": price_per_improve,
            }

    @app.post("/api/brain/pack/buy")
    async def api_brain_buy_pack(request: Request):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)

            brain = AIBrainService(
                session,
                openai_api_key=settings.openai_api_key,
                openai_base_url=settings.openai_base_url,
            )
            try:
                config = await brain.get_config()
            except Exception:
                logger.exception("ai_brain_config_unavailable_pack", extra={"user_id": user.id})
                return JSONResponse({"error": "brain_unavailable"}, status_code=503)
            if not config.enabled:
                return JSONResponse({"error": "brain_disabled"}, status_code=503)

            pack_price = max(0, int(config.pack_price_credits or 0))
            pack_size = max(1, int(config.pack_size_improvements or 1))
            if pack_price > 0 and int(user.balance_credits or 0) < pack_price:
                return JSONResponse({"error": "insufficient_credits"}, status_code=400)

            action_id = uuid.uuid4().hex
            try:
                charge = await brain.purchase_pack(
                    user,
                    pack_price_credits=pack_price,
                    pack_size_improvements=pack_size,
                    request_id=action_id,
                )
            except ValueError:
                return JSONResponse({"error": "insufficient_credits"}, status_code=400)

            await brain.log_improve(
                user_id=user.id,
                action="buy_pack",
                status="success",
                source=charge.source,
                spent_credits=charge.spent_credits,
                prompt_original="(pack_purchase)",
                prompt_result=None,
                model=(config.openai_model or "gpt-4o-mini").strip(),
                temperature=float(config.temperature or 0.7),
                max_tokens=max(1, int(config.max_tokens or 600)),
                meta={
                    "request_id": action_id,
                    "pack_size": pack_size,
                    "pack_price": pack_price,
                },
            )
            await session.commit()
            return {
                "ok": True,
                "pack_size_improvements": pack_size,
                "pack_price_credits": pack_price,
                "remaining_improvements": charge.remaining_improvements,
                "balance": charge.balance_credits,
            }

    @app.post("/api/redeem")
    async def api_redeem(request: Request, code: str = Form("")):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        code = code.strip().upper()
        if not code:
            return JSONResponse({"error": "empty"}, status_code=400)

        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)
            promo_service = PromoService(session)
            status = await promo_service.redeem(user, code)
            if status == "invalid":
                return JSONResponse({"error": "invalid"}, status_code=400)
            if status == "used":
                return JSONResponse({"error": "used"}, status_code=400)
            promo = await session.get(PromoCode, code)
            if promo:
                await credits.add_ledger(
                    user,
                    promo.credits_amount,
                    "promo_redeem",
                    meta={"code": promo.code},
                    idempotency_key=f"promo:{promo.code}:{user.id}",
                )
            await session.commit()
            return {"ok": True, "added": promo.credits_amount if promo else 0}

    @app.post("/api/generate")
    async def api_generate(
        request: Request,
        model_key: str = Form(""),
        prompt: str = Form(""),
        options: str = Form("{}"),
        outputs: int = Form(1),
        files: List[UploadFile] = File(default_factory=list),
    ):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        model = get_model(model_key)
        if not model:
            return JSONResponse({"error": "model_not_found"}, status_code=404)

        prompt = clamp_text(prompt or "", settings.max_prompt_length)
        if not prompt.strip():
            return JSONResponse({"error": "empty_prompt"}, status_code=400)

        try:
            options_payload = json.loads(options or "{}")
        except json.JSONDecodeError:
            options_payload = {}

        options_payload = model.validate_options(options_payload)

        ref_urls: List[str] = []
        ref_files: List[str] = []
        if files:
            if len(files) > model.max_reference_images:
                return JSONResponse({"error": "too_many_refs"}, status_code=400)
            token = uuid.uuid4().hex
            ref_dir = os.path.join(settings.reference_storage_path, token)
            os.makedirs(ref_dir, exist_ok=True)
            for upload in files:
                ext = os.path.splitext(upload.filename or "")[1] or ".jpg"
                filename = f"{uuid.uuid4().hex}{ext}"
                local_path = os.path.join(ref_dir, filename)
                content = await upload.read()
                with open(local_path, "wb") as f:
                    f.write(content)
                public_url = f"{settings.public_file_base_url}/{token}/{filename}"
                ref_urls.append(public_url)
                ref_files.append(local_path)

        if model.requires_reference_images and not ref_urls:
            return JSONResponse({"error": "refs_required"}, status_code=400)

        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)

            kie = KieClient()
            gen_service = GenerationService(session, kie, None)
            try:
                generation = await gen_service.create_generation(
                    user,
                    model,
                    prompt,
                    options_payload,
                    int(outputs),
                    ref_urls,
                    ref_files,
                )
                await session.commit()
            except ValueError as exc:
                await session.rollback()
                return JSONResponse({"error": str(exc)}, status_code=400)
            except KieError as exc:
                await session.commit()
                if exc.status_code == 429:
                    return JSONResponse({"error": "queued"}, status_code=202)
                return JSONResponse({"error": "kie_error"}, status_code=502)
            finally:
                await kie.close()

            result = await session.execute(
                select(GenerationTask.id).where(GenerationTask.generation_id == generation.id)
            )
            task_ids = [row[0] for row in result.all()]
            poller = app.state.user_web_poller
            if poller:
                for task_id in task_ids:
                    poller.schedule(task_id)

            return {"ok": True, "generation_id": generation.id, "created_at": generation.created_at.isoformat()}

    @app.get("/api/history")
    async def api_history(request: Request):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)

            rows = await session.execute(
                select(Generation)
                .where(Generation.user_id == user.id)
                .order_by(Generation.created_at.desc())
            )
            generations = rows.scalars().all()
            history = []
            for gen in generations:
                tasks = await session.execute(
                    select(GenerationTask).where(GenerationTask.generation_id == gen.id)
                )
                urls: List[str] = []
                for task in tasks.scalars().all():
                    urls.extend(task.result_urls or [])
                seen = []
                for url in urls:
                    if url not in seen:
                        seen.append(url)
                history.append(
                    {
                        "id": gen.id,
                        "model": gen.model,
                        "prompt": gen.prompt,
                        "status": gen.status,
                        "created_at": gen.created_at.isoformat(),
                        "urls": seen,
                    }
                )
        return {"history": history}

    @app.get("/api/generations/{generation_id}/status")
    async def api_generation_status(request: Request, generation_id: int):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)

            generation = await session.get(Generation, generation_id)
            if not generation:
                return JSONResponse({"error": "generation_not_found"}, status_code=404)
            if generation.user_id != user.id:
                return JSONResponse({"error": "forbidden"}, status_code=403)

            task_rows = await session.execute(
                select(GenerationTask.state, GenerationTask.fail_msg).where(GenerationTask.generation_id == generation.id)
            )
            states = []
            error_message = ""
            for state, fail_msg in task_rows.all():
                state_value = str(state or "").strip().lower() or "queued"
                states.append(state_value)
                if not error_message and fail_msg:
                    error_message = str(fail_msg)

            counts = {
                "queued": sum(1 for state in states if state == "queued"),
                "pending": sum(1 for state in states if state == "pending"),
                "running": sum(1 for state in states if state == "running"),
                "success": sum(1 for state in states if state == "success"),
                "fail": sum(1 for state in states if state == "fail"),
            }
            total = len(states)
            done = generation.status in {"success", "partial", "fail"}
            return {
                "generation_id": generation.id,
                "status": generation.status,
                "done": done,
                "created_at": generation.created_at.isoformat(),
                "updated_at": generation.updated_at.isoformat(),
                "tasks_total": total,
                "tasks_queued": counts["queued"],
                "tasks_pending": counts["pending"],
                "tasks_running": counts["running"],
                "tasks_success": counts["success"],
                "tasks_fail": counts["fail"],
                "error_message": error_message,
            }

    @app.get("/api/download")
    async def api_download(request: Request, url: str):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not _is_allowed_download(url):
            return JSONResponse({"error": "forbidden"}, status_code=403)

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.get(url, follow_redirects=True)
            except httpx.RequestError:
                return JSONResponse({"error": "fetch_failed"}, status_code=502)

        if resp.status_code >= 400:
            return JSONResponse({"error": "not_found"}, status_code=404)

        filename = _filename_from_url(url)
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
        }
        content_type = resp.headers.get("content-type", "application/octet-stream")
        return StreamingResponse(iter([resp.content]), media_type=content_type, headers=headers)

    @app.delete("/api/generations/{generation_id}")
    async def api_delete_generation(request: Request, generation_id: int):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        async with app.state.sessionmaker() as session:
            credits = CreditsService(session)
            user = await credits.get_user(int(request.session["user_id"]))
            if not user:
                return JSONResponse({"error": "user_not_found"}, status_code=404)
            generation = await session.get(Generation, generation_id)
            if not generation:
                return {"ok": True}
            if generation.user_id != user.id:
                return JSONResponse({"error": "forbidden"}, status_code=403)

            tasks = await session.execute(
                select(GenerationTask).where(GenerationTask.generation_id == generation_id)
            )
            for task in tasks.scalars().all():
                await session.delete(task)
            await session.delete(generation)
            await session.commit()
        return {"ok": True}

    @app.post("/api/generations/{generation_id}/delete")
    async def api_delete_generation_post(request: Request, generation_id: int):
        return await api_delete_generation(request, generation_id)

    app.i18n_keys = [
        "site_title",
        "site_tagline",
        "site_notice",
        "input_title",
        "output_title",
        "output_empty",
        "output_empty_hint",
        "result_view_grid",
        "result_view_carousel",
        "result_download",
        "result_regenerate",
        "result_regenerate_this",
        "result_edit_prompt",
        "result_edit_ai",
        "result_edit_with_title",
        "result_edit_with_no_models",
        "result_favorite_add",
        "result_favorite_remove",
        "result_reference_added",
        "result_reference_limit",
        "result_reference_failed",
        "result_favorite_saved",
        "result_favorite_removed",
        "download",
        "delete",
        "history_title",
        "balance",
        "credits",
        "topup_button",
        "topup_redeem_option",
        "topup_stars_option",
        "topup_crypto_option",
        "topup_modal_redeem_title",
        "topup_modal_crypto_title",
        "topup_stars_redirect",
        "topup_stars_unavailable",
        "language",
        "redeem",
        "redeem_placeholder",
        "redeem_button",
        "model_label",
        "prompt_label",
        "prompt_placeholder",
        "prompt_helper_button",
        "prompt_helper_title",
        "prompt_helper_empty",
        "prompt_history_button",
        "prompt_history_title",
        "prompt_history_empty",
        "prompt_history_remove",
        "prompt_length_hint",
        "prompt_too_long",
        "brain_improve_button",
        "brain_pack_buy_button",
        "brain_pack_remaining",
        "brain_pack_buying",
        "brain_pack_buy_success",
        "brain_pack_buy_failed",
        "brain_improve_loading",
        "brain_restore_original",
        "brain_improve_success",
        "brain_unavailable",
        "brain_daily_limit_reached",
        "brain_not_enough_credits",
        "brain_rate_limited",
        "brain_failed",
        "brain_pack_used",
        "brain_credits_used",
        "prompt_template_logo_title",
        "prompt_template_logo_text",
        "prompt_template_social_title",
        "prompt_template_social_text",
        "prompt_template_cinematic_title",
        "prompt_template_cinematic_text",
        "prompt_template_product_title",
        "prompt_template_product_text",
        "prompt_template_icon_title",
        "prompt_template_icon_text",
        "prompt_template_portrait_title",
        "prompt_template_portrait_text",
        "upload_label",
        "upload_hint",
        "upload_hint_required",
        "upload_hint_optional",
        "upload_required",
        "upload_count",
        "upload_button",
        "ref_images_title",
        "ref_images_note",
        "ref_add",
        "ref_replace",
        "ref_add_sub",
        "options_label",
        "aspect_ratio",
        "resolution",
        "output_format",
        "outputs",
        "run",
        "run_pending",
        "history",
        "history_empty",
        "history_deleted",
        "logout",
        "prompt_required",
        "login_required",
        "miniapp_auth_in_progress",
        "miniapp_auth_failed",
        "request_sent",
        "error_prefix",
        "promo_added",
        "promo_error",
        "crypto_title",
        "crypto_select_package",
        "crypto_create_invoice",
        "crypto_open_invoice",
        "crypto_check_status",
        "crypto_loading_packages",
        "crypto_packages_empty",
        "crypto_invoice_created",
        "crypto_waiting_payment",
        "crypto_partial_payment",
        "crypto_paid",
        "crypto_canceled",
        "crypto_unavailable",
        "crypto_create_failed",
        "crypto_status_failed",
        "crypto_best_value",
        "crypto_bonus_badge",
        "crypto_save_badge",
        "crypto_base_bonus_line",
        "crypto_total_line",
        "delete_failed",
        "quote_line",
        "quote_cost",
        "quote_info_title",
        "quote_info",
        "quote_breakdown_base",
        "quote_breakdown_outputs",
        "quote_breakdown_discount",
        "quote_breakdown_total",
        "quote_breakdown_fallback",
        "quote_insufficient",
        "confirm_title",
        "confirm_message",
        "confirm_cancel",
        "confirm_continue",
        "confirm_dont_show_again",
        "confirm_skip_toggle",
        "quote_login_required",
        "quote_unavailable",
        "gen_status_queued",
        "gen_status_step_1",
        "gen_status_step_2",
        "gen_status_finalizing",
        "gen_eta_format",
        "gen_cancel",
        "gen_canceled_local",
        "gen_running_already",
        "gen_connection_retrying",
        "gen_taking_long",
        "gen_failed_basic",
        "gen_partial_done",
    ]

    return app
