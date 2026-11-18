"""Prepare voice files and scene staging before a story becomes ready."""

import asyncio
import os
from copy import deepcopy

from wardrobe import WardrobeError
from backgrounds import BackgroundError
from body_director import BodyDirectionError
from production_state import transient,clear_production_error,normalize_recovered


class ProductionError(RuntimeError):
    """Safe, locally written production failure shown to the story's owner."""


def voice_targets(line, adapter):
    return [dict(line, speaker=cid, id=line["id"] + "~" + cid) if line.get("speakers") else line
            for cid in dict.fromkeys(line.get("speakers") or [line["speaker"]])
            if (adapter.has_voice(cid) if hasattr(adapter, 'has_voice') else cid in adapter.characters or (cid == "producer" and bool(os.environ.get("KAIBUN_PRODUCER_VOICE_ID"))))]


def voice_lines(doc, adapter):
    return [target for scene in doc['scenes'] for line in scene['lines'] for target in voice_targets(line, adapter)]


def voice_inventory(store, adapter, doc):
    targets = voice_lines(doc, adapter)
    documents = {doc['id']: doc}
    ready = {target['id'] for target in targets if cached_audio(store, adapter, doc['id'], target, documents=documents)}
    return targets, ready


def cached_audio(store, adapter, story_id, line, *, documents=None):
    signature, _ = adapter.voice_signature(line)
    path = store.audio_path(story_id, line["id"], signature)
    if path.is_file() and path.stat().st_size > 0:
        return path
    # A revised copy reuses only an exact content/voice match. Copy it into the
    # destination so deleting the original does not break the revised work.
    import shutil
    import uuid
    def read(sid):
        if documents is None:
            return store.get(sid)
        if sid not in documents:
            documents[sid] = store.get(sid)
        return documents[sid]

    doc = read(story_id)
    seen = {story_id}
    while doc and doc.get('revision_of') and doc['revision_of'] not in seen:
        source_id = doc['revision_of']
        seen.add(source_id)
        source = read(source_id)
        if not source:
            break
        original = next((l for l in voice_lines(source, adapter) if l['id'] == line['id']), None)
        if original and adapter.voice_signature(original)[0] == signature:
            cached = store.audio_path(source_id, line['id'], signature)
            if cached.is_file() and cached.stat().st_size:
                temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
                try:
                    shutil.copyfile(cached, temp)
                    temp.replace(path)
                    return path
                finally:
                    temp.unlink(missing_ok=True)
        doc = source
    return None


class Production:
    def __init__(self, store, adapter):
        self.store, self.adapter = store, adapter
        self.voice_gate = asyncio.Semaphore(2)

    async def ensure_voice(self, story_id, line, *, force=False):
        if not force and cached_audio(self.store, self.adapter, story_id, line):
            return
        async with self.voice_gate:
            if not force and cached_audio(self.store, self.adapter, story_id, line):
                return
            signature, voice_id = self.adapter.voice_signature(line)
            audio = await self.adapter.voice(line, voice_id)
            if not audio:
                raise ValueError('empty audio')
            path = self.store.audio_path(story_id, line['id'], signature)
            import uuid
            temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                temp.write_bytes(audio)
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)

    async def produce_voice(self, story_id, line, *, force=False, retry_delay=2, on_retry=None):
        try:
            await self.ensure_voice(story_id, line, force=force)
        except Exception as error:
            if not transient(error):
                raise
            if on_retry:
                on_retry(True)
            try:
                await asyncio.sleep(retry_delay)
                await self.ensure_voice(story_id, line, force=force)
            finally:
                if on_retry:
                    on_retry(False)

    async def prepare(self, doc):
        lines, ready = voice_inventory(self.store, self.adapter, doc)
        progress = dict(voices_done=len(ready),
                        voices_total=len(lines), staging_done=False)
        doc.update(status="preparing", phase="正在准备语音与演出", production=progress)
        self.store.save(doc)

        def checkpoint():
            doc["phase"] = f"正在准备语音与演出 · 配音 {progress['voices_done']} / {progress['voices_total']}"
            self.store.save(doc)

        async def prepare_voices():
            async def one(line):
                if line['id'] in ready:
                    return
                try:
                    await self.produce_voice(doc['id'], line)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    raise ProductionError("部分语音未能完成，继续准备会使用已完成的配音") from None
                progress["voices_done"] += 1
                checkpoint()
            await together([one(line) for line in lines])

        async def prepare_staging():
            try:
                if hasattr(self.adapter,'prepare_scene_production'):
                    await self.adapter.prepare_scene_production(doc)
                else:
                    await self.adapter.prepare_outfits(doc)
                    if hasattr(self.adapter, "prepare_backgrounds"):
                        await self.adapter.prepare_backgrounds(doc)
                    if hasattr(self.adapter, 'prepare_body_actions'):
                        await self.adapter.prepare_body_actions(doc)
            except asyncio.CancelledError:
                raise
            except (WardrobeError, BackgroundError, BodyDirectionError) as error:
                raise ProductionError(str(error)) from None
            except Exception:
                raise ProductionError("服装与演出准备未完成，请检查服装图片和对话模型的看图服务后继续") from None
            progress["staging_done"] = True
            checkpoint()

        await together([prepare_voices(), prepare_staging()])
        doc["production_version"] = 1


