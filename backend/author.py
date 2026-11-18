"""Durable story writing with full context and concurrent production."""

import asyncio
import json
import logging
import re
from pathlib import Path

from production import Production, ProductionError, ProductionPipeline
from script_context import full_script
from production_state import clear_production_error,normalize_recovered
from character_names import identity, name_reference, NAME_RULES
from support.llm import RateLimitError
from writer_retry import wait_for_writer_retry

EMOTIONS = {"calm", "happy", "shy", "surprised", "sad", "angry", "thinking"}
def creative_guidance(stage="script"):
    """Read every request; outline and dialogue have separate output contracts."""
    if stage not in ('script', 'outline'):
        raise ValueError('未知创作阶段')
    text = Path(__file__).with_name("CREATIVE_GUIDE.md").read_text(encoding="utf-8")
    common, marker, specific = text.partition('## 剧本输出约定')
    script, outline_marker, outline = specific.partition('## 大纲输出约定')
    if not marker or not outline_marker:
        raise ValueError('创作指导缺少大纲或剧本约定')
    return common + ('## 大纲输出约定' + outline if stage == 'outline' else marker + script)


DIALOGUE_FORMAT = (
    "同一speaker可以在lines中连续出现。长发言包含回忆、列举或心情转折时，按完整意思与自然停顿拆成连续多条台词，"
    "每条分别填写对应的ja、zh、emotion和kind=dialogue，接续原有语气与情绪，保留完整措辞和连贯语气。"
    "每条用于各自的配音和立绘演出，只包含本条内容，不能保留整段全文后再重复追加切分台词。"
    "微调中的句内演出区间仍在选中台词内部设置，不复制台词；台词不夹动作、旁白、心理或场景描写。"
)



CHARACTER_NOTES = json.loads(Path(__file__).with_name("character_notes.json").read_text(encoding="utf-8"))
log = logging.getLogger(__name__)
LENGTHS = {
    "short": dict(scale="几分钟的短篇", scenes="约1至3场", lines="15—25", speech="日语正文约800至1500字"),
    "standard": dict(scale="约十分钟的故事", scenes="约3至5场", lines="25—40", speech="日语正文约2000至3000字"),
    "half_hour": dict(scale="约三十分钟的故事", scenes="约7至12场", lines="30—45", speech="日语正文约6000至9000字"),
    "hour_plus": dict(scale="一小时以上的故事", scenes="约16至28场", lines="35—55", speech="日语正文约14000至20000字"),
}


class StoryFormatError(ValueError):
    """A locally written, safe explanation of an invalid story response."""

    def __init__(self, reason, *, field=None, scene_number=None, line_number=None):
        self.issue = dict(kind='format', reason=reason)
        self.issue.update({key: value for key, value in dict(field=field, scene_number=scene_number,
                                                             line_number=line_number).items() if value is not None})
        where = f'第{scene_number}幕' if scene_number is not None else ''
        if line_number is not None:
            where += f'第{line_number}句'
        super().__init__((where + '：' if where else '') + reason)


def story_character_sheet(sheet):
    """Keep character material while omitting instructions for live private chats."""
    sections = re.split(r"(?m)(?=^##\s)", sheet)
    return "\n".join(section.strip() for section in sections
                     if not re.match(r"^##\s+(?:硬性规则|自然对话与日常)\s*(?:\n|$)", section)).strip()


def json_object(text):
    start = text.find("{")
    if start < 0:
        raise StoryFormatError("模型回复中缺少剧本JSON对象")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        raise StoryFormatError("模型回复的剧本JSON不完整或格式有误") from None
    if not isinstance(value, dict):
        raise StoryFormatError("模型返回的剧本格式不正确")
    return value


