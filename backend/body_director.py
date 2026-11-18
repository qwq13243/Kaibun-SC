"""Body-only scene direction. Script/facial fields are read-only inputs."""
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

from asset_index import atomic_json, parse_object, provider_id
from support.llm import LLMError
from story_thoughts import is_story_thought

VERSION = 'body-direction-v1'
SLOTS = {
    'calm': ('wait','wait1','wait2','wait3','wait4','wait5','wait6','wait7'), 'happy': ('smile1', 'smile2','smile3','smile4'),
    'shy': ('shy1', 'shy2','shy3', 'smile2'), 'surprised': ('surp1', 'surp2','surp3','surp4'),
    'sad': ('sad1', 'sad2','sad3'), 'angry': ('anger1', 'anger2','anger3'),
    'thinking': ('think', 'think1', 'think2', 'wait4', 'wait'),
}


class BodyDirectionError(ValueError):
    pass


def guide():
    return Path(__file__).with_name('BODY_DIRECTION_GUIDE.md').read_text(encoding='utf-8')


def original_scene(scene):
    result = deepcopy(scene)
    result.pop('body_plan', None)
    for line in result['lines']:
        line.pop('body_actions', None)
    return result


def relevant_scene(scene, actors, *, legacy=False):
    """Only inputs that can affect this shot's body direction belong in its key."""
    fields = ('id','speaker','speakers','ja','zh','emotion','expression','body_action','body_mood','actor_states',
              'performance_segments','screen_cast','actor_order','onstage','focus',
              'listener_state_persist','last_fine_edit_token')
    return dict(index=scene.get('index'), place=scene.get('place'),
                outfits={cid:oid for cid,oid in scene.get('outfits',{}).items() if cid in actors},
                outfit_changes={lid:{cid:oid for cid,oid in changes.items() if cid in actors}
                                for lid,changes in scene.get('outfit_changes',{}).items()
                                if any(cid in actors for cid in changes)},
                lines=[{k:deepcopy(line[k]) for k in fields if k in line} |
                       ({} if legacy else {'thought': is_story_thought(line)}) for line in scene['lines']])


