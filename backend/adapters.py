"""Read-only adapters for the existing project's models and character assets."""

import hashlib
import json
import os
import asyncio
import re
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import httpx

from support.characters import load_characters
from support.config import LLMSettings, fish_audio_model_from_env
from support.proxy import proxy_from_env
from paths import characters_path_from_env, data_root, project_path
from support.delivery_cues import spoken_by, voiced
FISH_TTS_HTTP_URL = 'https://api.fish.audio/v1/tts'
from support.llm import ChatMessage
from support.llm.providers import open_llm
from support.proxy import open_client
from support.readings import load_readings, pronunciation_dictionary, readings_path, used_in
from character_names import speech_names, identity
from wardrobe import Wardrobe, complete_with_images
from backgrounds import Backgrounds
from outfit_policy import AUTO_EXCLUDED, DAILY_DEFAULTS
from voice_errors import VoiceError


def parenthesized_dialogue(text):
    """A complete Japanese dialogue sentence wrapped in parentheses is still speech.

    Bare cues such as （笑う） or （小声で） continue to be filtered. Only the
    synthesis input is unwrapped; the saved script and its kind are unchanged.
    """
    match=re.fullmatch(r'(?:\(([^()（）]*)\)|（([^()（）]*)）)',text.strip())
    if not match:return None
    inner=next(group for group in match.groups() if group is not None).strip()
    if re.search(r'[ぁ-ゖァ-ヺ]',inner) and re.search(r'[。！？!?、…]',inner):return inner
    return None


def story_voice_text(line, model):
    text = line["ja"]
    if line.get("kind") == "thought":
        # Story thoughts put meaningful words inside parentheses, unlike chat stage directions.
        text = text.translate(str.maketrans("", "", "()（）"))
    else:
        unwrapped=parenthesized_dialogue(text)
        if unwrapped is not None:text=unwrapped
    return spoken_by(voiced(f"[{line['emotion']}] {text}"), model)


class BackgroundSelector:
    """A separate, small configured model; scene selection never calls the dialogue model."""

    def __init__(self, proxy):
        settings = LLMSettings._from_env("KAIBUN_BACKGROUND", fallback="CHAT")
        self.settings = replace(settings, model=os.environ.get("KAIBUN_BACKGROUND_LLM_MODEL") or settings.model,
                                max_tokens=settings.max_tokens,
                                reasoning_effort=settings.reasoning_effort)
        self.model = self.settings.model
        self.request_timeout=max(1,float(os.environ.get('KAIBUN_BACKGROUND_REQUEST_TIMEOUT_SECONDS') or 180))
        from urllib.parse import urlsplit
        from selector_limits import SelectorLimits
        school=urlsplit(settings.base_url).hostname=='models.sjtu.edu.cn'
        self.limits=SelectorLimits(os.environ.get('KAIBUN_BACKGROUND_REQUESTS_PER_MINUTE') or (10 if school else 0),
                                   os.environ.get('KAIBUN_BACKGROUND_TOKENS_PER_MINUTE') or (300000 if school else 0))
        route = proxy.proxy_for("chat_llm")
        # Absent preserves the shared proxy; an explicitly empty override means
        # direct access to a school/local endpoint, without changing the writer.
        if 'KAIBUN_BACKGROUND_PROXY_URL' in os.environ:
            route = os.environ['KAIBUN_BACKGROUND_PROXY_URL'].strip() or None
        self.client = open_client(route, httpx.Timeout(180, connect=15))
        self.llm = open_llm(self.settings, self.client)

    async def complete(self, system, user):
        from selector_limits import estimate_budget
        from support.billing import Billing,billing
        async with self.limits.reserve(estimate_budget(self.settings,system,user)) as reservation:
            chunks = []
            used=Billing()
            stream = self.llm.stream([ChatMessage("system", system), ChatMessage("user", user)])
            try:
                async with asyncio.timeout(self.request_timeout):
                    with billing(used):
                        async for item in stream:
                            if isinstance(item, str):
                                chunks.append(item)
            finally:
                await stream.aclose()
                if used.input is not None or used.output is not None:
                    self.limits.settle(reservation,(used.input or 0)+(used.output or 0))
            return "".join(chunks)

    async def complete_with_images(self, system, user, images):
        from selector_limits import estimate_budget
        from support.billing import Billing,billing
        async with self.limits.reserve(estimate_budget(self.settings,system,user,images)) as reservation:
            used=Billing()
            try:
                with billing(used):
                    return await complete_with_images(self, system, user, images)
            finally:
                if used.input is not None or used.output is not None:
                    self.limits.settle(reservation,(used.input or 0)+(used.output or 0))

    async def close(self):
        await self.llm.aclose()