def validate_outline(raw, known, requested, length, *, previous=None):
    if isinstance(raw, dict) and "scenes" not in raw and isinstance(raw.get("outline"), dict):
        raw = raw["outline"]
    cast = raw.get("cast", requested)
    if not isinstance(cast, list) or any(not isinstance(c, str) or c not in known for c in cast):
        raise StoryFormatError("大纲出场人物需为资料中的角色id")
    cast = list(dict.fromkeys(cast))
    scenes = raw.get("scenes")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 64:
        raise StoryFormatError("大纲需要包含1至64个场景")
    guests = raw.get("guests", [])
    if not isinstance(guests, list):
        raise StoryFormatError("大纲guests需要数组，未用配角时填写空数组")
    prior_guests = {guest['id']: guest['name'] for guest in (previous or {}).get('guests', [])}
    clean_guests = []
    seen_guests = set()
    for number, guest in enumerate(guests, 1):
        guest_id = guest.get('id') if isinstance(guest, dict) else None
        name = guest.get('name') if isinstance(guest, dict) else None
        if not isinstance(guest_id, str) or not re.fullmatch(r'[A-Za-z0-9_]{1,30}', guest_id) or not guest_id.strip('_'):
            raise StoryFormatError(f'第{number}位配角的id需为1至30个字母、数字或下划线', field='guests')
        if guest_id in known or guest_id in {'producer', 'narrator'} or guest_id in seen_guests:
            raise StoryFormatError(f'第{number}位配角的id「{guest_id}」重复或与已有角色冲突', field='guests')
        if not isinstance(name, str) or not name.strip():
            raise StoryFormatError(f'第{number}位配角缺少姓名', field='guests')
        name = name.strip()[:40]
        if guest_id in prior_guests and name != prior_guests[guest_id]:
            raise StoryFormatError(f'第{number}位配角的id「{guest_id}」已用于前文的另一位配角', field='guests')
        seen_guests.add(guest_id)
        if len(seen_guests - prior_guests.keys()) > 3:
            raise StoryFormatError(f'第{number}位配角超过本次最多新增3位的上限', field='guests')
        clean_guests.append(dict(id=guest_id, name=name))
    clean_scenes = []
    for number, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict) or not all(isinstance(scene.get(k), str) and scene[k].strip() for k in ('place', 'direction')):
            raise StoryFormatError('大纲场景的place与direction需要非空文本', scene_number=number)
        people = scene.get('speaking_roles')
        if not isinstance(people, list) or not people or any(not isinstance(cid, str) for cid in people):
            raise StoryFormatError('speaking_roles需列出本幕实际发言的角色id', field='speaking_roles', scene_number=number)
        for cid in dict.fromkeys(people):
            if cid in known:
                if cid not in cast:
                    cast.append(cid)
            elif cid != 'producer' and cid not in seen_guests and cid not in prior_guests:
                label = f'「{cid}」' if re.fullmatch(r'[A-Za-z0-9_]{1,30}', cid) else ''
                raise StoryFormatError(f'speaking_roles含未登记的发言人{label}，请使用正式角色id或在guests定义', field='speaking_roles', scene_number=number)
        clean_scenes.append(dict(place=scene['place'].strip()[:160], direction=scene['direction'].strip()[:2400],
                                 transition=scene.get('transition') if scene.get('transition') in ('cut', 'pause', 'fade') else 'cut',
                                 speaking_roles=list(dict.fromkeys(people))))
    if not cast or not set(requested).issubset(cast):
        raise StoryFormatError('大纲至少需要一位正式角色，并包含所有指定人物', field='cast')
    return dict(cast=cast,
                opening=raw.get("opening", "").strip()[:500] if isinstance(raw.get("opening", ""), str) else "",
                guests=clean_guests, direction=str(raw.get("direction", ""))[:1800], scenes=clean_scenes,
                length=length)


