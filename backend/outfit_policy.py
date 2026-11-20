"""Resolve automatic outfits locally after one text-only story classification."""
import json
import logging
from pathlib import Path
from typing import Literal
from uuid import uuid4

from paths import data_root


AUTO_EXCLUDED = frozenset({'stand/1030230010', 'stand/1030240010'})
DAILY_DEFAULTS = {'hinana': 'stand/1040230010', 'nichika': 'stand/1030240020'}
OutfitSlot = Literal['spring', 'summer', 'autumn', 'winter', 'special']
SceneOutfitProfile = Literal['default', 'spring', 'summer', 'autumn', 'winter']
OutfitMode = Literal['story', 'fixed', 'default', 'spring', 'summer', 'autumn', 'winter']
SEASONS = ('spring', 'summer', 'autumn', 'winter')
PROFILES = (*SEASONS, 'special')
DEFAULT_PROFILE = 'default'
LOCAL_MODES = (DEFAULT_PROFILE, *SEASONS)
SEASON_PROMPT = (
    '只判断本故事所有未手动指定衣装的角色共用的衣装分类。'
    '优先采用用户明确要求的季节；没明说但剧情有一致、充分的季节线索（如盛夏酷暑或冬季大雪）也可判断。'
    '仅当全篇主情境明确要求演出、典礼或特殊主题装束时用 special；普通约会、聚会、单幕表演不算。'
    '没有足够线索或多个季节无法统一时用 default，不依据现实日期或任意猜测。'
    '不要选择具体衣装，不要修改剧情。只返回 JSON 对象，例如 {"outfit_profile":"winter"}。'
    '可用值：spring、summer、autumn、winter、special、default。'
)

def automatic_outfit(oid):
    return oid not in AUTO_EXCLUDED


def read_outfit_table(path):
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))['default_outfits']


def custom_outfits_path():
    return data_root() / 'outfit_defaults.json'


def outfit_preferences():
    built_in = read_outfit_table(Path(__file__).resolve().parents[1] / 'config' / 'seasonal_outfits.json')
    custom = read_outfit_table(custom_outfits_path())
    return dict(default_outfits={cid: {**built_in.get(cid, {}), **custom.get(cid, {})}
                                 for cid in dict.fromkeys([*built_in, *custom])}, overrides=custom)


def save_outfit_preference(cid, slot, outfit):
    path = custom_outfits_path()
    custom = read_outfit_table(path)
    if outfit is None:
        row = custom.get(cid, {})
        row.pop(slot, None)
        if not row:
            custom.pop(cid, None)
    else:
        custom.setdefault(cid, {})[slot] = outfit
    temporary = path.with_suffix(f'.{uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(dict(version=1, default_outfits=custom), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return outfit_preferences()


def load_seasonal_outfits():
    return outfit_preferences()['default_outfits']


def season_outfit(seasonal, outfit, cid, season):
    oid = seasonal.get(cid, {}).get('special' if season == DEFAULT_PROFILE else season)
    return oid if isinstance(oid, str) and outfit(cid, oid) else None


def apply_scene_outfit(scene, cid, oid):
    scene.setdefault('outfits', {})[cid] = oid
    for changes in scene.get('outfit_changes', {}).values():
        changes.pop(cid, None)


class StoryOutfits:
    def __init__(self, characters, outfit, outfits, selector):
        self.characters = characters
        self.outfit = outfit
        self.outfits = outfits
        self.selector = selector

    def valid(self, cid, oid):
        return isinstance(oid, str) and bool(self.outfit(cid, oid))

    async def profile(self, doc):
        saved = doc.get('outfit_profile')
        if saved in (*PROFILES, DEFAULT_PROFILE):
            return saved
        outline = doc.get('outline') or {}
        story = {key: doc[key] for key in ('premise', 'details', 'active_instruction') if doc.get(key)}
        start = doc.get('outfit_scope_start', 0)
        if start:
            story['outline'] = {'direction': doc.get('continuation_direction', '')}
        else:
            story['outline'] = {key: outline[key] for key in ('opening', 'direction') if outline.get(key)}
        story['outline']['scenes'] = [
            {key: scene[key] for key in ('place', 'direction') if scene.get(key)}
            for scene in outline.get('scenes', [])[start:]
        ]
        from asset_index import parse_object
        profile = DEFAULT_PROFILE
        try:
            choice = parse_object(await self.selector.complete(SEASON_PROMPT, json.dumps(story, ensure_ascii=False)))
            if choice.get('outfit_profile') in PROFILES:
                profile = choice['outfit_profile']
        except Exception as error:
            logging.getLogger(__name__).warning('Outfit season classification fell back to default (%s)', type(error).__name__)
        doc['outfit_profile'] = profile
        return profile

    def default(self, cid, profile, seasonal):
        original = DAILY_DEFAULTS.get(cid, getattr(self.characters.get(cid), 'default_outfit', None))
        if oid := season_outfit(seasonal, self.outfit, cid, profile):
            return oid
        if profile in SEASONS and (oid := season_outfit(seasonal, self.outfit, cid, DEFAULT_PROFILE)):
            return oid
        if automatic_outfit(original) and self.valid(cid, original):
            return original
        return next((row['id'] for row in self.outfits(cid) if automatic_outfit(row['id'])), None)

    async def scene_choices(self, doc, scene, actors, previous, fixed):
        outfits = dict(scene.get('outfits') or {})
        seasonal = load_seasonal_outfits()
        mode = doc.get('outfit_mode', 'story')
        local = mode in LOCAL_MODES
        requested = doc.get('requested_outfits') or {}
        pending = []
        for cid in actors:
            inherited = () if local else (fixed.get(cid), previous.get(cid))
            selected = next((oid for oid in (outfits.get(cid), requested.get(cid), *inherited)
                             if self.valid(cid, oid)), None)
            if selected is not None:
                outfits[cid] = selected
            else:
                outfits.pop(cid, None)
                pending.append(cid)
        profile = mode if local else DEFAULT_PROFILE
        if not local and any(self.valid(cid, oid)
               for cid in pending for oid in seasonal.get(cid, {}).values()):
            profile = await self.profile(doc)
        for cid in pending:
            oid = self.default(cid, profile, seasonal)
            if oid:
                outfits[cid] = oid
        return outfits