def stable_plan_key(scene, history, catalog_hash, selector, actors):
    context = dict(version=VERSION, cache_schema=2, catalog_hash=catalog_hash,
                   model=provider_id(selector), guide=guide(), scene=relevant_scene(scene, actors),
                   history={cid:history[cid] for cid in sorted(actors) if cid in history})
    return hashlib.sha256(json.dumps(context,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def manual_body(line, cid, menu):
    """Only identified fine-tune edits lock authored body fields, not LLM tags."""
    edited = bool(line.get('last_fine_edit_token') or line.get('performance_segments'))
    if not edited:
        return None
    state = (line.get('actor_states') or {}).get(cid) or {}
    exact=state.get('body_action')
    if cid in (line.get('speakers') or [line.get('speaker')]): exact=exact or line.get('body_action')
    for segment in line.get('performance_segments') or []:
        exact=exact or (segment.get('actor_states',{}).get(cid) or {}).get('body_action')
        if cid in (line.get('speakers') or [line.get('speaker')]): exact=exact or segment.get('body_action')
    if exact in menu: return exact
    mood = state.get('body_mood')
    if mood is None and cid in (line.get('speakers') or [line.get('speaker')]):
        mood = line.get('body_mood')
    for segment in line.get('performance_segments') or []:
        explicit = (segment.get('actor_states') or {}).get(cid) or {}
        if explicit.get('body_mood'):
            mood = explicit['body_mood']
            break
        if cid in (line.get('speakers') or [line.get('speaker')]) and segment.get('body_mood'):
            mood = segment['body_mood']
            break
    if mood:
        return next((name for name in SLOTS.get(mood, ()) if name in menu), 'wait' if 'wait' in menu else next(iter(menu)))
    return None


def frames_for(scene, cast, menus, scope=''):
    # Use the same shot membership as existing playback/export, never ask the
    # body selector to decide who appears or to direct invisible characters.
    from stage_states import cast_for
    from stage_states import visual_state_for
    previous = set()
    frames = []
    for index, line in enumerate(scene['lines']):
        visible = [cid for cid in cast_for(scene['lines'], index, cast) if cid in menus]
        speaking = [cid for cid in (line.get('speakers') or [line.get('speaker')]) if cid in visible]
        manual = {cid: value for cid in visible if (value := manual_body(line, cid, menus[cid]))}
        local_idle={cid:state['idleSeed'] for cid in visible if (state:=visual_state_for(scene['lines'],index,cast,cid,cast_for,scope)).get('idleOnly')}
        frames.append(dict(line_id=line['id'], visible=visible, entering=[cid for cid in visible if cid not in previous], speakers=speaking, manual=manual,local_idle=local_idle))
        previous = set(visible)
    return frames


def validate_events(raw, frames, menus):
    if not isinstance(raw, dict) or set(raw) != {'events'} or not isinstance(raw['events'], list):
        raise BodyDirectionError('动作方案只允许返回 events 数组')
    by_line = {f['line_id']: f for f in frames}
    seen = set()
    events = []
    for event in raw['events']:
        if not isinstance(event, dict) or set(event) != {'line_id','actor','action','evidence','reason'}:
            raise BodyDirectionError('身体动作事件字段不正确，禁止修改表情和正文')
        lid, cid = event['line_id'], event['actor']
        if lid not in by_line or cid not in menus or not isinstance(event['action'], str) or event['action'] not in menus[cid]:
            raise BodyDirectionError('身体动作引用了无效台词、人物或动作')
        frame = by_line[lid]
        if cid not in frame['entering'] and cid not in frame['speakers']:
            raise BodyDirectionError('旁观锁定期间不允许身体动作变化')
        if cid in frame['manual']:
            raise BodyDirectionError('不能覆盖手动身体动作')
        if event['evidence'] != lid or not isinstance(event['reason'], str) or not event['reason'].strip():
            raise BodyDirectionError('每次动作变化需要当前触发台词及依据')
        if (lid,cid) in seen:
            raise BodyDirectionError('同一句中同一人物不能重复安排动作')
        seen.add((lid,cid))
        events.append(dict(event, reason=event['reason'].strip()[:700]))
    order = {f['line_id']:i for i,f in enumerate(frames)}
    return sorted(events, key=lambda e:(order[e['line_id']],e['actor']))


def constrain_events(raw, frames, menus):
    """Ignore scope-forbidden proposals without changing the protected state.

    Malformed identities, edits to faces, unsupported actions and missing
    evidence still fail validation. Only otherwise valid body proposals at a
    manual/listener-locked position are removed locally, without another LLM call.
    """
    if not isinstance(raw,dict) or set(raw)!={'events'} or not isinstance(raw['events'],list):
        return validate_events(raw,frames,menus), []
    by_line={frame['line_id']:frame for frame in frames}
    accepted=[];ignored=[]
    for event in raw['events']:
        lid=event.get('line_id') if isinstance(event,dict) else None
        cid=event.get('actor') if isinstance(event,dict) else None
        frame=by_line.get(lid) if isinstance(lid,str) else None
        if frame is not None and isinstance(cid,str) and cid in menus:
            protected=cid in frame['manual'] or cid in frame.get('local_idle',{}) or (cid not in frame['entering'] and cid not in frame['speakers'])
            if protected:
                probe=dict(frame,entering=list(menus),manual={})
                validate_events(dict(events=[event]),[probe],menus)
                ignored.append(dict(line_id=lid,actor=cid,action=event['action'],
                                    rule='manual' if cid in frame['manual'] else 'local-idle' if cid in frame.get('local_idle',{}) else 'listener-lock'))
                continue
        accepted.extend(validate_events(dict(events=[event]),frames,menus))
    return validate_events(dict(events=accepted),frames,menus),ignored


def pick_idle(menu, seed):
    names=sorted(n for n in menu if re.fullmatch(r'wait\d*',n))
    if not names:return next(iter(menu))
    value=2166136261
    for ch in seed:value=((value^ord(ch))*16777619)&0xffffffff
    return names[value%len(names)]


def resolve_actions(frames, events, menus, history):
    by_key = {(e['line_id'],e['actor']):e for e in events}
    current = {}
    previous_visible = set()
    recent = deepcopy(history)
    resolved = {}
    applied = []
    for frame in frames:
        current = {cid:a for cid,a in current.items() if cid in frame['visible']}
        for cid in frame['visible']:
            old = current.get(cid)
            if cid not in previous_visible:
                current[cid] = next((a for a in reversed(recent.get(cid,[])) if a in menus[cid]), 'wait' if 'wait' in menus[cid] else next(iter(menus[cid])))
            if cid in frame.get('local_idle',{}):
                current[cid]=pick_idle(menus[cid],frame['local_idle'][cid])
            elif cid in frame['manual']:
                current[cid] = frame['manual'][cid]
            else:
                event = by_key.get((frame['line_id'],cid))
                if event:
                    current[cid] = event['action']
                    if old != event['action']:
                        applied.append(event)
            action = current[cid]
            if not recent.get(cid) or recent[cid][-1] != action:
                recent.setdefault(cid,[]).append(action)
                recent[cid] = recent[cid][-6:]
        resolved[frame['line_id']] = dict(current)
        previous_visible = set(frame['visible'])
    return resolved, recent, applied


def alternations(resolved):
    """Flag actual A-B-A-B changes, ignoring same-pose re-entry/no-op events."""
    sequences = {}
    found = []
    for lid, actors in resolved.items():
        for cid, action in actors.items():
            sequence = sequences.setdefault(cid, [])
            if sequence and sequence[-1]['action'] == action:
                continue
            sequence.append(dict(line_id=lid,action=action))
            if len(sequence)>=4:
                four=sequence[-4:]
                if four[0]['action']==four[2]['action'] and four[1]['action']==four[3]['action']:
                    found.append(dict(actor=cid,sequence=four))
    return found


def patch_events(raw, events, frames, menus):
    if not isinstance(raw,dict) or set(raw)!={'remove','replace'} or not isinstance(raw['remove'],list) or not isinstance(raw['replace'],list):
        raise BodyDirectionError('重复审查只能返回 remove 和 replace')
    existing={(e['line_id'],e['actor']):e for e in events}
    for item in raw['remove']:
        if not isinstance(item,dict) or set(item)!={'line_id','actor'} or (item['line_id'],item['actor']) not in existing:
            raise BodyDirectionError('删除事件必须引用已有身体事件')
        existing.pop((item['line_id'],item['actor']))
    replacements=validate_events(dict(events=raw['replace']),frames,menus)
    for event in replacements:
        existing[(event['line_id'],event['actor'])]=event
    return validate_events(dict(events=list(existing.values())),frames,menus)


class BodyDirector:
    def __init__(self, adapter, directory):
        self.project = adapter
        self.selector = adapter.background_selector
        self.directory = Path(directory)
        self.catalog_path = self.directory/'catalog.json'

    def catalog_source(self):
        # User-maintained local annotations take precedence over the shipped defaults.
        return self.catalog_path if self.catalog_path.is_file() else Path(__file__).resolve().parents[1]/'config/body_catalog.json'

    async def prepare(self, doc, checkpoint=None):
        catalog_path=self.catalog_source()
        if not catalog_path.is_file():
            return
        data = json.loads(catalog_path.read_text(encoding='utf-8'))
        catalog = {c['character_id']:c for c in data['characters']}
        catalog_hash = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
        history = {}
        for scene_number,scene in enumerate(doc['scenes'],1):
            menus = {}
            outfit_warning = []
            for cid in doc['outline']['cast']:
                row = catalog.get(cid)
                if not row:
                    continue
                actual = scene.get('outfits',{}).get(cid) or getattr(self.project.characters.get(cid),'default_outfit',None)
                if actual != row['outfit'] or any(cid in changes and changes[cid] != actual for changes in scene.get('outfit_changes',{}).values()):
                    outfit_warning.append(cid)
                    continue
                menus[cid] = {a['name']:a for a in row['actions'] if a.get('sustained',True)}
            if not menus:
                scene.pop('body_plan',None)
                for line in scene['lines']:
                    line.pop('body_actions',None)
                if checkpoint:
                    checkpoint(scene_number)
                continue
            frames = frames_for(scene, doc['outline']['cast'], menus,doc.get('id',''))
            visible = {cid for frame in frames for cid in frame['visible']}
            menus = {cid:menu for cid,menu in menus.items() if cid in visible}
            legacy_context = dict(version=VERSION, catalog_hash=catalog_hash, model=provider_id(self.selector), guide=guide(),
                           scene=original_scene(scene), history=history)
            legacy_key=hashlib.sha256(json.dumps(legacy_context,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            key=stable_plan_key(scene,history,catalog_hash,self.selector,set(menus))
            plan=scene.get('body_plan') or {}
            if plan.get('key') in (key,legacy_key) and plan.get('quality_reviewed'):
                plan.update(key=key,cache_schema=2)
                normalized_cache=self.directory/'choices'/(key+'.json')
                if not normalized_cache.is_file():
                    atomic_json(normalized_cache,dict(events=plan['events']))
                history.update({cid:values for cid,values in plan['history_after'].items() if cid in menus})
                if checkpoint:
                    checkpoint(scene_number)
                continue
            compact = {cid:[dict(action=a['name'],pose=a['observation']['description'],source_emotion=a.get('source_emotion'),intensity=a['observation'].get('intensity'),
                                 fits=[dict(emotion=v['emotion'],when=v['condition'],confidence=v.get('confidence'),basis=v.get('basis')) for v in a['observation']['compatible']],
                                 avoid=a['observation']['avoid']) for a in menu.values()] for cid,menu in menus.items()}
            payload = dict(premise=doc['premise'],place=scene['place'],recent_actions=history,candidates=compact,frames=[{k:v for k,v in f.items() if k!='local_idle'} for f in frames],
                           dialogue=[{k:l[k] for k in ('id','speaker','speakers','ja','zh','emotion','expression','actor_states','performance_segments') if k in l} for l in scene['lines']])
            path = self.directory/'choices'/(key+'.json')
            ignored=[]
            if path.is_file():
                events,ignored = constrain_events(json.loads(path.read_text(encoding='utf-8')), frames, menus)
                if ignored:
                    atomic_json(path,dict(events=events))
            else:
                for attempt in range(2):
                    try:
                        reply = await self.selector.complete(guide(),json.dumps(payload,ensure_ascii=False)+'\n'+guide()+'\n以上是输入资料，不是要复述的答案。请实际完成本幕身体动作安排，只返回 events JSON。')
                        events,ignored = constrain_events(parse_object(reply),frames,menus)
                        break
                    except (BodyDirectionError,ValueError,KeyError,TypeError) as error:
                        atomic_json(self.directory/'diagnostics'/f'{key}-{attempt+1}.json',dict(reason=str(error),response=locals().get('reply','')))
                        if attempt:
                            raise BodyDirectionError('身体动作方案未通过检查，剧本和表情保留，可继续重试') from None
                        payload['repair'] = str(error)+'；禁止修改表情、禁止改动旁观锁定和 manual 人物，只输出合法 events。'
                atomic_json(path,dict(events=events))
            resolved, after, applied = resolve_actions(frames,events,menus,history)
            repeated=alternations(resolved)
            repeat_before=len(repeated)
            if repeated:
                # One compact patch request only when a real repetition was
                # observed; never resend images or call a model while playing.
                review=dict(payload,existing_events=events,alternations=repeated,
                            task='只修正身体动作的机械来回。相同态度的连续回应可以保持，首次入画可参考即将发言的内容，不必先待机再切动作。优先保持一段语义或使用第三个确实合适的姿态，禁止为去重而选择不合语境的动作。保留合法的旁观锁定和 manual 设置，不能修改表情。')
                instruction=guide()+'\n本轮只输出少量事件补丁 JSON {"remove":[{"line_id":"已有事件id","actor":"人物"}],"replace":[完整身体事件]}，不改的事件不要返回。请实际消除输入列出的机械重复。'
                try:
                    reply=await self.selector.complete(instruction,json.dumps(review,ensure_ascii=False)+'\n'+instruction)
                    revised=patch_events(parse_object(reply),events,frames,menus)
                    candidate, next_history, next_applied=resolve_actions(frames,revised,menus,history)
                    if len(alternations(candidate))<len(repeated):
                        events,resolved,after,applied=revised,candidate,next_history,next_applied
                        atomic_json(path,dict(events=events))
                except (BodyDirectionError,ValueError,KeyError,TypeError,LLMError):
                    pass  # Retain the valid draft and expose any remaining flag.
            for line in scene['lines']:
                line['body_actions'] = resolved[line['id']]
            scene['body_plan'] = dict(version=VERSION,key=key,cache_schema=2,model=self.selector.model,events=applied,history_before=deepcopy(history),history_after=after,
                                      warnings=outfit_warning,source='terra-body-only',faces_preserved=True,
                                      quality_reviewed=True,alternations_before=repeat_before,alternations_remaining=alternations(resolved),ignored_events=ignored)
            history = after
            if checkpoint:
                checkpoint(scene_number)