def validate_scene(raw, outline, index, *, first_line_number=1):
    cast = set(outline['cast'])
    allowed = {*cast, 'producer', *(guest['id'] for guest in outline['guests'])}
    lines = raw.get("lines")
    if not isinstance(lines, list) or not lines or len(lines) > 100:
        raise StoryFormatError("场景需要包含1至100句对白")
    clean = []
    for i, line in enumerate(lines):
        def invalid(field, reason):
            return StoryFormatError(reason, field=field, scene_number=index + 1, line_number=first_line_number + i)
        if not isinstance(line, dict):
            raise invalid('line', '台词需为对象')
        speaker = line.get('speaker')
        if not isinstance(speaker, str) or speaker not in allowed:
            label = f'「{speaker}」' if isinstance(speaker, str) and re.fullmatch(r'[A-Za-z0-9_]{1,30}', speaker) else ''
            raise invalid('speaker', f'发言人{label}未登记，speaker需为本篇出场角色、producer或已定义配角的id')
        if not isinstance(line.get('zh'), str) or not line['zh'].strip():
            raise invalid('zh', '角色对白缺少中文字幕zh')
        ja = line.get("ja", "")
        if not isinstance(ja, str) or not ja.strip():
            raise invalid('ja', '角色对白缺少日语ja')
        if len(ja) > 1200 or len(line["zh"]) > 1600:
            raise invalid('ja' if len(ja) > 1200 else 'zh', '单句台词过长')
        emotion = line.get("emotion")
        direction = dict(id=f"{index}-{i}", speaker=line["speaker"], ja=ja.strip(), zh=line["zh"].strip(),
                         emotion=emotion if isinstance(emotion, str) and emotion in EMOTIONS else "calm")
        kind = line.get("kind", "dialogue")
        if kind not in ("dialogue", "thought"):
            raise invalid('kind', 'kind需要填写dialogue对白或thought心声')
        direction["kind"] = kind
        if line["speaker"] == "fuyuko" and "voice_style" in line:
            if line["voice_style"] not in ("natural", "idol"):
                raise invalid('voice_style', '冬优子声线需要填写natural或idol')
            direction["voice_style"] = line["voice_style"]
        if "speakers" in line:
            speakers = line["speakers"]
            if not isinstance(speakers, list) or not speakers or any(not isinstance(cid, str) or cid not in cast for cid in speakers):
                raise invalid('speakers', '异口同声只能填写本篇出场角色id')
            speakers = list(dict.fromkeys(speakers))
            if len(speakers) > 7 or speaker not in speakers:
                raise invalid('speakers', '异口同声去重后最多7人，且必须包含主发言人')
            if len(speakers) > 1:
                direction['speakers'] = speakers
        for field in ('onstage', 'focus'):
            people = line.get(field)
            if isinstance(people, list):
                visible = list(dict.fromkeys(cid for cid in people if isinstance(cid, str) and cid in cast))
                direction[field] = visible[:7] if field == 'focus' else visible
        if 'actor_states' in line:
            states = line['actor_states']
            if not isinstance(states, dict):
                raise invalid('actor_states', 'actor_states需要按人物id保存表情与动作')
            checked = {}
            for cid, state in states.items():
                if cid not in cast or not isinstance(state, dict):
                    continue
                # Raw visual controls belong to manual editing/production, not the writer.
                state={key:value for key,value in state.items() if key not in ('expression','body_action','preset_id','mouth_action','rest_mouth_action')}
                if 'body_mood' in state and not valid_body_mood(state['body_mood']):
                    state.pop('body_mood')
                for key,value in state.items():
                    if key not in ('emotion','body_mood') or not (isinstance(value,str) and value in EMOTIONS or key=='body_mood' and value is None):
                        raise invalid(f'actor_states.{cid}.{key}', f'模型返回的人物情绪或动作分类无效（{cid}.{key}），需使用七类情绪')
                checked[cid]=state
            direction['actor_states'] = checked
        if 'listener_state_persist' in line:
            if not isinstance(line['listener_state_persist'], bool):
                raise invalid('listener_state_persist', 'listener_state_persist需要布尔值')
            direction['listener_state_persist'] = line['listener_state_persist']
        if 'body_mood' in line and valid_body_mood(line['body_mood']):
            direction['body_mood'] = line['body_mood']
        clean.append(direction)
    memory = raw.get("memory")
    if not isinstance(memory, str) or not memory.strip():
        raise StoryFormatError('场景缺少非空文本memory连续性记录', field='memory', scene_number=index + 1)
    return dict(index=index, title=f"第{index + 1}幕", place=outline["scenes"][index]["place"],
                lines=clean, memory=memory.strip()[:2800], transition=outline["scenes"][index].get("transition", "cut"))


def valid_body_mood(value):
    return value is None or isinstance(value, str) and value in EMOTIONS


