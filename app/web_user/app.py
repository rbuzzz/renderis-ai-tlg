from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Dict, List

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Generation, GenerationTask, PromoCode
from app.db.session import create_sessionmaker
from app.i18n import normalize_lang, t
from app.modelspecs.registry import get_model, list_models
from app.services.credits import CreditsService
from app.services.pricing import PricingService
from app.services.generation import GenerationService
from app.services.kie_client import KieClient, KieError
from app.services.poller import PollManager
from app.services.promos import PromoService
from app.utils.text import clamp_text


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _is_logged_in(request: Request) -> bool:
    return bool(request.session.get("user_id"))


def _get_lang(request: Request) -> str:
    session_lang = request.session.get("lang")
    if session_lang:
        return normalize_lang(session_lang)
    header = request.headers.get("accept-language", "")
    lang = header.split(",")[0].strip()
    return normalize_lang(lang)


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

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        lang = _get_lang(request)
        return app.state.templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "title": t(lang, "site_title"),
                "lang": lang,
                "labels": {key: t(lang, key) for key in app.i18n_keys},
                "logged_in": _is_logged_in(request),
                "bot_username": settings.bot_username,
            },
        )

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
                    "tagline": model.tagline,
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
        "delete_failed",
        "quote_line",
        "quote_login_required",
        "quote_unavailable",
    ]

    return app
