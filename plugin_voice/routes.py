"""plugin-voice routes — realtime session minting, tool relay, settings, static UI.

Auth model:
- Owner-facing routes are gated by ``luna_sdk.get_current_user`` (cookie or
  bearer). ``GET /rt/session`` works with cookie auth alone — widget iframes
  are read-only on hosted, and minting writes nothing in Luna.
- ``/rt/tool``, ``/rt/events`` and ``/live`` are gated by the per-call
  ``rt_token`` minted by ``/rt/session`` — the relay is driven by the widget's
  data-channel handler and must keep working however the iframe is embedded.
"""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from luna_sdk import get_current_user

from fastapi import WebSocket, WebSocketDisconnect

from . import (
    VAULT_OPENAI_KEY,
    VAULT_SETTINGS,
    broker,
    identity,
    openai_realtime,
    persona_config,
    setup,
    talker,
    tasks,
)
from .openai_realtime import RealtimeError
from . import state as live_state

VAULT_PROFILE = "plugin_voice.voice_profile"

# Pre-0.5.0 (ElevenLabs era) vault entries — deleted on disconnect so a
# migrated install leaves nothing behind.
LEGACY_VAULT_KEYS = (
    "plugin_voice.elevenlabs_api_key",
    "plugin_voice.agent_id",
    "plugin_voice.bridge_secret",
)

log = logging.getLogger("plugin-voice.routes")


async def _owner_conversation_id(ctx) -> object | None:
    """The conversation a delegated luna_do turn should bind to.

    A voice call has no chat window of its own, so a background ``run_turn``
    left unbound can't surface approval cards or route ``send_chat_message`` —
    approval-gated actions then stall forever ("sees the tools, can't use
    them"). Binding the owner's active conversation is the sanctioned fix.

    Resolved via the SDK-only ``ctx.conversations`` reader (no core imports):
    the conversation carrying the most recent message is the owner's active
    chat, matching ``send_chat_message``'s own "most recent" fallback. Any
    failure degrades to None — today's unbound behavior — and never raises.
    """
    reader = getattr(ctx, "conversations", None)
    if reader is None:
        return None
    try:
        convs = await reader.list()
        ids = [c.id for c in convs if getattr(c, "id", None) is not None]
        if not ids:
            return None
        recent = await reader.messages(ids, order="desc", limit=1)
        return recent[0].conversation_id if recent else ids[0]
    except Exception as exc:  # noqa: BLE001 — never break a tool call on this
        log.debug("plugin-voice: owner conversation resolve failed: %s", exc)
        return None


_UI_DIR = Path(__file__).parent / "ui"
_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}


class _ConnectReq(BaseModel):
    # Optional at the schema level so a blank form yields a friendly 400 string
    # instead of FastAPI's 422 array (which UIs render as "[object Object]").
    api_key: str | None = None


class _SettingsReq(BaseModel):
    # Engine knobs — only fields present in the body are applied; None clears.
    rt_voice: str | None = None
    rt_model: str | None = None
    rt_lock_tools_to_owner: bool | None = None
    rt_tools_allow: list[str] | None = None
    rt_tools_deny: list[str] | None = None


class _EnrollReq(BaseModel):
    phrase_index: int
    pcm_b64: str


class _TestVoiceReq(BaseModel):
    pcm_b64: str


class _RtToolReq(BaseModel):
    token: str | None = None
    name: str | None = None
    arguments: dict | None = None