class Author:
    def __init__(self, store, adapter):
        self.store, self.adapter = store, adapter
        self.tasks = {}
        self.pipelines = {}
        self.drains = {}
        self.pausing = set()
        self.gate = asyncio.Semaphore(1)
        self.production = Production(store, adapter)

    def enqueue(self, id):
        if id in self.tasks:
            return
        task = asyncio.create_task(self.generate(id))
        self.tasks[id] = task
        task.add_done_callback(lambda _: self.tasks.pop(id, None))

    def busy(self, id):
        return id in self.tasks or id in self.pipelines

    def retry_production(self,id,scope='staging'):
        doc=self.store.get(id)
        if not doc or not doc.get('scenes'):
            raise ValueError('至少保存一幕后才能准备演出')
        if doc.get('revision_proposal') or (doc.get('revision_progress') or {}).get('status')=='working':
            raise ValueError('请先处理当前修改预览')
        pipeline=self.pipelines.get(id)
        if pipeline and not pipeline.closed:
            pipeline.retry(scope)
            return pipeline.doc
        if id in self.tasks or id in self.drains:
            raise ValueError('当前配音或调整任务正在收尾，请稍后重试')
        clear_production_error(doc)
        complete=bool(doc.get('outline') and len(doc['scenes'])>=len(doc['outline']['scenes']))
        if complete:doc.update(status='preparing',phase='正在重试未完成的制作')
        else:doc.update(status='interrupted',phase='写作保持暂停，正在重试已保存场景的制作')
        pipeline=ProductionPipeline(self.production,doc,voices_enabled=scope in ('voices','all'),staging_enabled=scope in ('staging','all'))
        self.pipelines[id]=pipeline
        drain=asyncio.create_task(self.drain_paused(id,pipeline))
        self.drains[id]=drain
        drain.add_done_callback(lambda _: self.drains.pop(id,None) if self.drains.get(id) is drain else None)
        return doc

    async def stop(self, id, *, production=False):
        if not production:
            self.pausing.add(id)
        task = self.tasks.get(id)
        try:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            self.pausing.discard(id)
        pipeline = self.pipelines.get(id)
        if production:
            drain = self.drains.pop(id, None)
            if drain:
                drain.cancel()
                await asyncio.gather(drain, return_exceptions=True)
            if pipeline:
                self.pipelines.pop(id, None)
                await pipeline.close()
        doc = pipeline.doc if pipeline else self.store.get(id)
        if doc and doc["status"] != "ready":
            doc.update(status="interrupted", phase="写作已暂停，可以继续")
            self.store.save(doc)
        if pipeline and not production:
            pipeline.checkpoint()
            if id not in self.drains:
                drain = asyncio.create_task(self.drain_paused(id, pipeline))
                self.drains[id] = drain
                drain.add_done_callback(lambda _: self.drains.pop(id, None) if self.drains.get(id) is drain else None)

    async def drain_paused(self, id, pipeline):
        await pipeline.wait_idle()
        if id in self.tasks or self.pipelines.get(id) is not pipeline:
            return
        # Keep ownership through cleanup so edits cannot race the last save.
        doc = pipeline.doc
        pending_voice=len(pipeline.ready)<pipeline.total
        pending_stage=pipeline.staged_count<len(doc['scenes']) or bool(pipeline.staging_error)
        if len(doc['scenes']) == len(doc['outline']['scenes']):
            if pending_voice or pending_stage:
                doc.update(status='failed', phase='剧本已完成，语音与演出待补齐',
                           error='部分素材未完成，继续会补齐缺失部分', failure_stage='production')
            else:
                doc.update(status='ready', phase='故事写好了', error=None, production_version=1)
                doc.pop('failure_stage',None)
                normalize_recovered(doc)
            self.store.save(doc)
        else:
            doc.update(status='interrupted',phase='写作已暂停，已保存场景的制作完成' if not pending_voice and not pending_stage else '写作已暂停，部分制作仍待处理')
            normalize_recovered(doc)
            self.store.save(doc)
        await pipeline.close()
        if self.pipelines.get(id) is pipeline:
            self.pipelines.pop(id, None)

    def extend(self, id, instruction):
        doc = self.store.get(id)
        if not doc or self.busy(id):
            raise ValueError("请先停止当前创作")
        if not doc["scenes"]:
            raise ValueError("写完一幕后可以续写")
        doc.update(status="queued", continuation=instruction.strip(), phase="准备续写", error=None)
        self.store.save(doc)
        self.enqueue(id)

    async def request(self, system, user, *, validate, doc, stage):
        last = None
        correction = ""
        rate_retries = 0
        for attempt in range(2):
            while True:
                try:
                    reply = await self.adapter.complete(system, user + correction)
                    break
                except RateLimitError as error:
                    await wait_for_writer_retry(self.store, doc, error, rate_retries)
                    rate_retries += 1
                    doc['phase'] = '正在构思故事'
                    self.store.save(doc)
            try:
                parsed = json_object(reply)
                return validate(parsed)
            except StoryFormatError as error:
                last = error
                directory = self.store.directory / "diagnostics"
                directory.mkdir(exist_ok=True)
                diagnostic = dict(stage=stage, model=self.adapter.model, attempt=attempt + 1,
                                  reason=str(error), response=reply)
                # A cancelled to_thread write could recreate a deleted story's
                # diagnostic after author.stop() returned. This bounded write
                # finishes before the task can be cancelled.
                (directory / f"{doc['id']}-{stage}-{attempt + 1}.json").write_text(
                    json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
                log.warning("story=%s stage=%s attempt=%s reason=%s", doc["id"], stage, attempt + 1, error)
                correction = ("\n上一版未通过格式检查：" + str(error) +
                              "\n请保留本篇构思，按上述格式返回修正后的完整JSON。上一版：\n" + reply)
        raise last

    async def generate(self, id):
        async with self.gate:
            existing = self.pipelines.get(id)
            if existing and existing.closed:
                # A paused pipeline may be finishing cleanup as resume arrives.
                # Wait for its final save, then load a fresh document.
                drain = self.drains.get(id)
                if drain:
                    await asyncio.shield(drain)
                existing = None
            doc = existing.doc if existing else self.store.get(id)
            if not doc or doc["status"] == "ready":
                return
            stage = "outline"
            length = LENGTHS.get(doc["length"], LENGTHS["short"])
            pipeline = None
            try:
                doc.update(status="generating", phase="正在构思故事", error=None, model=self.adapter.model)
                doc.pop('failure_stage',None)
                self.store.save(doc)
                if not doc["outline"] or doc.get("continuation"):
                    catalog = [{"id": c.id, **identity(c), "intro": c.intro,
                                "relationship": CHARACTER_NOTES.get(c.id, "")} for c in self.adapter.characters.values()]
                    raw = await self.request(
                        "你是《偶像大师 闪耀色彩》的剧本编剧。构思一个适合私人观看的对白剧大纲。\n" + creative_guidance("outline") + "\n" + NAME_RULES,
                        "用户想法：" + doc["premise"] + "\n指定角色：" + json.dumps(doc["requested_cast"]) +
                        "\n规模：" + length["scale"] + "，" + length["speech"] + "，可参考" + length["scenes"] + "，随情境自然分场。" +
                        "\n按情境选择人物，简短交代这次发生的事、关系与松散走向，再自然分场。"
                        "大纲、开头、场次地点与场次说明均用简体中文；日文名称保留原文时附中文解释。"
                        "本篇的趣味留在情境里，具体心意、回应和后续行动由人物在正文中自由展开。"
                        "cast选资料中的角色id，人物数量随故事需要决定，指定人物均需包含。场景为1至64场，每场place与direction填写非空文本。"
                        "制作人用producer，cast填写资料中的正式角色id；guests填写新配角，未用配角时填写空数组。"
                        "场次数量随故事发展自行决定。每幕transition选择cut（剧情场景连续）、pause（时间空间小变化）或fade（时间空间大变化）。允许P和必要的虚构配角。"
                        '只输出JSON：{"cast":["偶像id"],"guests":[{"id":"friend","name":"配角称呼"}],'
                        '"opening":"供读者观看的故事开头，仅介绍起始处境，后续留给正文揭晓",'
                        '"direction":"整体情境与松散走向","scenes":[{"place":"地点","direction":"本场情境与可展开的线索","speaking_roles":["producer","角色id"]}]}。'
                        "\n补充内容：" + doc.get("details", "") +
                        "\n人物资料：" + json.dumps(catalog, ensure_ascii=False) + ("\n续写要求：" + doc["continuation"] + "\n完整原剧本：" + json.dumps(full_script(doc), ensure_ascii=False) + "\n原大纲：" + json.dumps(doc["outline"], ensure_ascii=False) if doc.get("continuation") else ""),
                        validate=lambda raw: validate_outline(raw, self.adapter.characters, doc['requested_cast'], doc['length'], previous=doc.get('outline') if doc.get('continuation') else None),
                        doc=doc, stage=stage)
                    planned = raw
                    if doc.get("continuation"):
                        original = doc["outline"]
                        original["scenes"] = original["scenes"][:len(doc["scenes"])] + planned["scenes"]
                        original["cast"] = list(dict.fromkeys(original["cast"] + planned["cast"]))
                        original["guests"] = list({g["id"]: g for g in original["guests"] + planned["guests"]}.values())
                        doc["continuation_direction"] = planned["direction"]
                        doc["active_instruction"] = doc["continuation"]
                        doc.pop("continuation", None)
                    else:
                        doc["outline"] = planned
                    doc["title"] = doc["premise"]
                    self.store.save(doc)
                if doc.get("tuning") == "outline":
                    doc.update(status="tuning", phase="大纲已完成")
                    self.store.save(doc)
                    return
                outline = doc["outline"]
                if doc.get('tuning') != 'script':
                    pipeline = existing or ProductionPipeline(self.production, doc)
                    self.pipelines[id] = pipeline
                    if existing:
                        pipeline.resume()
                stage = f"scene-{len(doc['scenes']) + 1}"
                from batch_writer import write_batches
                await write_batches(self, doc, pipeline)
                doc.pop("active_instruction", None)
                if doc.get("tuning") == "script":
                    doc.update(status="tuning", phase="剧本已完成")
                    self.store.save(doc)
                    return
                stage = "production"
                await pipeline.finish()
                doc.update(status="ready", phase="故事写好了", error=None)
                doc.pop('failure_stage',None)
                normalize_recovered(doc)
                self.store.save(doc)
            except asyncio.CancelledError:
                if pipeline and id not in self.pausing:
                    self.pipelines.pop(id, None)
                    await pipeline.close()
                doc.update(status="interrupted", phase="生成中断，可以继续")
                self.store.save(doc)
                raise
            except Exception as error:
                if pipeline:
                    self.pipelines.pop(id, None)
                    await pipeline.close()
                if stage.startswith('scene-'):
                    stage = f"scene-{len(doc['scenes']) + 1}"
                # Only locally written format errors reach the UI, never provider text or credentials.
                reason = str(error) if isinstance(error, (StoryFormatError, ProductionError)) else f"模型请求未完成（{type(error).__name__}）"
                if isinstance(error, RateLimitError):
                    reason = '模型服务限流或容量不足（HTTP 429），请等待片刻再试'
                    doc['writing_failure'] = dict(kind='rate_limit', status=429)
                where = "语音与演出" if stage == "production" else "大纲" if stage == "outline" else f"第{stage.split('-')[1]}幕"
                log.warning("story=%s stage=%s failed type=%s", doc["id"], stage, type(error).__name__)
                doc.update(status="failed", phase="剧本已完成，语音与演出待补齐" if stage == "production" else "暂时没能写完", failure_stage=stage,
                           error=f"{where}生成失败：{reason}。已保存进度，可以继续。")
                self.store.save(doc)

            finally:
                if pipeline and id not in self.pausing:
                    drain = self.drains.pop(id, None)
                    if drain:
                        drain.cancel()
                        await asyncio.gather(drain, return_exceptions=True)
                    if self.pipelines.get(id) is pipeline:
                        self.pipelines.pop(id, None)
                    await pipeline.close()

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        drains = list(self.drains.values())
        for task in drains:
            task.cancel()
        await asyncio.gather(*drains, return_exceptions=True)
        await asyncio.gather(*(pipeline.close() for pipeline in self.pipelines.values()))
        self.pipelines.clear()
