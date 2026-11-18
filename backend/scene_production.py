"""One auxiliary request per scene for background and body direction."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from asset_index import atomic_json, parse_object, provider_id
from body_director import (BodyDirectionError,frames_for,relevant_scene,resolve_actions,alternations,constrain_events)
from outfit_policy import LOCAL_MODES, StoryOutfits, apply_scene_outfit
from stage_states import cast_for
from story_thoughts import is_story_thought

VERSION='scene-production-v1'


class SceneProductionError(BodyDirectionError):
    def __init__(self,message,*,retryable=False):
        super().__init__(message)
        self.retryable=retryable


def instructions():
    return Path(__file__).with_name('SCENE_PRODUCTION_GUIDE.md').read_text(encoding='utf-8')


def participants(scene,cast):
    known=set(cast);result=set()
    for i,line in enumerate(scene['lines']):
        result.update(cid for cid in line.get('onstage',[]) if cid in known)
        result.update(cid for cid in (line.get('speakers') or [line.get('speaker')]) if cid in known)
        result.update(cast_for(scene['lines'],i,cast))
    return sorted(result)


def scene_key(scene,actors,mode,previous,history,fixed,*,legacy=False):
    value=dict(version=VERSION,scene=relevant_scene(scene,set(actors),legacy=legacy),mode=mode,
               previous={c:previous[c] for c in actors if c in previous},
               history={c:history[c] for c in actors if c in history})
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def content_key(scene,actors,mode):
    return scene_key(scene,actors,mode,{},{},{})


def reusable_content_key(scene,actors,mode,previous,history):
    """Reuse by verified scene inputs, independent of changes in preceding scenes."""
    plan=scene.get('scene_plan') or {}
    if not plan.get('input_key') or scene.get('scene_plan_invalidated') or 'background' not in scene:return None
    mode=plan.get('outfit_mode',mode)
    key=content_key(scene,actors,mode)
    if plan.get('content_key')==key:return key
    if plan['input_key']==scene_key(scene,actors,mode,previous,history,{}):return key
    if plan.get('version')==VERSION:
        if any(line.get('kind')=='thought' and not is_story_thought({**line,'kind':None}) for line in scene['lines']):return None
        expected=scene_key(scene,actors,mode,{}, {}, {},legacy=True) if 'content_key' in plan else scene_key(scene,actors,mode,previous,history,{},legacy=True)
        if plan.get('content_key',plan['input_key'])==expected:return key
    return None


def update_history(scene,history):
    for line in scene['lines']:
        for cid,action in line.get('body_actions',{}).items():
            if not history.get(cid) or history[cid][-1]!=action:
                history.setdefault(cid,[]).append(action);history[cid]=history[cid][-6:]


def ending_outfits(scene,previous):
    previous.update(scene.get('outfits') or {})
    for line in scene['lines']:
        previous.update(scene.get('outfit_changes',{}).get(line['id'],{}))


def apply_scene_outfits(doc, index, choices, apply_later):
    cast = doc['outline']['cast']
    mode = doc.get('outfit_mode', 'story')
    previous, history = {}, {}
    scenes = doc['scenes'] if apply_later else doc['scenes'][:index + 1]
    for position, scene in enumerate(scenes):
        if position >= index:
            actors = participants(scene, cast)
            verified = reusable_content_key(scene, actors, mode, previous, history)
        update_history(scene, history)
        ending_outfits(scene, previous)
        if position < index:
            continue
        for cid, oid in choices.items():
            apply_scene_outfit(scene, cid, oid)
        if verified is not None:
            plan = scene['scene_plan']
            plan['content_key'] = content_key(scene, actors, plan.get('outfit_mode', mode))


class SceneProduction:
    def __init__(self,project,directory):
        self.project=project;self.selector=project.background_selector
        self.directory=Path(directory)
        self.wardrobe_policy=StoryOutfits(project.characters,self.valid_outfit,lambda cid:project.wardrobe.outfits(cid),self.selector)

    def valid_outfit(self,cid,oid):
        return bool(isinstance(oid,str) and self.project.outfit(cid,oid))

    def fixed_choices(self,doc):
        fixed={cid:oid for cid,oid in doc.get('fixed_outfits',{}).items() if self.valid_outfit(cid,oid)}
        for scene in doc['scenes']:
            for cid,oid in scene.get('outfits',{}).items():
                if self.valid_outfit(cid,oid):fixed.setdefault(cid,oid)
        for cid,oid in doc.get('requested_outfits',{}).items():
            if self.valid_outfit(cid,oid):fixed.setdefault(cid,oid)
        return fixed

    def backgrounds_for(self,scene,previous):
        backgrounds=self.project.backgrounds
        indexed=[card for card in backgrounds.list() if card['indexed'] and card['type']!='solid']
        rows=backgrounds._candidates(scene,indexed,limit=8) if indexed else []
        for bid in [scene.get('background'),previous]:
            card=backgrounds.card(bid) if bid else None
            if card and not any(c['id']==card['id'] for c in rows):rows.insert(0,card)
        return [{k:row.get(k) for k in ('id','description','tags','time_of_day','place_group')} for row in rows]

    def legacy_complete(self,doc,scene,actors,catalog):
        if not doc.get('production_version') or 'background' not in scene or scene.get('scene_plan_invalidated'):return False
        if scene['background'] is not None and not self.project.backgrounds.image_path(scene['background']):return False
        for cid in actors:
            if self.project.wardrobe.outfits(cid) and not self.valid_outfit(cid,scene.get('outfits',{}).get(cid)):return False
        if scene.get('body_plan'):
            menus={cid:{a['name']:a for a in catalog[cid]['actions'] if a.get('sustained',True)} for cid in actors if cid in catalog and scene.get('outfits',{}).get(cid)==catalog[cid]['outfit']}
            frames=frames_for(scene,doc['outline']['cast'],menus,doc.get('id',''))
            for line,frame in zip(scene['lines'],frames):
                if any(line.get('body_actions',{}).get(cid) not in menus[cid] for cid in frame['visible']):return False
        return True

    def check_assets(self,scene):
        for cid,oid in scene.get('outfits',{}).items():
            if not self.valid_outfit(cid,oid):raise SceneProductionError('已保存的衣装素材缺失，请检查后继续')
        for bid in [scene.get('background'),*scene.get('background_changes',{}).values()]:
            if bid is not None and not self.project.backgrounds.image_path(bid):raise SceneProductionError('已保存的背景素材缺失，请检查后继续')

    def completed_prefix(self,doc):
        previous={};history={};completed=0
        mode=doc.get('outfit_mode','story')
        for scene in doc['scenes']:
            actors=participants(scene,doc['outline']['cast'])
            if reusable_content_key(scene,actors,mode,previous,history) is None:break
            try:self.check_assets(scene)
            except SceneProductionError:break
            update_history(scene,history);ending_outfits(scene,previous)
            completed+=1
        return completed

    def apply_result(self,raw,scene,doc,actors,backgrounds,previous_bg,catalog,history,number):
        if isinstance(raw,dict) and isinstance(raw.get('scenes'),list) and len(raw['scenes'])==1:raw=raw['scenes'][0]
        preset_director=getattr(self.project,'preset_director',None)
        event_key='performance_events' if preset_director is not None else 'body_events'
        if not isinstance(raw,dict) or raw.get('scene_number',number)!=number or not isinstance(raw.get(event_key),list):
            raise SceneProductionError('本幕联合准备结果不完整，已完成场景保留，继续会重试本幕',retryable=True)
        warnings=[]
        line_ids={line['id'] for line in scene['lines']}
        bgids={b['id'] for b in backgrounds}
        if 'background' not in scene:
            selected=raw.get('background')
            if selected not in bgids:
                selected=previous_bg if previous_bg in bgids else (backgrounds[0]['id'] if backgrounds else None)
                if bgids:warnings.append(dict(type='invalid-background-local-fallback'))
            scene['background']=selected
        bg_changes=deepcopy(scene.get('background_changes') or {})
        for lid,bid in (raw.get('background_changes') or {}).items():
            if lid in line_ids and bid in bgids:bg_changes[lid]=bid
        scene['background_changes']=bg_changes
        if preset_director is not None:
            after,extra=preset_director.apply(scene,doc,raw['performance_events'],history)
            return after,warnings+extra
        # A different costume is not assumed to share the annotated pose shape.
        menus={cid:{a['name']:a for a in catalog[cid]['actions'] if a.get('sustained',True)} for cid in actors if cid in catalog and self.valid_outfit(cid,catalog[cid]['outfit'])}
        frames=frames_for(scene,doc['outline']['cast'],menus,doc.get('id',''))
        costumes=dict(scene.get('outfits') or {})
        changes=scene.get('outfit_changes') or {}
        unannotated=set()
        for frame in frames:
            costumes.update(changes.get(frame['line_id'],{}))
            supported={cid for cid in menus if costumes.get(cid)==catalog[cid]['outfit']}
            unannotated.update(cid for cid in frame['visible'] if cid not in supported)
            frame['visible']=[cid for cid in frame['visible'] if cid in supported]
            frame['entering']=[cid for cid in frame['entering'] if cid in supported]
            frame['speakers']=[cid for cid in frame['speakers'] if cid in supported]
            frame['manual']={cid:v for cid,v in frame['manual'].items() if cid in supported}
        active={cid for f in frames for cid in f['visible']};menus={cid:m for cid,m in menus.items() if cid in active}
        warnings.extend(dict(type='unannotated-outfit-uses-existing-motion-rules',actor=cid) for cid in sorted(unannotated))
        eligible={f['line_id']:f for f in frames};safe=[];ignored=[]
        for event in raw['body_events']:
            if isinstance(event,dict) and (event.get('actor') not in menus or event.get('action') not in menus.get(event.get('actor'),{}) or event.get('line_id') not in eligible):
                ignored.append(dict(type='body-candidate-not-available',line_id=event.get('line_id'),actor=event.get('actor'),action=event.get('action')));continue
            try:
                accepted,dropped=constrain_events({'events':[event]},frames,menus);safe.extend(accepted);ignored.extend(dropped)
            except (ValueError,KeyError,TypeError):
                ignored.append(dict(type='invalid-body-event-ignored'))
        # Avoid duplicate triggers while retaining the first validated decision.
        safe=list({(e['line_id'],e['actor']):e for e in reversed(safe)}.values())
        safe.sort(key=lambda e:next(i for i,f in enumerate(frames) if f['line_id']==e['line_id']))
        resolved,after,applied=resolve_actions(frames,safe,menus,history)
        for line in scene['lines']:line['body_actions']=resolved[line['id']]
        scene['body_plan']=dict(version=VERSION,model=self.selector.model,source='combined-scene',events=applied,
                                faces_preserved=True,quality_reviewed=True,history_before=deepcopy(history),history_after=after,
                                ignored_events=ignored,alternations_remaining=alternations(resolved))
        return after,warnings+ignored

    async def prepare(self,doc,checkpoint=None,progress=None):
        director=self.project.body_director
        catalog_path=director.catalog_source() if hasattr(director,'catalog_source') else director.catalog_path
        data=json.loads(catalog_path.read_text(encoding='utf-8')) if catalog_path.is_file() else {'characters':[]}
        catalog={c['character_id']:c for c in data['characters']}
        mode=doc.get('outfit_mode','story')
        if mode not in ('fixed','story',*LOCAL_MODES):raise SceneProductionError('衣装模式无效')
        fixed=self.fixed_choices(doc) if mode=='fixed' else {}
        scope_start=doc.get('outfit_scope_start',0)
        previous={};wardrobe_previous={};history={};previous_bg=None
        for number,scene in enumerate(doc['scenes'],1):
            actors=participants(scene,doc['outline']['cast'])
            existing=scene.get('scene_plan') or {}
            saved_key=reusable_content_key(scene,actors,mode,previous,history)
            if saved_key is not None or (not existing and self.legacy_complete(doc,scene,actors,catalog)):
                self.check_assets(scene)
                if not existing:
                    scene['scene_plan']=dict(version=VERSION,input_key=scene_key(scene,actors,mode,previous,history,fixed),outfit_mode=mode,source='existing-scene-preserved',requests=0)
                scene['scene_plan']['content_key']=saved_key or content_key(scene,actors,mode)
                update_history(scene,history);ending_outfits(scene,previous)
                if number>scope_start:ending_outfits(scene,wardrobe_previous)
                previous_bg=scene.get('background',previous_bg)
                for line in scene['lines']:previous_bg=scene.get('background_changes',{}).get(line['id'],previous_bg)
                if mode=='fixed':doc['fixed_outfits']=dict(fixed)
                if checkpoint:checkpoint(number)
                continue
            candidate=deepcopy(scene)
            next_fixed=dict(fixed)
            scoped=list(dict.fromkeys([*actors,*[cid for cid in doc['outline']['cast'] if cid not in fixed]])) if mode=='fixed' else actors
            candidate['outfits']=await self.wardrobe_policy.scene_choices(doc,scene,scoped,wardrobe_previous if number>scope_start else previous,fixed)
            if mode=='fixed':
                for cid,oid in candidate['outfits'].items():next_fixed.setdefault(cid,oid)
            backgrounds=self.backgrounds_for(scene,previous_bg)
            menus={cid:{a['name']:a for a in catalog[cid]['actions'] if a.get('sustained',True)} for cid in actors if cid in catalog and self.valid_outfit(cid,catalog[cid]['outfit'])}
            frames=frames_for(scene,doc['outline']['cast'],menus,doc.get('id',''))
            body_candidates={cid:dict(annotated_outfit=catalog[cid]['outfit'],actions=[dict(action=a['name'],pose=a['observation']['description'],source_emotion=a.get('source_emotion'),intensity=a['observation'].get('intensity'),
                              fits=[dict(emotion=v['emotion'],when=v['condition'],confidence=v.get('confidence'),basis=v.get('basis')) for v in a['observation']['compatible']],avoid=a['observation']['avoid']) for a in menu.values()]) for cid,menu in menus.items()}
            payload=dict(scene_number=number,place=scene['place'],premise=doc['premise'],
                         existing_background=scene.get('background'),previous_background=previous_bg,background_candidates=backgrounds,
                         body_candidates=body_candidates,recent_actions={c:history[c] for c in actors if c in history},frames=[{k:v for k,v in f.items() if k!='local_idle'} for f in frames],
                         dialogue=[{k:l[k] for k in ('id','speaker','speakers','ja','zh','emotion','expression','actor_states','performance_segments') if k in l} for l in scene['lines']])
            preset_director=getattr(self.project,'preset_director',None)
            if preset_director is not None:
                candidates,frames=preset_director.candidates(candidate,doc['outline']['cast'],actors,doc.get('id',''),history)
                spoken=preset_director.recent_spoken(doc['scenes'][:number-1],actors)
                for cid,value in candidates.items():value['recent_spoken']=spoken.get(cid,[])
                payload.pop('body_candidates',None)
                payload.update(performance_candidates=candidates,frames=frames,preset_catalog_hash=preset_director.fingerprint)
            cache_id=hashlib.sha256(json.dumps(dict(provider=provider_id(self.selector),guide=instructions(),payload=payload),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            cache=self.directory/'choices'/(cache_id+'.json')
            if cache.is_file():raw=json.loads(cache.read_text(encoding='utf-8'));source='cached-combined-scene'
            elif not backgrounds and not (payload.get('performance_candidates') if preset_director is not None else menus):
                raw=dict(scene_number=number,background=None,**{('performance_events' if preset_director is not None else 'body_events'):[]});source='local-no-assets'
            else:
                reply=None
                from datetime import datetime,UTC
                started=datetime.now(UTC).isoformat()
                if progress:progress(dict(scene_number=number,status='waiting-model',started_at=started))
                try:
                    reply=await self.selector.complete(instructions(),json.dumps(payload,ensure_ascii=False)+'\n'+instructions())
                    raw=parse_object(reply)
                except Exception as error:
                    atomic_json(self.directory/'diagnostics'/(cache_id+'.json'),dict(scene_number=number,started_at=started,finished_at=datetime.now(UTC).isoformat(),response=reply,error_type=type(error).__name__,cause_type=type(error.__cause__).__name__ if error.__cause__ else None))
                    from production_state import transient
                    retryable=isinstance(error,TimeoutError) or transient(error) or (reply is not None and isinstance(error,ValueError))
                    reason='辅助模型等待超时' if isinstance(error,TimeoutError) else '辅助模型请求或返回校验未完成'
                    raise SceneProductionError(f'第{number}幕{reason}，已完成的幕保留，可重试未完成场景',retryable=retryable) from None
                source='combined-scene'
            after,warnings=self.apply_result(raw,candidate,doc,actors,backgrounds,previous_bg,catalog,history,number)
            # Commit the complete scene atomically, never partially apply output.
            scene.clear();scene.update(candidate);fixed=next_fixed
            scene.pop('scene_plan_invalidated', None)
            scene['scene_plan']=dict(version=VERSION,input_key=scene_key(scene,actors,mode,previous,history,fixed),
                                     content_key=content_key(scene,actors,mode),
                                     outfit_mode=mode,source=source,model=self.selector.model,requests=1 if source=='combined-scene' else 0,warnings=warnings)
            atomic_json(cache,raw)
            history=after;ending_outfits(scene,previous)
            if number>scope_start:ending_outfits(scene,wardrobe_previous)
            previous_bg=scene.get('background',previous_bg)
            for line in scene['lines']:previous_bg=scene.get('background_changes',{}).get(line['id'],previous_bg)
            if mode=='fixed':doc['fixed_outfits']=dict(fixed)
            if checkpoint:checkpoint(number)
            if progress:progress(dict(scene_number=number,status='complete'))