def register_routes(app, ctx):
    router = APIRouter(prefix="/api/p/plugin-voice", tags=["voice"])

    # ---------- vault helpers (resolved at call time, never cached) ----------

    def _vault():
        vault = getattr(ctx, "vault", None)
        if vault is None:
            raise HTTPException(503, "Vault not available")
        return vault

    async def _read(key: str) -> str | None:
        try:
            cred = await _vault().get_credential(key)
        except KeyError:
            return None
        value = (getattr(cred, "value", None) or "").strip()
        return value or None

    async def _settings() -> dict:
        raw = await _read(VAULT_SETTINGS)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    async def _save_settings(settings: dict) -> None:
        await _vault().store_credential(VAULT_SETTINGS, json.dumps(settings), kind="config")

    # ---------- owner-facing API ----------

    @router.post("/connect")
    async def connect(body: _ConnectReq, user=Depends(get_current_user)):
        try:
            return await setup.do_connect(ctx, pasted_key=body.api_key)
        except setup.SetupError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.post("/disconnect")
    async def disconnect(user=Depends(get_current_user)):
        vault = _vault()
        for key in (VAULT_OPENAI_KEY, *LEGACY_VAULT_KEYS):
            try:
                await vault.delete_credential(key)
            except KeyError:
                pass
        return {"connected": False}

    @router.get("/status")
    async def status(user=Depends(get_current_user)):
        try:
            return await setup.build_status(ctx)
        except setup.SetupError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.get("/voices")
    async def voices(user=Depends(get_current_user)):
        # The Realtime voice set is fixed — a static catalog, no upstream call.
        return {"voices": openai_realtime.VOICES, "models": list(openai_realtime.MODELS)}

    @router.get("/settings")
    async def get_settings(user=Depends(get_current_user)):
        return await _settings()

    @router.post("/settings")
    async def post_settings(body: _SettingsReq, user=Depends(get_current_user)):
        changes = body.model_dump(exclude_unset=True)
        settings = await _settings()
        if "rt_voice" in changes:
            voice = (changes["rt_voice"] or "").strip() or None
            if voice and voice not in openai_realtime.VOICE_IDS:
                raise HTTPException(400, f"Unknown voice '{voice}'")
            settings["rt_voice"] = voice
        if "rt_model" in changes:
            model = (changes["rt_model"] or "").strip() or None
            if model and model not in openai_realtime.MODELS:
                raise HTTPException(400, f"Unknown model '{model}'")
            settings["rt_model"] = model
        if "rt_lock_tools_to_owner" in changes:
            settings["rt_lock_tools_to_owner"] = bool(changes["rt_lock_tools_to_owner"])
        for field in ("rt_tools_allow", "rt_tools_deny"):
            if field in changes:
                names = changes[field] or []
                settings[field] = [str(n).strip() for n in names if str(n).strip()]
        await _save_settings(settings)
        return settings

    # ---------- realtime S2S: session minting ----------

    # GET as well as POST: the sidebar widget iframe has cookie auth only (the
    # shell doesn't hand widgets a bearer token), and cookie auth is read-only.
    # Minting a session writes nothing in Luna, so GET is honest.
    # Luna View reaction tool: UI-only — rt-client intercepts it in the
    # browser and animates the dot avatar; it never reaches /rt/tool. Listed
    # here so the talker knows it exists.
    _VIEW_REACT_SCHEMA = {
        "type": "function",
        "name": "luna_view_react",
        "description": (
            "Show a visual reaction on the Luna View avatar. Use SPARINGLY, "
            "only when clearly fitting: heart (affection, thanks), thumb "
            "(agreement, nice work), rocket (launch, shipping something), "
            "arm (strength, 'we got this'), fireworks (a real win worth "
            "celebrating). Never announce that you did it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "shape": {
                    "type": "string",
                    "enum": ["heart", "thumb", "rocket", "arm", "fireworks"],
                    "description": "The reaction to display",
                }
            },
            "required": ["shape"],
        },
    }

    @router.get("/rt/session")
    @router.post("/rt/session")
    async def rt_session(user=Depends(get_current_user)):
        key_res = await openai_realtime.resolve_openai_key(ctx, vault_key=VAULT_OPENAI_KEY)
        if key_res is None:
            raise HTTPException(400, "No OpenAI key — add one in Settings → Voice")

        settings = await _settings()
        pv = persona_config.effective(settings)
        ov = persona_config.overrides_of(settings)
        # The live identity is the source of truth for name AND mission — both
        # go stale in the connect-time snapshot the moment the owner edits them.
        live_id = await identity.live_identity(ctx) or {}
        persona_name = (live_id.get("name") or "").strip() or settings.get("persona_name")
        mission = (live_id.get("mission") or "").strip() or None
        has_imprint = bool(await _read(VAULT_PROFILE))

        instructions = talker.build_instructions(
            persona_name=persona_name,
            greeting=pv["greeting"],
            fillers=pv["fillers"],
            voice_style=ov.get("voice_system_prompt"),
            has_imprint=has_imprint,
            talker_extra=ov.get("talker_extra"),
            persona_brief=settings.get("persona_brief"),
            mission=mission,
        )

        lane2 = broker.knowledge_tools(ctx, settings)
        tools = broker.tool_schemas(lane2) + tasks.synthetic_schemas() + [_VIEW_REACT_SCHEMA]
        model = settings.get("rt_model") or openai_realtime.DEFAULT_MODEL
        voice = settings.get("rt_voice") or openai_realtime.DEFAULT_VOICE
        session_cfg = openai_realtime.session_config(
            instructions=instructions,
            voice=voice,
            model=model,
            tools=tools,
            turn_eagerness=pv.get("turn_eagerness") or "normal",
        )

        rt_client = openai_realtime.RealtimeClient(**{
            k: v for k, v in key_res.items()
            if k in ("api_key", "base_url", "headers", "params") and v
        })
        try:
            minted = await rt_client.mint_client_secret(session_cfg)
        except RealtimeError as exc:
            # 400, not 502: hosted edges replace 5xx JSON bodies with HTML
            # error pages, which hides the actionable message from the widget.
            raise HTTPException(400, str(exc)) from exc
        finally:
            await rt_client.close()

        # One plugin token arms every relay surface for this call (/rt/tool,
        # /rt/events, /live) — realtime calls outlast the 5-minute live TTL.
        rt_token = secrets.token_urlsafe(16)
        live_state.mint_live_token(rt_token, ttl=live_state.RT_TOKEN_TTL)
        live_state.reset_speaker()
        return {
            "client_secret": minted["value"],
            "expires_at": minted.get("expires_at"),
            "webrtc_url": openai_realtime.WEBRTC_CALLS_URL,
            "model": session_cfg["model"],
            "voice": session_cfg["audio"]["output"]["voice"],
            "rt_token": rt_token,
            "live_token": rt_token if has_imprint else None,
            "persona_name": persona_name,
            "has_imprint": has_imprint,
            "tool_names": [t["name"] for t in tools],
        }

    # ---------- realtime S2S: lane-2 tool relay ----------

    @router.post("/rt/tool")
    async def rt_tool(body: _RtToolReq):
        if not live_state.live_token_valid(body.token or ""):
            raise HTTPException(401, "Bad or expired call token")
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(400, "Missing tool name")

        settings = await _settings()
        # Server-authoritative owner lock: the widget's [voice check] context
        # items only inform the talker; this actually refuses.
        speaker = live_state.recent_speaker()
        speaker_label = speaker[0] if speaker else None
        if settings.get("rt_lock_tools_to_owner") and speaker_label == "other":
            return {"ok": False, "error": broker.ERR_OWNER_ONLY}

        # Lane-3 synthetics run here, never through the registry.
        if name == "luna_do":
            return live_state.task_manager().dispatch(
                ctx,
                (body.arguments or {}).get("instruction") or "",
                owner_verified=speaker_label != "other",
                settings=settings,
                conversation_id=await _owner_conversation_id(ctx),
            )
        if name == "luna_task_status":
            return live_state.task_manager().status((body.arguments or {}).get("task_id"))
        if name == "luna_view_react":
            # Normally intercepted client-side; if it lands here (old cached
            # rt-client), succeed quietly — it's a visual, nothing to execute.
            return {"ok": True}

        result = await broker.execute(ctx, name, body.arguments, settings)
        if not result.get("ok"):
            log.info("plugin-voice rt tool %s: %s", name, result.get("error"))
        return result

    # ---------- realtime S2S: lane-3 task events ----------

    @router.websocket("/rt/events")
    async def rt_events(ws: WebSocket):
        token = ws.query_params.get("token") or ""
        if not live_state.live_token_valid(token):
            await ws.close(code=4401)
            return
        try:
            since = int(ws.query_params.get("since") or 0)
        except ValueError:
            since = 0
        await ws.accept()
        tm = live_state.task_manager()
        queue = tm.subscribe()
        try:
            for event in tm.recent_events(since):  # replay, then live
                await ws.send_json(event)
            while True:
                await ws.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            tm.unsubscribe(queue)

    # ---------- voice imprint: enrollment + live speaker check ----------

    def _dsp():
        """numpy fast path when Luna's runtime ships it (local installs),
        pure-Python fallback otherwise (hosted never pip-installs plugin deps)."""
        try:
            from . import dsp as dsp_module
        except ImportError:
            from . import dsp_pure as dsp_module
        return dsp_module

    async def _profile():
        raw = await _read(VAULT_PROFILE)
        if not raw:
            return None, {}
        try:
            data = json.loads(raw)
        except ValueError:
            return None, {}
        emb = data.get("profile")
        return (_dsp().as_vector(emb) if emb else None), data

    @router.get("/enroll")
    async def enroll_status(user=Depends(get_current_user)):
        dsp = _dsp()
        _, data = await _profile()
        done = data.get("enrolled") or []
        return {
            "phrases": dsp.ENROLL_PHRASES,
            "enrolled": done,
            "ready": bool(data.get("profile")),
            "min_required": dsp.MIN_ENROLL,
        }

    @router.post("/enroll")
    async def enroll(body: _EnrollReq, user=Depends(get_current_user)):
        import base64

        dsp = _dsp()
        if not (0 <= body.phrase_index < len(dsp.ENROLL_PHRASES)):
            raise HTTPException(400, "Unknown phrase index")
        try:
            pcm = base64.b64decode(body.pcm_b64 or "")
        except ValueError:
            raise HTTPException(400, "Bad audio payload") from None
        if len(pcm) < 16000:  # <0.5s — surely a misfire
            raise HTTPException(400, "That recording was too short — try again")
        emb = dsp.embed(pcm)
        if emb is None:
            raise HTTPException(400, "Couldn't hear speech in that recording — try again")

        _, data = await _profile()
        embs = data.get("embeddings") or {}
        embs[str(body.phrase_index)] = [float(x) for x in emb]
        enrolled = sorted(int(k) for k in embs)
        profile = None
        personal_threshold = None
        if len(embs) >= dsp.MIN_ENROLL:
            vecs = [dsp.as_vector(v) for v in embs.values()]
            profile = [float(x) for x in dsp.profile_from(vecs)]
            # Personal threshold from leave-one-out self-similarity: the global
            # dojo threshold is tuned on clean TTS and proved too strict for
            # real microphones (owner feedback, 2026-07-05). Anchor on how
            # similar the owner's OWN recordings are to each other.
            loo = [
                dsp.score(dsp.profile_from(vecs[:i] + vecs[i + 1:]), vecs[i])
                for i in range(len(vecs))
            ]
            personal_threshold = max(0.25, min(dsp.effective_threshold(), 0.6 * min(loo)))
        await _vault().store_credential(
            VAULT_PROFILE,
            json.dumps({
                "embeddings": embs,
                "enrolled": enrolled,
                "profile": profile,
                "threshold": personal_threshold,
            }),
            kind="config",
        )
        return {"enrolled": enrolled, "ready": profile is not None}

    @router.delete("/enroll")
    async def enroll_reset(user=Depends(get_current_user)):
        try:
            await _vault().delete_credential(VAULT_PROFILE)
        except KeyError:
            pass
        live_state.reset_speaker()
        return {"enrolled": [], "ready": False}

    @router.websocket("/live")
    async def live_check(ws: WebSocket):
        # Widget iframes carry no bearer token — the rt_token minted by the
        # owner-authed /rt/session gates this socket instead.
        token = ws.query_params.get("token") or ""
        if not live_state.live_token_valid(token):
            await ws.close(code=4401)
            return
        try:
            dsp = _dsp()
        except HTTPException:
            await ws.close(code=4503)
            return
        profile, data = await _profile()
        threshold = float(
            (await _settings()).get("threshold")
            or data.get("threshold")
            or dsp.effective_threshold()
        )
        await ws.accept()
        import base64

        buf = b""
        try:
            while True:
                msg = await ws.receive_json()
                try:
                    buf += base64.b64decode(msg.get("pcm_b64") or "")
                except ValueError:
                    continue
                if len(buf) >= 32000:  # 1s window @16k s16le
                    label, score_ = dsp.verdict(profile, buf[-32000:], threshold)
                    buf = b""
                    if label != "unknown":
                        live_state.set_last_speaker(label, score_)
                    await ws.send_json({"speaker": label, "score": round(score_, 3)})
        except WebSocketDisconnect:
            pass

    @router.post("/enroll/test")
    async def enroll_test(body: _TestVoiceReq, user=Depends(get_current_user)):
        """Record → immediate verdict, so the owner can verify the imprint works."""
        import base64

        dsp = _dsp()
        profile, data = await _profile()
        if profile is None:
            raise HTTPException(400, "Record the imprint phrases first")
        try:
            pcm = base64.b64decode(body.pcm_b64 or "")
        except ValueError:
            raise HTTPException(400, "Bad audio payload") from None
        threshold = float(
            (await _settings()).get("threshold")
            or data.get("threshold")
            or dsp.effective_threshold()
        )
        label, score_ = dsp.verdict(profile, pcm, threshold)
        return {"speaker": label, "score": round(score_, 3), "threshold": round(threshold, 3)}

    @router.post("/refresh-persona")
    async def refresh_persona(user=Depends(get_current_user)):
        """Re-run the personality setup — e.g. after the owner changed the
        agent's personality: new greeting, new fillers, re-matched voice.
        Takes effect on the next minted session."""
        try:
            await setup.resync_persona(ctx)
        except setup.SetupError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return await status(user=user)

    # ---------- Voice Persona settings (every hardcoded knob, editable) ----------

    @router.get("/persona-settings")
    async def get_persona_settings(user=Depends(get_current_user)):
        settings = await _settings()
        return {
            "values": persona_config.effective(settings),
            "overrides": persona_config.overrides_of(settings),
            "defaults": dict(persona_config.DEFAULTS),
            "auto": {
                "greeting": settings.get("greeting"),
                "fillers": settings.get("fillers"),
            },
            "persona_name": await identity.live_name(ctx) or settings.get("persona_name"),
            "rt_voice": settings.get("rt_voice"),
            "turn_eagerness_values": list(persona_config.TURN_EAGERNESS_VALUES),
        }

    @router.put("/persona-settings")
    async def put_persona_settings(request: Request, user=Depends(get_current_user)):
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "Expected a JSON body") from None
        if not isinstance(body, dict):
            raise HTTPException(400, "Expected a JSON object of fields")
        settings = await _settings()
        try:
            settings, changed = persona_config.apply_changes(settings, body)
        except persona_config.PersonaConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
        await _save_settings(settings)
        # Everything applies at the next /rt/session mint — no upstream PATCH.
        return {
            "saved": True,
            "changed": sorted(changed),
            "values": persona_config.effective(settings),
            "overrides": persona_config.overrides_of(settings),
        }

    # ---------- static UI (widget + settings iframe) ----------

    def _serve(base: Path, path: str) -> FileResponse:
        if not path or path == "/":
            path = "index.html"
        target = (base / path).resolve()
        if not str(target).startswith(str(base.resolve())):
            raise HTTPException(403, "Forbidden")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # extension-less paths fall back to the SPA index; missing assets 404
            index = base / "index.html"
            if "." not in target.name and index.is_file():
                return FileResponse(str(index), headers=_NO_CACHE)
            raise HTTPException(404, "Not found")
        return FileResponse(str(target), headers=_NO_CACHE)

    @router.get("/ui/widgets/voice/{path:path}")
    async def widget_ui(path: str = ""):
        return _serve(_UI_DIR / "widgets" / "voice", path)

    @router.get("/ui/settings/{path:path}")
    async def settings_ui(path: str = ""):
        return _serve(_UI_DIR / "settings", path)

    @router.get("/ui/view/{path:path}")
    async def view_ui(path: str = ""):
        return _serve(_UI_DIR / "view", path)

    # Catch-all LAST (FastAPI matches in registration order): today's shell
    # hardcodes plugin pane iframes to /api/p/<plugin>/ui/, so the pane root
    # must also serve Luna View for cores that don't know SidebarSection.path.
    @router.get("/ui/{path:path}")
    async def pane_root_ui(path: str = ""):
        return _serve(_UI_DIR / "view", path)

    app.include_router(router)