async def together(coroutines):
    tasks = [asyncio.create_task(coro) for coro in coroutines]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class ProductionPipeline:
    """One story owner, two voice workers and one coalescing staging worker.

    The author and checkpoints share one live document on the same event loop.
    Model-based staging works on a snapshot and commits asset fields only, so a
    late response cannot replace scenes that the author has appended meanwhile.
    """

    ASSET_FIELDS = ('outfits', 'outfit_changes', 'background', 'background_changes', 'body_plan','scene_plan','performance_plan')
    STAGING_RETRY_DELAYS=(5,15)
    VOICE_RETRY_DELAY=2

    def __init__(self, production, doc, *, voices_enabled=True, staging_enabled=True):
        self.production = production
        self.store, self.adapter, self.doc = production.store, production.adapter, doc
        self.queue = asyncio.Queue()
        self.staging_wake = asyncio.Event()
        self.progress_changed = asyncio.Event()
        self.seen, self.ready, self.failed = set(), set(), set()
        self.queued=set()
        self.voices_enabled=voices_enabled
        self.staging_enabled=staging_enabled
        previous=doc.get('production') or {}
        self.inherited_voice_error=previous.get('voice_error') if not voices_enabled else None
        self.total = 0
        self.staged_count = 0 if staging_enabled else previous.get('staging_scenes_done',len(doc['scenes']) if previous.get('staging_done') else 0)
        if staging_enabled and hasattr(getattr(self.adapter,'scene_production',None),'completed_prefix'):
            self.staged_count=self.adapter.scene_production.completed_prefix(doc)
            doc.pop('staging_progress',None)
        self.staging_error = previous.get('staging_error') if not staging_enabled else None
        self.voice_error = None
        self.staging_retrying=False
        self.staging_retry_attempt=0
        self.voice_retrying=0
        self.writing_finished = False
        self.closed = False
        self.tasks = []
        self.notify()
        self.tasks = [asyncio.create_task(self.voice_worker()) for _ in range(2)]
        self.tasks.append(asyncio.create_task(self.staging_worker()))

    def checkpoint(self):
        progress = dict(voices_done=len(self.ready), voices_total=self.total,
                        voices_failed=len(self.failed) or (max(0,self.total-len(self.ready)) if self.inherited_voice_error else 0), staging_done=self.staged_count == len(self.doc['scenes']) and not self.staging_error,
                        staging_scenes_done=self.staged_count, staging_scenes_total=len(self.doc['scenes']),
                        voices_active=not self.closed and self.voices_enabled and len(self.ready)+len(self.failed)<self.total,
                        staging_active=not self.closed and self.staging_enabled and self.staged_count<len(self.doc['scenes']) and not self.staging_error,
                        staging_retrying=self.staging_retrying,staging_retry_attempt=self.staging_retry_attempt,voice_retrying=self.voice_retrying>0)
        progress['active']=bool(progress['voices_active'] or progress['staging_active'])
        if self.failed or (self.inherited_voice_error and len(self.ready)<self.total):
            progress['voice_error'] = self.voice_error or self.inherited_voice_error
            progress['voice_failed_ids']=sorted({key[0].split('~')[0] for key in (self.failed or (self.seen-self.ready))})
        if self.staging_error:
            progress['staging_error'] = self.staging_error
        progress['staging_progress']=deepcopy(self.doc.get('staging_progress'))
        self.doc['production'] = progress
        normalize_recovered(self.doc)
        if self.doc.get('status') == 'interrupted':
            self.doc['phase'] = '写作已暂停，配音与演出继续' if progress['active'] else '写作已暂停，已有内容的配音与演出已处理'
        elif self.writing_finished and self.doc.get('status') == 'preparing':
            self.doc['phase'] = f"剧本已完成，正在准备语音与演出 · 配音 {len(self.ready)} / {self.total}"
        self.store.save(self.doc)
        self.progress_changed.set()

    def notify(self):
        """Schedule new saved lines without waiting for any external service."""
        for line in voice_lines(self.doc, self.adapter):
            signature, _ = self.adapter.voice_signature(line)
            key = (line['id'], signature)
            if key not in self.seen:
                self.seen.add(key)
                self.total += 1
            if key in self.ready:
                continue
            if cached_audio(self.store, self.adapter, self.doc['id'], line, documents={self.doc['id']:self.doc}):
                self.ready.add(key)
            elif self.voices_enabled and key not in self.queued and key not in self.failed:
                self.queued.add(key)
                self.queue.put_nowait((key, deepcopy(line)))
        self.staging_wake.set()
        self.checkpoint()

    def resume(self):
        # Failed attempts are no longer in flight, so only those keys can be
        # requeued while existing requests keep running undisturbed.
        self.voices_enabled=self.staging_enabled=True
        self.failed.clear()
        self.voice_error = self.staging_error = None
        self.inherited_voice_error=None
        self.staging_retry_attempt=0
        self.writing_finished = False
        self.notify()

    def retry(self,scope):
        clear_production_error(self.doc)
        if scope in ('voices','all'):
            self.voices_enabled=True
            self.failed.clear();self.voice_error=None;self.inherited_voice_error=None
        if scope in ('staging','all'):
            self.staging_enabled=True
            self.staging_error=None;self.staging_retry_attempt=0
        self.notify()

    async def voice_worker(self):
        from voice_errors import voice_failure
        def retrying(active):
            self.voice_retrying += 1 if active else -1
            self.checkpoint()

        while True:
            target = await self.queue.get()
            try:
                if target is None:
                    return
                key, line = target
                try:
                    await self.production.produce_voice(self.doc['id'], line, retry_delay=self.VOICE_RETRY_DELAY, on_retry=retrying)
                    self.ready.add(key)
                    self.failed.discard(key)
                    if not self.failed:self.voice_error=None
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.failed.add(key)
                    self.voice_error = voice_failure(error)
                self.checkpoint()
            finally:
                if target is not None:self.queued.discard(target[0])
                self.queue.task_done()

    def merge_assets(self, snapshot):
        originals = {scene['index']: scene for scene in self.doc['scenes']}
        for staged in snapshot['scenes']:
            current = originals.get(staged['index'])
            if current is not None:
                for key in self.ASSET_FIELDS:
                    if key in staged:
                        current[key] = deepcopy(staged[key])
                by_id = {line['id']:line for line in current['lines']}
                for line in staged['lines']:
                    if line['id'] in by_id:
                        for key in ('body_actions','performance_actions'):
                            if key in line:by_id[line['id']][key] = deepcopy(line[key])
        if 'background_selection' in snapshot:
            self.doc['background_selection'] = deepcopy(snapshot['background_selection'])
        if 'fixed_outfits' in snapshot:
            self.doc['fixed_outfits']=deepcopy(snapshot['fixed_outfits'])
        if 'outfit_profile' in snapshot:
            self.doc['outfit_profile']=snapshot['outfit_profile']

    async def staging_worker(self):
        while True:
            await self.staging_wake.wait()
            self.staging_wake.clear()
            if not self.staging_enabled or self.staging_error or len(self.doc['scenes'])<=self.staged_count:
                continue
            while True:
                snapshot=deepcopy(self.doc)
                try:
                    def scene_checkpoint(completed):
                        if completed<=self.staged_count:return
                        self.merge_assets(snapshot)
                        self.staged_count=completed
                        self.checkpoint()
                    if hasattr(self.adapter,'prepare_scene_production'):
                        def staging_progress(value):
                            self.doc['staging_progress']=value
                            self.checkpoint()
                        if hasattr(self.adapter,'scene_production'):
                            await self.adapter.prepare_scene_production(snapshot,checkpoint=scene_checkpoint,progress=staging_progress)
                        else:await self.adapter.prepare_scene_production(snapshot,checkpoint=scene_checkpoint)
                    else:
                        await self.adapter.prepare_outfits(snapshot)
                        self.merge_assets(snapshot)
                        self.checkpoint()
                        if hasattr(self.adapter,'prepare_backgrounds'):
                            await self.adapter.prepare_backgrounds(snapshot)
                        self.merge_assets(snapshot)
                        self.checkpoint()
                        if hasattr(self.adapter,'prepare_body_actions'):
                            await self.adapter.prepare_body_actions(snapshot,checkpoint=scene_checkpoint)
                    self.merge_assets(snapshot)
                    self.staged_count=len(snapshot['scenes'])
                    self.staging_error=None
                    self.staging_retrying=False
                    self.staging_retry_attempt=0
                    self.checkpoint()
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if 'outfit_profile' in snapshot:
                        self.doc['outfit_profile']=snapshot['outfit_profile']
                    retryable=getattr(error,'retryable',False) or transient(error)
                    if retryable and self.staging_retry_attempt<len(self.STAGING_RETRY_DELAYS):
                        delay=self.STAGING_RETRY_DELAYS[self.staging_retry_attempt]
                        self.staging_retry_attempt+=1
                        self.staging_retrying=True
                        self.staging_error=None
                        self.checkpoint()
                        try:
                            await asyncio.sleep(delay)
                        finally:
                            self.staging_retrying=False
                        continue
                    self.staging_error=str(error) if isinstance(error,(WardrobeError,BackgroundError,BodyDirectionError)) else '服装、背景或身体动作准备失败，可重试未完成场景'
                    self.checkpoint()
                    break

    async def wait_idle(self):
        """Drain saved work without closing workers that resumed writing can reuse."""
        await self.queue.join()
        while self.staging_enabled and not self.staging_error and self.staged_count < len(self.doc['scenes']):
            self.progress_changed.clear()
            await self.progress_changed.wait()

    async def finish(self):
        self.notify()
        self.writing_finished = True
        self.doc.update(status='preparing', phase='剧本已完成，正在准备语音与演出')
        self.checkpoint()
        self.staging_wake.set()
        await self.wait_idle()
        if self.failed or self.staging_error:
            reason = '；'.join(value for value in [self.voice_error if self.failed else None, self.staging_error] if value)
            raise ProductionError(reason + '。剧本已写完，已完成素材保留，继续会补齐未完成部分')
        self.doc['production_version'] = 1

    async def close(self):
        if self.closed:
            return
        self.closed = True
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if isinstance(self.doc.get('production'), dict):
            self.doc['production'].update(active=False,voices_active=False,staging_active=False,staging_retrying=False,voice_retrying=False)
            self.store.save(self.doc)