class WardrobeAdapter:
    """Use the configured asset selector while retaining the project's local catalog."""
    def __init__(self, project, selector):
        self.project, self.selector = project, selector
        self.settings, self.model = selector.settings, selector.model
        self.characters, self.asset_root = project.characters, project.asset_root

    async def complete(self, system, user):
        return await self.selector.complete(system, user)

    async def complete_with_images(self, system, user, images):
        return await self.selector.complete_with_images(system, user, images)


ROOT = Path(__file__).resolve().parents[1]


class ProjectAdapter:
    def __init__(self):
        self.settings = LLMSettings.chat_from_env()
        self.model = self.settings.model
        self.proxy = proxy_from_env()
        self.client = open_client(self.proxy.proxy_for("chat_llm"), httpx.Timeout(180, connect=15))
        self.llm = open_llm(self.settings, self.client)
        self.characters = load_characters(characters_path_from_env())
        self.characters = {cid: replace(character, default_outfit=DAILY_DEFAULTS[cid]) if cid in DAILY_DEFAULTS and character.default_outfit in AUTO_EXCLUDED else character for cid, character in self.characters.items()}
        self.asset_root = project_path(os.environ.get("KAIBUN_ASSET_ROOT") or 'assets')
        self.stand_bounds = {}
        for c in self.characters.values():
            if c.default_outfit:
                path = self.asset_root / "spine" / "idols" / c.default_outfit / "data.json"
                try:
                    skeleton = json.loads(path.read_text(encoding="utf-8"))["skeleton"]
                    width, height = float(skeleton["width"]), float(skeleton["height"])
                    if width > 0 and height > 0:
                        self.stand_bounds[c.id] = dict(width=width, height=height)
                except (OSError, ValueError, KeyError, TypeError):
                    pass
        self.readings = load_readings(readings_path(characters_path_from_env()))
        # Name normalization precedes synthesis; the dictionary must match that normalized spelling.
        from support.readings import Reading
        natsuha = next((r for r in self.readings if r.written == '夏葉' and r.phonemes), None)
        if natsuha:
            self.readings = tuple(r for r in self.readings if r.written != 'ナツハ') + (Reading('ナツハ', 'なつは', natsuha.phonemes),)
        self.dictionary = pronunciation_dictionary(self.readings)
        self.tts_model = fish_audio_model_from_env()
        self.tts_key = os.environ.get("FISH_AUDIO_API_KEY", "")
        self.background_selector = BackgroundSelector(self.proxy)
        self.wardrobe = Wardrobe(WardrobeAdapter(self, self.background_selector), data_root() / 'wardrobe')
        self.backgrounds = Backgrounds(self.background_selector, data_root() / 'backgrounds')
        from body_director import BodyDirector
        self.body_director = BodyDirector(self, data_root() / 'body_direction')
        from scene_production import SceneProduction
        self.scene_production = SceneProduction(self,data_root()/'scene_production')
        from preset_director import PresetDirector
        self.preset_director = PresetDirector(self)

    def has_voice(self, cid):
        character = self.characters.get(cid)
        voice = getattr(character, 'voice_id', '') if character else (
            os.environ.get('KAIBUN_PRODUCER_VOICE_ID') if cid == 'producer' else '')
        return bool(self.tts_key and voice)

    async def complete(self, system, user):
        chunks = []
        async for item in self.stream_complete(system, user):
            chunks.append(item)
        return "".join(chunks)

    async def stream_complete(self, system, user):
        stream = self.llm.stream([ChatMessage("system", system), ChatMessage("user", user)])
        try:
            async for item in stream:
                if isinstance(item, str):
                    yield item
        finally:
            await stream.aclose()

    async def complete_with_images(self, system, user, images):
        return await complete_with_images(self, system, user, images)

    def outfits(self, cid):
        return self.wardrobe.outfits(cid)

    def outfit(self, cid, outfit_id):
        return self.wardrobe.outfit(cid, outfit_id)

    def thumbnail_path(self, outfit_id):
        return self.wardrobe.thumbnail_path(outfit_id)

    async def ensure_thumbnail(self, outfit_id):
        return await self.wardrobe.ensure_thumbnail(outfit_id)

    async def prepare_outfits(self, doc):
        return await self.wardrobe.prepare(doc)

    async def prepare_backgrounds(self, doc):
        return await self.backgrounds.prepare(doc)

    async def prepare_body_actions(self, doc, checkpoint=None):
        return await self.body_director.prepare(doc,checkpoint=checkpoint)

    async def prepare_scene_production(self, doc, checkpoint=None, progress=None):
        return await self.scene_production.prepare(doc,checkpoint=checkpoint,progress=progress)

    def catalog(self):
        result = []
        for c in self.characters.values():
            outfit = c.default_outfit
            stand = None
            if self.wardrobe.outfit(c.id, outfit):
                stand = {"id": outfit, "url": f"/api/kaibun/stand/{outfit}/"}
            result.append(dict(**identity(c),
                               retired_default_outfits=[oid for oid in AUTO_EXCLUDED if self.wardrobe.outfit(c.id, oid)],
                               avatar_url=f"/api/kaibun/characters/{c.id}/avatar" if c.avatar and Path(c.avatar).is_file() else None,
                               background_url=f"/api/kaibun/characters/{c.id}/background" if c.background else None,
                               stand=stand, stand_bounds=self.stand_bounds.get(c.id)))
        return result

    def voice_signature(self, line):
        character = self.characters.get(line["speaker"])
        voice = character.voice_id if character else os.environ.get("KAIBUN_PRODUCER_VOICE_ID", "")
        if line["speaker"] == "fuyuko" and line.get("voice_style") == "idol":
            voice = os.environ.get("KAIBUN_FUYUKO_IDOL_VOICE_ID") or voice
        if line['speaker'] == 'nichika' and line.get('nichika_voice_style') == 'idol':
            voice = os.environ.get('KAIBUN_NICHIKA_IDOL_VOICE_ID') or 'b3757a5a71ba4078be1e25b446190975'
        return self._voice_signature(line['ja'], line['emotion'], voice, self.tts_model, line.get('kind') == 'thought')

    @lru_cache(maxsize=8192)
    def _voice_signature(self, ja, emotion, voice, model, thought):
        # Configuration/readings/character names belong to this adapter instance.
        # Content, effective voice and model stay in the key, so edits cannot
        # reuse an earlier line's audio signature.
        line = dict(ja=ja, emotion=emotion, kind='thought' if thought else 'dialogue')
        payload = "\n".join((ja, emotion, voice, model))
        if thought:
            payload += "\nstory-thought-v2"
        elif parenthesized_dialogue(ja) is not None:
            payload += "\nparenthesized-dialogue-v1"
        original = story_voice_text(line, model)
        spoken = speech_names(original, self.characters)
        if spoken != original:
            payload += "\nname-kana-v1\n" + spoken
        relevant = used_in(spoken, getattr(self, 'readings', ()))
        if relevant:
            payload += "\nreadings-v1\n" + json.dumps([(r.written, r.phonemes) for r in relevant], ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()[:24], voice

    async def voice(self, line, voice):
        if not self.tts_key:
            raise VoiceError("还没有配置语音服务")
        text = speech_names(story_voice_text(line, self.tts_model), self.characters)
        if not text.strip():
            raise VoiceError("这句没有可朗读的剧本文字")
        body = dict(text=text, format="mp3", sample_rate=44100, mp3_bitrate=128, latency="normal")
        if voice:
            body["reference_id"] = voice
        if self.dictionary:
            body["pronunciation_dictionary"] = self.dictionary
        proxy = self.proxy.proxy_for('tts')
        for attempt in range(3):
            try:
                async with open_client(proxy, httpx.Timeout(90, connect=15)) as client:
                    response = await client.post(FISH_TTS_HTTP_URL, headers={"Authorization": f"Bearer {self.tts_key}", "model": self.tts_model}, json=body)
                break
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError):
                # These failures occur while establishing the connection, before synthesis.
                if attempt == 2:
                    route = '本机代理连接失败，请检查代理节点和网络' if proxy else '网络连接失败，请检查网络或语音代理配置'
                    raise VoiceError('无法连接 Fish Audio：' + route) from None
                await asyncio.sleep(.5 * (attempt + 1))
            except httpx.TimeoutException:
                raise VoiceError('Fish Audio 响应超时，本次未自动重发，请稍后检查配音后重试') from None
            except httpx.HTTPError:
                raise VoiceError('Fish Audio 连接中断，请稍后重试') from None
        if response.status_code in (401, 403):
            raise VoiceError(f'Fish Audio 拒绝访问（HTTP {response.status_code}），请检查密钥及音色访问权限')
        if response.status_code == 429:
            raise VoiceError('Fish Audio 请求受限（HTTP 429），请稍后重试并检查账户额度')
        if not response.is_success:
            raise VoiceError(f'Fish Audio 未完成配音（HTTP {response.status_code}），请检查服务状态、模型和音色配置')
        if not response.is_success or not response.content or response.headers.get("content-type", "").startswith("application/json"):
            raise VoiceError("Fish Audio 未返回有效音频，请稍后重试")
        return response.content

    async def close(self):
        await self.wardrobe.close()
        await self.background_selector.close()
        await self.llm.aclose()
