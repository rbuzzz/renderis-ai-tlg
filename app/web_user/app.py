from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlparse
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
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Generation, GenerationTask, Order, PromoCode, StarProduct, User
from app.db.session import create_sessionmaker
from app.i18n import SUPPORTED_LANGS, normalize_lang, t
from app.modelspecs.registry import get_model, list_models
from app.services.credits import CreditsService
from app.services.cryptocloud import CryptoCloudClient, CryptoCloudError
from app.services.pricing import PricingService
from app.services.generation import GenerationService
from app.services.kie_client import KieClient, KieError
from app.services.poller import PollManager
from app.services.promos import PromoService
from app.services.app_settings import AppSettingsService
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


def _is_cryptocloud_enabled(settings) -> bool:
    return bool(settings.cryptocloud_api_key.strip() and settings.cryptocloud_shop_id.strip())


def _crypto_locale(lang: str) -> str:
    return "ru" if normalize_lang(lang) == "ru" else "en"


def _normalize_invoice_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _invoice_status_from_info(info: dict[str, Any] | None) -> str:
    if not info:
        return ""
    return _normalize_invoice_status(info.get("status") or info.get("invoice_status") or info.get("status_invoice"))


def _credit_price_usd(credits_amount: int, stars_per_credit: float, usd_per_star: float) -> float:
    usd_per_credit = Decimal(str(stars_per_credit)) * Decimal(str(usd_per_star))
    total = Decimal(str(credits_amount)) * usd_per_credit
    rounded = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rounded <= Decimal("0"):
        rounded = Decimal("0.01")
    return float(rounded)


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

    @app.on_event("startup")
    async def startup() -> None:
        if not settings.user_web_poll_enabled:
            return
        bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        kie_client = KieClient()
        poller = PollManager(bot, app.state.sessionmaker, kie_client)
        app.state.user_web_poller = poller
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
            await credits.apply_signup_bonus(user, settings.signup_bonus_credits)
            await session.commit()

        request.session["user_id"] = int(data["id"])
        request.session["lang"] = lang
        request.session["username"] = data.get("username")
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
            first_name = (user.settings.get("first_name") or "").strip()
            last_name = (user.settings.get("last_name") or "").strip()
            photo_url = (user.settings.get("photo_url") or "").strip()
            display_name = " ".join([part for part in [first_name, last_name] if part]) or user.username or str(
                user.telegram_id
            )
            return {
                "telegram_id": user.telegram_id,
                "username": user.username,
                "display_name": display_name,
                "photo_url": photo_url,
                "balance": user.balance_credits,
                "lang": user.settings.get("lang", "ru"),
                "max_outputs": settings.max_outputs_per_request,
            }

    @app.get("/api/payments/packages")
    async def api_payment_packages(request: Request):
        if not _is_logged_in(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not _is_cryptocloud_enabled(settings):
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
                amount = _credit_price_usd(product.credits_amount, stars_per_credit, usd_per_star)
                packages.append(
                    {
                        "id": product.id,
                        "title": product.title,
                        "credits_amount": product.credits_amount,
                        "amount": amount,
                        "currency": settings.cryptocloud_currency.upper(),
                    }
                )
        return {"enabled": True, "packages": packages}

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
            amount = _credit_price_usd(product.credits_amount, stars_per_credit, usd_per_star)
            local_order_id = f"cc_{uuid.uuid4().hex}"
            order_payload = f"cc:{product.id}:{local_order_id}"
            client = CryptoCloudClient(
                api_key=settings.cryptocloud_api_key,
                shop_id=settings.cryptocloud_shop_id,
            )
            try:
                invoice = await client.create_invoice(
                    amount=amount,
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
                credits_amount=product.credits_amount,
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
            "credits_amount": product.credits_amount,
            "amount": amount,
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

    @app.get("/successful-payment")
    async def cryptocloud_successful_payment():
        return RedirectResponse(url="/?payment=success", status_code=302)

    @app.get("/failed-payment")
    async def cryptocloud_failed_payment():
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
                    "requires_reference_images": model.requires_reference_images,
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
            return {
                "per_output": breakdown.per_output,
                "outputs": breakdown.outputs,
                "discount_pct": breakdown.discount_pct,
                "total": breakdown.total,
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
                .limit(20)
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
        "download",
        "delete",
        "history_title",
        "balance",
        "credits",
        "language",
        "redeem",
        "redeem_placeholder",
        "redeem_button",
        "model_label",
        "prompt_label",
        "prompt_placeholder",
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
        "delete_failed",
        "quote_line",
        "quote_login_required",
        "quote_unavailable",
    ]

    return app
