"""Standalone local demo: python server.py (with backend environment and .env loaded)."""

import asyncio
import json
import os
import re
import sys
import uuid
import secrets
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from copy import deepcopy
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
from paths import data_root, load_environment, runtime_connection
load_environment()

from adapters import ProjectAdapter  # noqa: E402 - local read-only adapter requires the project path
from author import Author  # noqa: E402
from store import StoryStore  # noqa: E402
from performance import normalize_segments, recover_split_audio
from production import cached_audio, voice_inventory, voice_lines, voice_targets  # noqa: E402
from stage_states import scene_shots
from video_export import VideoExport  # noqa: E402
from voice_errors import voice_failure
from production_state import story_work
from outfit_policy import OutfitMode, Season, load_seasonal_outfits, season_outfit
from scene_production import apply_scene_outfits


def create_app(store, adapter, api_token=None):
    api_token = api_token or secrets.token_urlsafe(32)
    author = Author(store, adapter)
    exports = VideoExport(store, adapter, author.production)
    deleting_stories = set()
    story_write_locks = {}
    recover_split_audio(store, adapter)

    # Older versions called a text-only story ready. Check local files without making paid requests.
    for brief in store.list():
        doc = store.get(brief["id"])
        if doc["status"] == "ready" and not doc.get("production_version"):
            lines = voice_lines(doc, adapter)
            done = sum(cached_audio(store, adapter, doc["id"], line) is not None for line in lines)
            for scene in doc["scenes"]:
                outfits = scene.setdefault("outfits", {})
                for cid in doc["outline"]["cast"]:
                    character = adapter.characters.get(cid)
                    default = getattr(character, "default_outfit", None)
                    if default and hasattr(adapter, "outfit") and adapter.outfit(cid, default):
                        outfits.setdefault(cid, default)
            doc["production"] = dict(voices_done=done, voices_total=len(lines), staging_done=True)
            if done == len(lines):
                doc["production_version"] = 1
            else:
                doc.update(status="interrupted", phase="剧本已保存，继续准备语音与演出", error=None)
            store.save(doc)

    @asynccontextmanager
    async def lifespan(app):
        yield
        for sid in list(exports.tasks):
            await exports.stop_story(sid.split('-scene-')[0])
        await author.close()
        await adapter.close()

    app = FastAPI(title="怪文书 Demo", lifespan=lifespan)
    app.state.author = author
    app.state.exports = exports
    app.state.api_token = api_token

    # A service restart can interrupt an audio-only job without changing the
    # story's ready/tuning state. Make that job explicitly retryable.
    for brief in store.list():
        document = store.get(brief['id'])
        if (document.get('voice_job') or {}).get('status') == 'working':
            document['voice_job'].update(status='interrupted', message='服务重启，已完成配音保留，可继续补齐')
            store.save(document)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if not hmac.compare_digest(request.headers.get('x-kaibun-token', ''), api_token):
            return JSONResponse({'detail': '访问验证失败，请从本机页面操作'}, status_code=403)
        origin = request.headers.get("origin")
        if request.method == "POST" and origin and origin not in ("http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3010", "http://127.0.0.1:3010"):
            return JSONResponse({"detail": "请从本机页面操作"}, status_code=403)
        if request.method == 'POST':
            match = re.fullmatch(r'/stories/([a-f0-9]{32})/(.+)', request.url.path)
            if match:
                sid, action = match.groups()
                current = store.get(sid)
                if current and action not in {'stop', 'resume', 'delete', 'pin', 'rename'} and not action.startswith('audio/'):
                    try:
                        payload = await request.json()
                    except ValueError:
                        return JSONResponse({'detail': '请求内容不是有效JSON'}, status_code=422)
                    expected = payload.get('base_fingerprint') if isinstance(payload, dict) else None
                    if expected:
                        from revisions import fingerprint
                        if expected != fingerprint(current):
                            return JSONResponse({'detail': '剧本已更新，请刷新后重新操作'}, status_code=409)
                safe = {'stop', 'voices/stop', 'resume', 'production/retry', 'tune', 'tune-apply', 'tune-discard', 'tune-undo', 'rename', 'pin', 'delete'}
                if current and action not in {'export', 'export/stop', 'rename', 'pin', 'delete'} and not action.startswith('audio/') and is_exporting(sid):
                    return JSONResponse({'detail': '请先完成或暂停视频导出'}, status_code=409)
                if current and action not in safe and not action.startswith('audio/') and (author.busy(sid) or current.get('revision_proposal')):
                    if sid in author.pipelines and sid not in author.tasks:
                        return JSONResponse({'detail': '写作已暂停，配音与演出仍在继续；请等待完成后调整'}, status_code=409)
                    return JSONResponse({'detail': '请先完成当前写作或制作，并处理待确认的修改预览'}, status_code=409)
        return await call_next(request)

    @app.middleware('http')
    async def serialize_story_writes(request: Request, call_next):
        match = re.match(r'/stories/([a-f0-9]{32})(?:/|$)', request.url.path)
        if match:
            sid = match[1]
            if sid in deleting_stories:
                return JSONResponse({'detail': '故事正在删除，请稍候'}, status_code=409)
            if request.method == 'POST':
                async with story_write_locks.setdefault(sid, asyncio.Lock()):
                    return await call_next(request)
        return await call_next(request)

    def story_or_404(id):
        if id in deleting_stories:
            raise HTTPException(409, '故事正在删除，请稍候')
        if not re.fullmatch(r"[a-f0-9]{32}", id):
            raise HTTPException(404, "没有这篇故事")
        doc = store.get(id)
        if doc is None:
            raise HTTPException(404, "没有这篇故事")
        return doc

    def presentation(doc):
        result = deepcopy(doc)
        from production_state import normalize_recovered
        from revisions import fingerprint
        result['base_fingerprint'] = fingerprint(doc)
        result['revision_history_count'] = len(doc.get('revision_history') or [])
        result.pop('revision_history', None)
        result['revision_undo'] = True if doc.get('revision_undo') else None
        if isinstance(result.get('production'), dict):
            # Readiness was recorded under the old pronunciation/cache rules.
            # Recheck files rather than claiming all voices still exist.
            targets, ready = voice_inventory(store, adapter, doc)
            result['production'].update(voices_done=len(ready), voices_total=len(targets))
            normalize_recovered(result)
        result['work'] = work_for(result)
        for scene in result["scenes"]:
            shots = scene_shots(scene, (result.get('outline') or {}).get('cast', []), doc['id'])
            for line in scene['lines']:
                line['shot'] = shots[line['id']]
                line['voice_targets'] = {target['speaker']: target['id'] for target in voice_targets(line, adapter)}
                if line['shot']['thought']:
                    line['kind'] = 'thought'
            scene["wardrobe"] = {cid: outfit for cid, oid in scene.get("outfits", {}).items()
                                 if (outfit := adapter.outfit(cid, oid)) is not None}
            scene["wardrobe_changes"] = {
                lid: {cid: outfit for cid, oid in changes.items() if (outfit := adapter.outfit(cid, oid)) is not None}
                for lid, changes in scene.get("outfit_changes", {}).items()}
            if hasattr(adapter, "backgrounds"):
                scene["background_asset"] = adapter.backgrounds.card(scene.get("background"))
                scene["background_change_assets"] = {lid: adapter.backgrounds.card(bid)
                                                      for lid, bid in scene.get("background_changes", {}).items()}
        return result

    def is_exporting(sid):
        return any(job.get('status') == 'working' for key, job in exports.jobs.copy().items() if key == sid or key.startswith(sid+'-scene-'))

    def work_for(doc):
        return story_work(doc, busy=author.busy(doc['id']), exporting=is_exporting(doc['id']))

    def scoped_export(doc, scene):
        if scene is None: return doc
        if not 0 <= scene < len(doc['scenes']):
            raise HTTPException(422, '没有这一幕')
        result = deepcopy(doc)
        result['scenes'] = [result['scenes'][scene]]
        result['export_id'] = doc['id'] + '-scene-' + str(scene)
        return result

    @app.post('/stories/{id}/export')
    async def start_export(id: str, scene: int | None = None):
        doc = scoped_export(story_or_404(id), scene)
        if doc.get('status') != 'ready':
            raise HTTPException(409, '请先完成故事的语音与演出')
        if any(line.get('imported_untranslated') for s in doc['scenes'] for line in s['lines']):
            raise HTTPException(422, '导入内容尚未整理为双语台词')
        if any(line.get('performance_invalidated') for s in doc['scenes'] for line in s['lines']):
            raise HTTPException(422, '请先检查文字变化后失效的演出区间，并在微调中保存确认')
        return exports.start(doc)

    @app.get('/stories/{id}/export-check')
    async def export_check(id: str, scene: int | None = None):
        doc = scoped_export(story_or_404(id), scene)
        targets, ready = voice_inventory(store, adapter, doc)
        missing = [line['id'] for line in targets if line['id'] not in ready]
        invalidated = [line['id'] for s in doc['scenes'] for line in s['lines'] if line.get('performance_invalidated')]
        untranslated = [line['id'] for s in doc['scenes'] for line in s['lines'] if line.get('imported_untranslated')]
        return dict(missing_audio=missing, performance_review=invalidated, untranslated=untranslated, voice_total=len(targets), ready=doc['status'] == 'ready' and not untranslated and not invalidated)

    @app.post('/stories/{id}/export/stop')
    async def stop_export(id: str, scene: int | None = None):
        doc = scoped_export(story_or_404(id), scene)
        id = doc.get("export_id", id)
        task = exports.tasks.get(id)
        if task and not task.done():
            exports.cancel_events[id].set()
            await asyncio.gather(task, return_exceptions=True)
            exports.jobs[id] = {'status': 'interrupted', 'progress': 0, 'message': '导出已暂停，可以继续'}
        return exports.status(doc)

    @app.get('/stories/{id}/export')
    async def export_status(id: str, scene: int | None = None):
        return exports.status(scoped_export(story_or_404(id), scene))

    @app.get('/stories/{id}/export/download')
    async def download_export(id: str, scene: int | None = None):
        doc = scoped_export(story_or_404(id), scene)
        path = exports.output_path(doc)
        if not exports.cached(doc):
            raise HTTPException(404, '视频尚未生成，请先导出')
        return FileResponse(path, media_type='video/mp4', filename=(doc.get('title') or '故事') + '.mp4')

    class NewStory(BaseModel):
        premise: str = Field(min_length=2, max_length=2000)
        cast: list[str] = Field(default_factory=list)
        length: Literal["short", "standard", "half_hour", "hour_plus"] = "short"
        details: str = Field(default="", max_length=12000)
        outfits: dict[str, str] = Field(default_factory=dict)
        outfit_mode: OutfitMode = "default"
        tuning: bool = False

    @app.get("/health")
    async def health():
        return {"ok": True, "model": adapter.model, "voice": bool(adapter.tts_key),
                "background_model": getattr(getattr(adapter, "background_selector", None), "model", None)}

    @app.get("/characters")
    async def characters():
        return adapter.catalog()

    @app.get("/backgrounds")
    async def backgrounds():
        return adapter.backgrounds.list()

    class CustomBackground(BaseModel):
        image: str = Field(min_length=1, max_length=14000000)
        label: str = Field(min_length=1, max_length=80)
        description: str = Field(default='', max_length=700)
        tags: list[str] = Field(default_factory=list, max_length=20)
        time_of_day: Literal['day', 'dusk', 'night', 'unknown'] = 'unknown'

    @app.post('/backgrounds/import')
    async def import_background(body: CustomBackground):
        import base64
        from backgrounds import BackgroundError
        try:
            data = base64.b64decode(body.image, validate=True)
            async with adapter.backgrounds._index_lock:
                return adapter.backgrounds.import_image(data, body.label, body.description, body.tags, body.time_of_day)
        except (ValueError, BackgroundError) as error:
            raise HTTPException(422, str(error) if isinstance(error, BackgroundError) else '图片编码无效') from None

    @app.get("/backgrounds/{bid}/{kind}")
    async def background_image(bid: str, kind: str):
        if kind not in ("image", "thumbnail"):
            raise HTTPException(404, "没有这个背景素材")
        path = adapter.backgrounds.image_path(bid, thumbnail=kind == "thumbnail")
        if path is None:
            raise HTTPException(404, "背景尚未下载")
        return FileResponse(path, headers={"Cache-Control": "public,max-age=31536000,immutable"})

    @app.get("/characters/{cid}/outfits")
    async def outfits(cid: str):
        if cid not in adapter.characters:
            raise HTTPException(404, "没有这个人物")
        return adapter.outfits(cid)

    @app.get("/wardrobe/{category}/{number}.png")
    async def outfit_thumbnail(category: str, number: str, request: Request):
        if category not in ("stand", "stand_costume") or not re.fullmatch(r"\d{10}", number):
            raise HTTPException(404, "没有这套服装")
        try:
            oid = category + "/" + number
            path = adapter.thumbnail_path(oid) or await adapter.ensure_thumbnail(oid)
        except ValueError:
            raise HTTPException(404, "没有这套服装") from None
        except Exception:
            raise HTTPException(503, "服装图片暂时未准备好，请重试") from None
        stat = path.stat()
        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        headers = {"Cache-Control": "public,max-age=31536000,immutable", "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, media_type="image/png", headers=headers)

    @app.get("/characters/{cid}/{kind}")
    async def character_image(cid: str, kind: str):
        c = adapter.characters.get(cid)
        file = getattr(c, kind, None) if c and kind in ("avatar", "background") else None
        if not file or not Path(file).is_file():
            raise HTTPException(404, "没有这个素材")
        return FileResponse(file, headers={"Cache-Control": "public,max-age=86400"})

    @app.get("/stand/{category}/{outfit}/{filename}")
    async def stand(category: str, outfit: str, filename: str):
        if category not in ("stand", "stand_costume") or not re.fullmatch(r"\d{10}", outfit) or not re.fullmatch(r"[\w-]+\.(json|atlas|png)", filename):
            raise HTTPException(404, "没有这个素材")
        file = adapter.asset_root / "spine" / "idols" / category / outfit / filename
        if not file.is_file():
            raise HTTPException(404, "没有这个素材")
        return FileResponse(file, headers={"Cache-Control": "public,max-age=86400"})

    @app.get("/stories")
    async def list_stories():
        return await asyncio.to_thread(store.list)

    @app.post("/stories", status_code=202)
    async def create(body: NewStory):
        if any(cid not in adapter.characters for cid in body.cast) or not body.premise.strip():
            raise HTTPException(422, "请检查想法和出场人物")
        if any(cid not in body.cast or adapter.outfit(cid, oid) is None for cid, oid in body.outfits.items()):
            raise HTTPException(422, "请选择对应人物可用的服装")
        if len(author.tasks) >= 3:
            raise HTTPException(409, "还有故事正在创作，稍后再试")
        doc = store.create(body.premise.strip(), list(dict.fromkeys(body.cast)), body.length, body.details.strip(), body.outfits, body.outfit_mode)
        if body.tuning:
            doc["tuning"] = "outline"
            store.save(doc)
        author.enqueue(doc["id"])
        return doc

    @app.get("/stories/{id}")
    async def get_story(id: str):
        # Whole-story checks must not hold the event loop that serves playback.
        return await asyncio.to_thread(lambda: presentation(story_or_404(id)))

    from revisions import install_revision_routes
    install_revision_routes(app, store, adapter, author, exports, story_or_404, presentation)
    from voice_jobs import install_voice_routes
    install_voice_routes(app, store, adapter, author, exports, story_or_404)

    class EditLine(BaseModel):
        zh: str = Field(min_length=1, max_length=1600)
        ja: str | None = Field(default=None, max_length=1200)
        speaker: str | None = None
        speakers: list[str] | None = None
        voice_style: Literal["natural", "idol"] | None = None
        nichika_voice_style: Literal['natural', 'idol'] | None = None
        emotion: Literal["calm", "happy", "shy", "surprised", "sad", "angry", "thinking"] | None = None
        body_mood: Literal["calm", "happy", "shy", "surprised", "sad", "angry", "thinking"] | None = None
        expression: str | None = None
        body_action: str | None = None
        mouth_action: str | None = None
        rest_mouth_action: str | None = None
        actor_states: dict[str, dict[str, str | None]] | None = None
        listener_state_persist: bool | None = None
        onstage: list[str] | None = None
        screen_cast: list[str] | None = None
        actor_order: list[str] | None = None
        performance_segments: list[dict] | None = None
        edit_token: str | None = None
        regenerate: bool = False

    @app.post("/stories/{id}/lines/{line_id}/fine-tune")
    async def fine_tune_line(id: str, line_id: str, body: EditLine):
        doc = story_or_404(id)
        if author.busy(id) or any(job.get("status") == "working" for key, job in exports.jobs.items() if key == id or key.startswith(id + "-scene-")):
            raise HTTPException(409, "请先停止当前制作或导出")
        line = next((line for scene in doc["scenes"] for line in scene["lines"] if line["id"] == line_id), None)
        if line is None:
            raise HTTPException(404, "没有这句台词")
        previous_voice = [(target["id"], adapter.voice_signature(target)[0]) for target in voice_lines(doc, adapter) if target["id"] == line_id or target["id"].startswith(line_id + "~")]
        if body.edit_token and line.get('last_fine_edit_token') == body.edit_token:
            return presentation(doc)
        if not body.zh.strip() or body.ja is not None and not body.ja.strip():
            raise HTTPException(422, "台词不能为空")
        line["zh"] = body.zh.strip()
        if body.listener_state_persist is not None:
            line['listener_state_persist'] = body.listener_state_persist
        if body.ja is not None:
            line["ja"] = body.ja.strip()
        if body.voice_style is not None and line["speaker"] == "fuyuko":
            line["voice_style"] = body.voice_style
        if body.emotion is not None:
            line["emotion"] = body.emotion
        from visual_fields import expression_valid, body_valid, actor_state, mouth_valid
        for field,check in (('expression',expression_valid),('body_action',body_valid),('mouth_action',mouth_valid),('rest_mouth_action',mouth_valid)):
            if field in body.model_fields_set:
                value=getattr(body,field)
                if not check(value): raise HTTPException(422,'未知表情或动作')
                line[field]=value
        if 'body_mood' in body.model_fields_set:
            if body.body_mood is None:
                line.pop('body_mood', None)
            else:
                line['body_mood'] = body.body_mood
        if body.actor_order is not None:
            if any(cid not in doc['outline']['cast'] for cid in body.actor_order):
                raise HTTPException(422, '角色排序无效')
            line['actor_order'] = list(dict.fromkeys(body.actor_order))
        if body.screen_cast is not None:
            if len(body.screen_cast) > 7 or any(cid not in doc['outline']['cast'] for cid in body.screen_cast):
                raise HTTPException(422, '请选择本故事内的至多七位角色')
            line['screen_cast'] = list(dict.fromkeys(body.screen_cast))
        if body.onstage is not None:
            if any(cid not in adapter.characters for cid in body.onstage):
                raise HTTPException(422, '出场人物包含无效角色')
            line['onstage'] = list(dict.fromkeys(body.onstage))
        if body.actor_states is not None:
            states = {}
            for cid, state in body.actor_states.items():
                if cid not in adapter.characters or not isinstance(state, dict):
                    raise HTTPException(422, '角色动作设置无效')
                try: states[cid] = actor_state(state)
                except ValueError as error: raise HTTPException(422,str(error)) from None
            line['actor_states'] = states
        if body.performance_segments is not None:
            try:
                line['performance_segments'] = normalize_segments(body.performance_segments, line, doc['outline']['cast'])
                line.pop('performance_invalidated', None)
            except ValueError as error:
                raise HTTPException(422, str(error)) from None
        if body.speakers is not None:
            people = list(dict.fromkeys(body.speakers))
            allowed = set(doc['outline']['cast']) | {'producer'}
            if not people or len(people) > 7 or any(cid not in allowed for cid in people):
                raise HTTPException(422, '请选择1至7位本篇发言人物')
            line['speaker'] = people[0]
            if len(people) > 1:
                if 'producer' in people: raise HTTPException(422, '多人同声请选择偶像')
                line['speakers'] = people
                selected = list(line.get('screen_cast') or [])
                line['screen_cast'] = list(dict.fromkeys(people + selected))[:7]
            else:
                line.pop('speakers', None)
        # Commit the edit before voice generation: a provider failure must not lose it.
        if body.voice_style is not None and line['speaker'] == 'fuyuko':
            line['voice_style'] = body.voice_style
        if body.nichika_voice_style is not None and 'nichika' in (line.get('speakers') or [line['speaker']]):
            line['nichika_voice_style'] = body.nichika_voice_style
        targets = voice_targets(line, adapter)
        current_voice = [(target['id'], adapter.voice_signature(target)[0]) for target in targets]
        if previous_voice != current_voice:
            line['audio_revision'] = uuid.uuid4().hex
        store.save(doc)
        if body.regenerate:
            try:
                results = await asyncio.gather(*(author.production.produce_voice(id, target) for target in targets), return_exceptions=True)
                if error := next((value for value in results if isinstance(value, Exception)), None):
                    raise error
            except Exception as error:
                from revisions import fingerprint
                return JSONResponse(status_code=502, content={
                    'detail': '修改已保存；' + voice_failure(error) + '。原有配音文件保留。',
                    'saved_fingerprint': fingerprint(doc),
                })
        if body.edit_token:
            line['last_fine_edit_token'] = body.edit_token
        store.save(doc)
        return presentation(doc)

    @app.post('/stories/{id}/lines/{line_id}/delete')
    async def delete_fine_line(id: str, line_id: str):
        doc = story_or_404(id)
        if author.busy(id) or any(job.get('status') == 'working' for key, job in exports.jobs.items() if key == id or key.startswith(id + '-scene-')):
            raise HTTPException(409, '请先停止当前制作或导出')
        scene = next((s for s in doc['scenes'] if any(l['id'] == line_id for l in s['lines'])), None)
        if scene is None:
            raise HTTPException(404, '没有这句台词')
        if len([l for l in scene['lines'] if l['speaker'] != 'narrator']) <= 1:
            raise HTTPException(422, '每幕至少保留一句对白')
        scene['lines'] = [l for l in scene['lines'] if l['id'] != line_id]
        for key in ('outfit_changes', 'background_changes', 'line_transitions', 'line_transition_lengths'):
            scene.get(key, {}).pop(line_id, None)
        store.save(doc)
        return presentation(doc)

    class OrderLines(BaseModel):
        line_id: str
        action: Literal["up", "down", "delete", "insert"]
        speakers: list[str] = Field(default_factory=list)

    class OrderScene(BaseModel):
        action: Literal["up", "down", "delete", "insert"]

    @app.post("/stories/{id}/scenes/{index}/reorder")
    async def change_scene_order(id: str, index: int, body: OrderScene):
        doc = story_or_404(id)
        if doc.get("status") != "tuning" or doc.get("tuning") != "script" or author.busy(id):
            raise HTTPException(409, "请先完成剧本生成")
        if not 0 <= index < len(doc["scenes"]):
            raise HTTPException(404, "没有这一幕")
        scenes, plans = doc["scenes"], doc["outline"]["scenes"]
        if body.action == "delete":
            if len(scenes) == 1:
                raise HTTPException(422, "至少保留一幕")
            scenes.pop(index)
            plans.pop(index)
        elif body.action == "insert":
            if len(scenes) >= 64:
                raise HTTPException(422, "已达到场数上限")
            scene_index = max((scene["index"] for scene in scenes), default=-1) + 1
            scenes.insert(index + 1, dict(index=scene_index, title="", place="新场景", memory="", lines=[dict(id=f"{scene_index}-n{uuid.uuid4().hex[:16]}", speaker="producer", ja="……", zh="……", emotion="calm", kind="dialogue")]))
            plans.insert(index + 1, dict(place="新场景", direction="新场景"))
        else:
            target = index + (-1 if body.action == "up" else 1)
            if 0 <= target < len(scenes):
                scenes[index], scenes[target] = scenes[target], scenes[index]
                plans[index], plans[target] = plans[target], plans[index]
        doc["story_memory"] = scenes[-1].get("memory", "")
        store.save(doc)
        return presentation(doc)

    @app.post("/stories/{id}/scenes/{index}/lines")
    async def change_scene_lines(id: str, index: int, body: OrderLines):
        doc = story_or_404(id)
        if doc.get("status") != "tuning" or doc.get("tuning") != "script" or author.busy(id):
            raise HTTPException(409, "请先完成剧本生成")
        if not 0 <= index < len(doc["scenes"]):
            raise HTTPException(404, "没有这一幕")
        scene = doc["scenes"][index]
        lines = scene["lines"]
        pos = next((i for i, line in enumerate(lines) if line["id"] == body.line_id), None)
        if pos is None:
            raise HTTPException(404, "没有这句台词")
        if body.action == "delete":
            if len(lines) == 1:
                raise HTTPException(422, "每幕至少保留一句")
            removed = lines.pop(pos)["id"]
            for key in ("outfit_changes", "background_changes"):
                scene.get(key, {}).pop(removed, None)
        elif body.action == "insert":
            if len(lines) >= 100:
                raise HTTPException(422, "本幕已达到台词数量上限")
            people = list(dict.fromkeys(body.speakers)) or ['producer']
            allowed = {*doc['outline']['cast'], 'producer', *(g['id'] for g in doc['outline']['guests'])}
            if any(cid not in allowed for cid in people) or len(people) > 7 or (len(people) > 1 and any(cid not in doc['outline']['cast'] for cid in people)):
                raise HTTPException(422, '多人同声请选择2至7位本篇偶像')
            new_line = dict(id=f"{index}-n{uuid.uuid4().hex[:16]}", speaker=people[0], ja="……", zh="……", emotion="calm", kind="dialogue")
            if len(people) > 1: new_line['speakers'] = people
            lines.insert(pos + 1, new_line)
        else:
            target = pos + (-1 if body.action == "up" else 1)
            if 0 <= target < len(lines):
                lines[pos], lines[target] = lines[target], lines[pos]
        store.save(doc)
        return presentation(doc)

    @app.post("/stories/{id}/lines/{line_id}")
    async def edit_line(id: str, line_id: str, body: EditLine):
        doc = story_or_404(id)
        if doc.get("status") != "tuning" or doc.get("tuning") != "script":
            raise HTTPException(409, "请先完成剧本生成")
        line = next((line for scene in doc["scenes"] for line in scene["lines"] if line["id"] == line_id), None)
        if line is None:
            raise HTTPException(404, "没有这句台词")
        if not body.zh.strip():
            raise HTTPException(422, "请输入中文台词")
        previous_line = deepcopy(line)
        if body.speaker is not None:
            allowed = {*doc["outline"]["cast"], "producer", *(guest["id"] for guest in doc["outline"]["guests"])}
            if body.speaker not in allowed:
                raise HTTPException(422, "请选择本篇人物")
            if body.speaker != line["speaker"]:
                line.pop("voice_style", None)
                line.pop("speakers", None)
            line["speaker"] = body.speaker
        if body.speakers is not None:
            people = list(dict.fromkeys(body.speakers))
            if len(people) < 2 or len(people) > 7 or any(cid not in doc['outline']['cast'] for cid in people):
                raise HTTPException(422, "多人同声请选择2至7位本篇偶像")
            line['speakers'] = people
            line['speaker'] = people[0]
        if body.ja is not None:
            if not body.ja.strip():
                raise HTTPException(422, "请输入日语台词")
            line["ja"] = body.ja.strip()
        line["zh"] = body.zh.strip()
        from revisions import reconcile_performance, rebuild_memory
        line.pop('imported_untranslated', None)
        reconcile_performance(previous_line, line, 'preserve')
        rebuild_memory(doc)
        store.save(doc)
        return presentation(doc)

    @app.post("/stories/{id}/tune-next")
    async def tune_next(id: str):
        doc = story_or_404(id)
        if doc.get("status") != "tuning" or author.busy(id) or doc.get("revision_progress", {}).get("status") == "working":
            raise HTTPException(409, "请等待当前调整完成")
        if doc.get('tuning') == 'script' and any(l.get('imported_untranslated') for scene in doc['scenes'] for l in scene['lines']):
            raise HTTPException(422, '导入内容尚未整理为双语台词，请先生成并应用修改预览，或逐句编辑日文和中文')
        doc["tuning"] = "script" if doc.get("tuning") == "outline" else "production"
        doc.update(status="queued", phase="准备生成", error=None)
        store.save(doc)
        author.enqueue(id)
        return presentation(doc)

    class SceneTransition(BaseModel):
        transition: Literal["cut", "pause", "fade"]
        duration: Literal["short", "medium", "long"] = "short"

    @app.post("/stories/{id}/scenes/{index}/transition")
    async def set_transition(id: str, index: int, body: SceneTransition):
        doc = story_or_404(id)
        if author.busy(id): raise HTTPException(409, "请等待当前任务完成")
        if not 0 <= index < len(doc['outline']['scenes']): raise HTTPException(404, "没有这一幕")
        doc['outline']['scenes'][index]['transition'] = body.transition
        doc['outline']['scenes'][index]['transition_length'] = body.duration
        if index < len(doc['scenes']):
            doc['scenes'][index]['transition'] = body.transition
            doc['scenes'][index]['transition_length'] = body.duration
        store.save(doc)
        return presentation(doc)

    @app.post("/stories/{id}/scenes/{index}/lines/{line_id}/transition")
    async def set_line_transition(id: str, index: int, line_id: str, body: SceneTransition):
        doc = story_or_404(id)
        if author.busy(id): raise HTTPException(409, "请等待当前任务完成")
        if not 0 <= index < len(doc['scenes']) or not any(l['id'] == line_id for l in doc['scenes'][index]['lines']): raise HTTPException(404, "没有这句台词")
        doc['scenes'][index].setdefault('line_transitions', {})[line_id] = body.transition
        doc['scenes'][index].setdefault('line_transition_lengths', {})[line_id] = body.duration
        store.save(doc)
        return presentation(doc)

    class SceneBackground(BaseModel):
        background: str | None

    @app.post("/stories/{id}/scenes/{index}/background")
    async def set_scene_background(id: str, index: int, body: SceneBackground):
        doc = story_or_404(id)
        if id in author.tasks:
            raise HTTPException(409, "请先停止当前创作")
        if not 0 <= index < len(doc["scenes"]):
            raise HTTPException(404, "没有这一幕")
        if body.background is not None and adapter.backgrounds.card(body.background) is None:
            raise HTTPException(422, "请选择已下载的背景")
        doc["scenes"][index]["background"] = body.background
        doc["scenes"][index]["background_changes"] = {}
        store.save(doc)
        return presentation(doc)

    class SceneOutfit(BaseModel):
        character: str
        outfit: str
        apply_later: bool = False

    @app.post("/stories/{id}/scenes/{index}/outfit")
    async def set_scene_outfit(id: str, index: int, body: SceneOutfit):
        doc = story_or_404(id)
        if author.busy(id):
            raise HTTPException(409, "请先停止当前制作")
        if not 0 <= index < len(doc['scenes']) or body.character not in doc['outline']['cast'] or not adapter.outfit(body.character, body.outfit):
            raise HTTPException(422, "请选择本故事人物的有效服装")
        apply_scene_outfits(doc, index, {body.character: body.outfit}, body.apply_later)
        store.save(doc)
        return presentation(doc)

    class SceneSeason(BaseModel):
        season: Season
        apply_later: bool = True

    @app.post("/stories/{id}/scenes/{index}/outfit-season")
    async def set_scene_season(id: str, index: int, body: SceneSeason):
        doc = story_or_404(id)
        if author.busy(id):
            raise HTTPException(409, "请先停止当前制作")
        if not 0 <= index < len(doc['scenes']) or not doc.get('outline'):
            raise HTTPException(404, "没有这一幕")
        seasonal = load_seasonal_outfits()
        choices, skipped = {}, []
        for cid in doc['outline']['cast']:
            if oid := season_outfit(seasonal, adapter.outfit, cid, body.season):
                choices[cid] = oid
            else:
                skipped.append(cid)
        if choices:
            apply_scene_outfits(doc, index, choices, body.apply_later)
            store.save(doc)
        return dict(story=presentation(doc), applied=list(choices), skipped=skipped)

    class PinStory(BaseModel):
        pinned: bool

    class RenameStory(BaseModel):
        title: str = Field(min_length=1, max_length=2000)

    @app.post("/stories/{id}/rename")
    async def rename_story(id: str, body: RenameStory):
        story_or_404(id)
        title = body.title.strip()
        if not title:
            raise HTTPException(422, "请输入名称")
        return store.brief(store.rename(id, title))

    @app.post("/stories/{id}/pause-for-edit")
    async def pause_for_edit(id: str):
        doc = story_or_404(id)
        if not doc.get("outline") or len(doc["scenes"]) < len(doc["outline"]["scenes"]):
            raise HTTPException(409, "请先完成剧本")
        if author.busy(id):
            await author.stop(id, production=True)
        return presentation(story_or_404(id))

    @app.post("/stories/{id}/revise")
    async def revise_story(id: str):
        source = story_or_404(id)
        if not source.get("outline") or len(source["scenes"]) < len(source["outline"]["scenes"]):
            raise HTTPException(409, "请先完成剧本")
        if author.busy(id):
            await author.stop(id, production=True)
            source = story_or_404(id)
        title = source.get("title", source["premise"]) + "·改"
        doc = store.create(source["premise"], source.get("requested_cast", []), source["length"], source.get("details", ""), source.get("requested_outfits", {}), source.get("outfit_mode", "story"))
        doc.update(title=title, custom_title=title, outline=deepcopy(source["outline"]), scenes=deepcopy(source["scenes"]),
                   story_memory=source.get("story_memory", ""), tuning="script", status="tuning", phase="调整剧本", revision_of=id, production=None, production_version=None, revision_epoch=0)
        for key in ('outfit_profile', 'outfit_scope_start', 'continuation_direction'):
            if key in source:
                doc[key] = source[key]
        store.save(doc)
        for target in voice_lines(doc, adapter):
            cached_audio(store, adapter, doc['id'], target)
        return presentation(doc)

    @app.post("/stories/{id}/pin")
    async def pin_story(id: str, body: PinStory):
        story_or_404(id)
        return store.brief(store.pin(id, body.pinned))

    @app.post("/stories/{id}/delete")
    async def delete_story(id: str):
        story_or_404(id)
        deleting_stories.add(id)
        try:
            await author.stop(id, production=True)
            await exports.stop_story(id)
            # Materialize exact matching voices in surviving revision copies
            # before removing the source, including copies made by older code.
            for brief in store.list():
                if brief['id'] == id:
                    continue
                doc = store.get(brief['id'])
                ancestor = doc
                visited = set()
                while ancestor and ancestor.get('revision_of') and ancestor['revision_of'] not in visited:
                    source = ancestor['revision_of']
                    visited.add(source)
                    if source == id:
                        for line in voice_lines(doc, adapter):
                            cached_audio(store, adapter, doc['id'], line)
                        break
                    ancestor = store.get(source)
            result = store.delete(id)
            return {"id": id, "deleted": True, **result}
        except OSError:
            raise HTTPException(409, '文件清理未完成，故事记录仍保留。请关闭正在播放的视频或占用文件的程序后重试删除') from None
        finally:
            deleting_stories.discard(id)

    @app.post("/stories/{id}/resume", status_code=202)
    async def resume(id: str):
        doc = story_or_404(id)
        if id in author.tasks:
            return presentation(doc)
        progress = doc.get('revision_progress') or {}
        if doc.get('revision_request') and progress.get('status') in ('failed', 'interrupted'):
            doc['status'] = 'tuning'
            store.save(doc)
            return await app.state.resume_revision(id)
        if doc.get('tuning') in ('outline', 'script') and doc.get('status') in ('interrupted', 'failed') and doc.get('outline') and (doc['tuning'] == 'outline' or len(doc['scenes']) >= len(doc['outline']['scenes'])):
            doc.update(status='tuning', phase='已恢复编辑，可以继续调整', error=None)
            store.save(doc)
            return presentation(doc)
        if doc["status"] != "ready":
            if len(author.tasks) >= 3 and id not in author.tasks:
                raise HTTPException(409, "稍后再试")
            author.enqueue(id)
        return doc

    @app.post("/stories/{id}/stop")
    async def stop(id: str):
        doc = story_or_404(id)
        if work_for(doc)['actions']['pause_writing'] or (doc.get('revision_progress') or {}).get('status') == 'working':
            await author.stop(id)
        return presentation(story_or_404(id))

    class RetryProduction(BaseModel):
        scope: Literal['staging','voices','all']='staging'

    @app.post('/stories/{id}/production/retry',status_code=202)
    async def retry_production(id:str,body:RetryProduction):
        story_or_404(id)
        if any(job.get('status')=='working' for key,job in exports.jobs.items() if key==id or key.startswith(id+'-scene-')):
            raise HTTPException(409,'请先暂停视频导出')
        try:doc=author.retry_production(id,body.scope)
        except ValueError as error:raise HTTPException(409,str(error)) from None
        return presentation(doc)

    class ContinueStory(BaseModel):
        instruction: str = Field(min_length=1, max_length=2000)
        cast: list[str] | None = None
        outfits: dict[str, str] | None = None
        length: Literal["short", "standard", "half_hour", "hour_plus"] | None = None
        outfit_mode: OutfitMode | None = None
        tuning: bool = False

    @app.post("/stories/{id}/continue", status_code=202)
    async def extend(id: str, body: ContinueStory):
        doc = story_or_404(id)
        if not doc.get("outline") or len(doc["scenes"]) < len(doc["outline"]["scenes"]):
            raise HTTPException(409, "请先完成剧本")
        if author.busy(id):
            await author.stop(id, production=True)
            doc = story_or_404(id)
        if body.cast is not None:
            if any(cid not in adapter.characters for cid in body.cast):
                raise HTTPException(422, "请选择有效人物")
            doc["requested_cast"] = list(dict.fromkeys(body.cast))
        if body.outfits is not None:
            if any(cid not in doc["requested_cast"] or adapter.outfit(cid, oid) is None for cid, oid in body.outfits.items()):
                raise HTTPException(422, "请选择对应人物可用的服装")
            doc["requested_outfits"] = body.outfits
        if body.length is not None:
            doc["length"] = body.length
        if body.outfit_mode is not None:
            doc["outfit_mode"] = body.outfit_mode
            if body.outfit_mode != "fixed":
                doc['outfit_scope_start'] = len(doc['scenes'])
                doc.pop('outfit_profile', None)
                doc.pop('fixed_outfits', None)
        if body.tuning:
            doc["tuning"] = "outline"
        store.save(doc)
        try:
            author.extend(id, body.instruction)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return store.get(id)

    @app.get("/stories/{id}/audio/{line_id}")
    @app.post("/stories/{id}/audio/{line_id}")
    async def voice(id: str, line_id: str):
        doc = story_or_404(id)
        line = next((line for line in voice_lines(doc, adapter) if line["id"] == line_id), None)
        if not line:
            raise HTTPException(404, "这句不需要配音")
        path = cached_audio(store, adapter, id, line)
        if path is None:
            raise HTTPException(404, "这句配音尚未完成，请继续准备作品")
        return FileResponse(path, media_type="audio/mpeg")

    return app


if __name__ == "__main__":
    import uvicorn
    adapter = ProjectAdapter()
    store = StoryStore(data_root())
    token = runtime_connection()
    uvicorn.run(create_app(store, adapter, token), host="127.0.0.1", port=int(os.environ.get("KAIBUN_PORT", "8011")), log_level="warning")
