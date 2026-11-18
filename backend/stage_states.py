"""Resolve a shot's independent actor states, including scoped listener edits."""
import json
from functools import lru_cache

from story_thoughts import is_story_thought

def cast_for(lines, index, cast):
    line = lines[index]
    def ordered(visible):
        return list(dict.fromkeys([cid for cid in line.get('actor_order', []) if cid in visible] + visible))
    if 'screen_cast' in line:
        return ordered([cid for cid in line['screen_cast'] if cid in cast][:7])
    present = set(line.get('onstage', cast))
    present.add(line.get('speaker'))
    chosen = []
    def add(cid):
        if cid in cast and cid in present and cid not in chosen and len(chosen) < 5:
            chosen.append(cid)
    if line.get('speakers'):
        return ordered([cid for cid in cast if cid in line['speakers']][:7])
    add(line.get('speaker'))
    if line.get('focus'):
        anchor = index
        while anchor and lines[anchor - 1].get('speaker') == line.get('speaker'):
            anchor -= 1
        for cid in lines[anchor].get('focus') or line['focus']:
            add(cid)
    else:
        for entry in list(reversed(lines[max(0, index - 5):index])) + lines[index + 1:index + 5]:
            add(entry.get('speaker'))
    return ordered([cid for cid in cast if cid in chosen])



def visual_state_for(lines, index, cast, cid, cast_for, scope=''):
    carry = {}
    variant = 0
    entry_seed=idle_seed=''
    entry_idle=was_visible=was_idle=False
    manual_mood=False
    result = dict(mood='calm',variant=0,bodyMood=None,expression=None,bodyAction=None)
    for i, line in enumerate(lines[:index + 1]):
        speaker = cid in (line.get('speakers') or [line.get('speaker')])
        if cid not in cast_for(lines, i, cast):
            carry = {}
            was_visible=False
            manual_mood=False
            if speaker:
                variant += 1
            continue
        if not was_visible:
            entry_seed=f"{scope}:{line.get('id')}:{cid}"
            idle_seed=entry_seed;entry_idle=not speaker;was_idle=False
        was_visible=True
        if speaker: entry_idle=False
        if speaker:
            carry = dict(emotion=line.get('emotion') or 'calm', body_mood=line.get('body_mood'),expression=line.get('expression'),body_action=line.get('body_action'),mouth_action=line.get('mouth_action'),rest_mouth_action=line.get('rest_mouth_action'))
            manual_mood=bool(line.get('body_mood') and line.get('performance_segments'))
            variant += 1
        current = dict(carry)
        current.update(line.get('actor_states', {}).get(cid) or {})
        if 'body_mood' in (line.get('actor_states',{}).get(cid) or {}):
            manual_mood=bool(current.get('body_mood') and line.get('performance_segments'))
        if i < index:
            for segment in line.get('performance_segments') or []:
                if speaker:
                    current.update(emotion=segment.get('emotion') or current.get('emotion', 'calm'), body_mood=segment.get('body_mood'))
                    manual_mood=bool(segment.get('body_mood'))
                    for field in ('expression','body_action','mouth_action','rest_mouth_action'): current[field]=segment.get(field)
                current.update(segment.get('actor_states', {}).get(cid) or {})
                if 'body_mood' in (segment.get('actor_states',{}).get(cid) or {}): manual_mood=bool(current.get('body_mood'))
        mood = current.get('emotion') or 'calm'
        body = current.get('body_mood')
        idle_only=not current.get('body_action') and not (manual_mood and body!='calm') and (entry_idle or (body or mood)=='calm')
        if idle_only and not was_idle and not entry_idle: idle_seed=f"{scope}:{line.get('id')}:{cid}:calm"
        was_idle=idle_only
        result = dict(mood=mood,variant=max(0,variant-1),bodyMood=body or None,expression=current.get('expression'),bodyAction=current.get('body_action'),mouthAction=current.get('mouth_action'),restMouthAction=current.get('rest_mouth_action'),manualIdle=bool(manual_mood and body=='calm'),manualBody=bool(current.get('body_action') or manual_mood and body!='calm'),entryIdle=entry_idle,idleOnly=bool(idle_only),entrySeed=entry_seed,idleSeed=idle_seed)
        if speaker or line.get('listener_state_persist', True):
            carry = current
    performance=(lines[index].get('performance_actions') or {}).get(cid)
    if performance:
        result.update(presetControlled=True,faceAction=result.get('expression') or performance['face'],
                      mouthAction=result.get('mouthAction') or performance['mouth'],restMouthAction=result.get('restMouthAction') or performance['rest'],
                      autoBodyAction=performance['body'],autoFaceAction=performance['face'],autoMouthAction=performance['mouth'],autoRestMouthAction=performance['rest'],
                      idleOnly=bool(result.get('manualIdle')),idleEntrance=bool(performance.get('entry_idle')))
        if not result.get('manualBody'): result['bodyAction']=performance['body']
    return result

def pose_for(lines,index,cast,cid,cast_for):
    state=visual_state_for(lines,index,cast,cid,cast_for)
    return state['mood'],state['variant'],state['bodyMood']


def scene_shots(scene, cast, scope):
    inputs = {key: scene[key] for key in ('lines', 'outfits', 'outfit_changes', 'background', 'background_changes') if key in scene}
    return _scene_shots(json.dumps(inputs, ensure_ascii=False), tuple(cast), scope)


@lru_cache(maxsize=128)
def _scene_shots(snapshot, cast, scope):
    scene = json.loads(snapshot)
    lines = scene['lines']
    visible = [cast_for(lines, index, cast) for index in range(len(lines))]
    def lookup(_lines, index, _cast):
        return visible[index]
    result = {}
    outfits = dict(scene.get('outfits') or {})
    outfit_sources = {}
    background = scene.get('background')
    background_source = None
    for index, line in enumerate(lines):
        lid = line['id']
        for cid, outfit in (scene.get('outfit_changes') or {}).get(lid, {}).items():
            outfits[cid] = outfit
            outfit_sources[cid] = lid
        if lid in (scene.get('background_changes') or {}):
            background = scene['background_changes'][lid]
            background_source = lid
        poses = {}
        for cid in visible[index]:
            pose = visual_state_for(lines, index, cast, cid, lookup, scope)
            pose['bodyVariant'] = pose.pop('variant')
            pose['bodyAction'] = pose.get('bodyAction') or (line.get('body_actions') or {}).get(cid)
            poses[cid] = pose
        result[lid] = dict(cast=visible[index], poses=poses, thought=is_story_thought(line),
                           outfits=outfits.copy(), outfitSources=outfit_sources.copy(),
                           background=background, backgroundSource=background_source)
    return result
