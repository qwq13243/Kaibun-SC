"""Text-only preset candidates and deterministic application of scene selections."""
import hashlib
import json
import re
import unicodedata
from copy import deepcopy
from pathlib import Path

from body_director import frames_for, pick_idle, manual_body
from story_thoughts import is_story_thought
from stage_states import cast_for
from visual_fields import is_hold_action, speaking_mouth_valid

VERSION='performance-presets-v1'
MOODS=('calm','happy','shy','surprised','sad','angry','thinking')


def emotion_for(line,cid):
    value=((line.get('actor_states') or {}).get(cid) or {}).get('emotion')
    if not value and cid in (line.get('speakers') or [line.get('speaker')]):value=line.get('emotion')
    return value if value in MOODS else 'calm'


def matches_spoken_terms(preset,line,cid):
    terms=preset.get('requires_spoken_terms')
    if not terms:return True
    if is_story_thought(line) or cid not in (line.get('speakers') or [line.get('speaker')]):return False
    return any(term in unicodedata.normalize('NFKC',line.get(language) or '') for language in ('ja','zh') for term in terms)


class PresetDirector:
    def __init__(self,project,path=None):
        self.project=project
        self.path=Path(path or Path(__file__).resolve().parents[1]/'config/performance_presets.json')
        data=json.loads(self.path.read_text(encoding='utf-8')) if self.path.is_file() else {'characters':[]}
        self.rows={c['character_id']:c for c in data['characters']}
        self.fingerprint=hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.is_file() else ''
        compatibility_path=self.path.with_name('performance_outfit_compatibility.json')
        compatibility=json.loads(compatibility_path.read_text(encoding='utf-8')) if compatibility_path.is_file() else {}
        self.compatibility=compatibility.get('characters',{}) if compatibility.get('catalog_hash')==self.fingerprint else {}
        self._clips={};self._options={}

    def clips(self,oid):
        if oid not in self._clips:
            try:self._clips[oid]=json.loads((self.project.asset_root/'spine/idols'/oid/'data.json').read_text(encoding='utf-8'))['animations']
            except (AttributeError,OSError,ValueError,KeyError):self._clips[oid]={}
        return self._clips[oid]

    def options(self,cid,outfit=None):
        row=self.rows.get(cid)
        if not row:return {}
        outfit=outfit or row['outfit'];key=(cid,outfit)
        if key in self._options:return self._options[key]
        if not self.project.outfit(cid,outfit):return {}
        indexed=self.compatibility.get(cid,{}).get(outfit)
        compatible=set(indexed['presets']) if indexed is not None else None
        clips=self.clips(outfit) if compatible is None else None;result={}
        for p in row['presets']:
            if not p.get('auto_enabled',True):continue
            for index,v in enumerate([{},*p.get('variants',[])]):
                value=dict(p,**v);pid=p['id'] if index==0 else p['id']+'/v'+str(index)
                body=value['body'];face=value['face'];mouth=value['mouth'];rest=value['rest']
                if body in row.get('auto_excluded_bodies',[]):continue
                if compatible is not None:
                    if pid not in compatible:continue
                else:
                    if body not in clips or not is_hold_action(body,clips[body]) or re.fullmatch(r'touch\d*',body):continue
                    if any(name not in clips for name in [face,mouth,rest]):continue
                    if not speaking_mouth_valid(mouth,clips[mouth]):continue
                info=row.get('bodies',{}).get(body,{})
                result[pid]=dict(preset_id=pid,outfit=outfit,body=body,face=face,mouth=mouth,rest=rest,
                                 emotion=p.get('emotion'),kind=p.get('kind','emotion'),name=v.get('label',p['name']),
                                 intensity=value['intensity'],amplitude=v.get('amplitude',info.get('amplitude',value['amplitude'])) if index else value['amplitude'],
                                 condition=value['condition'],avoid=value.get('avoid',''),posture=info.get('description',''),
                                 pose_group=info.get('similarity_group',body),
                                 requires_spoken_terms=value.get('requires_spoken_terms'),
                                 action_meaning=value.get('action_meaning') or next((s.get('action_meaning',s['name']) for s in row['presets'] if s.get('kind')=='special' and s['body']==body),None),mouth_affect=value.get('mouth_affect'),review_note=value.get('review_note'))
        self._options[key]=result;return result

    def costumes(self,scene,actors,extra=None):
        result={}
        for cid in actors:
            if cid not in self.rows:continue
            outfits=[(scene.get('outfits') or {}).get(cid) or self.rows[cid]['outfit']]
            outfits.extend(change[cid] for change in (scene.get('outfit_changes') or {}).values() if cid in change)
            outfits.extend((extra or {}).get(cid,[]))
            result[cid]=list(dict.fromkeys(outfits))
        return result

    def menus(self,actors,costumes=None):
        return {cid:{name:{} for outfit in (costumes or {}).get(cid,[self.rows[cid]['outfit']])
                     for name,clip in self.clips(outfit).items() if is_hold_action(name,clip)}
                for cid in actors if cid in self.rows}

    def candidates(self,scene,cast,actors,scope,history,extra_outfits=None):
        costumes=self.costumes(scene,actors,extra_outfits);menus=self.menus(actors,costumes)
        frames=frames_for(scene,cast,menus,scope);moods={cid:set() for cid in menus};spoke={cid:[] for cid in menus}
        lines={l['id']:l for l in scene['lines']}
        for f in frames:
            for cid in f['speakers']:
                line=lines[f['line_id']]
                moods[cid].add(emotion_for(line,cid))
                if cid not in f['manual']:spoke[cid].append(line)
        result={}
        for cid,needed in moods.items():
            if not needed:continue
            options={}
            for outfit in costumes[cid]:
                for pid,p in self.options(cid,outfit).items():
                    if p['kind']=='special' or p['emotion'] in needed:
                        candidate=dict(p,available_outfits=[])
                        if p.get('requires_spoken_terms'):
                            candidate['eligible_line_ids']=[line['id'] for line in spoke[cid] if matches_spoken_terms(p,line,cid)]
                            if not candidate['eligible_line_ids']:continue
                        options.setdefault(pid,candidate)['available_outfits'].append(outfit)
            selected=list(options.values())
            if not selected:continue
            result[cid]=dict(annotation_outfit=self.rows[cid]['outfit'],
                            candidates=[{k:p[k] for k in ['preset_id','available_outfits','emotion','kind','name','intensity','amplitude','condition','avoid','body','posture','pose_group','action_meaning','mouth_affect','review_note','eligible_line_ids'] if p.get(k) is not None} for p in selected],
                            recent_bodies=[dict(body=n,pose_group=self.rows[cid].get('bodies',{}).get(n,{}).get('similarity_group',n),posture=self.rows[cid].get('bodies',{}).get(n,{}).get('description','')) for n in history.get(cid,[]) if not re.fullmatch(r'touch\d*',n)])
        compact_frames=[{k:v for k,v in f.items() if k!='local_idle'} for f in frames]
        return result,compact_frames

    def default(self,cid,mood,previous=None,outfit=None):
        choices=[p for p in self.options(cid,outfit).values() if p['kind']!='special' and p['emotion']==mood]
        if not choices:return None
        return next((p for p in choices if previous and p['body']==previous.get('body')),None) or next((p for p in choices if re.fullmatch(r'wait\d*',p['body'])),choices[0])

    @staticmethod
    def saved(p,**extra):
        result=dict({k:deepcopy(p[k]) for k in ['preset_id','outfit','body','face','mouth','rest','emotion','kind','name','intensity','amplitude','pose_group'] if k in p},**extra)
        if p.get('requires_spoken_terms'):result['requires_spoken_terms']=p['requires_spoken_terms'][:]
        return result

    def recent_spoken(self,scenes,actors):
        result={cid:[] for cid in actors}
        for scene in scenes:
            for line in scene['lines']:
                for cid in line.get('speakers') or [line.get('speaker')]:
                    if cid not in result:continue
                    p=(line.get('performance_actions') or {}).get(cid)
                    if not p or not isinstance(p.get('body'),str):continue
                    group=self.rows.get(cid,{}).get('bodies',{}).get(p['body'],{}).get('similarity_group',p['body'])
                    row=dict(line_id=line['id'],preset_id=p.get('preset_id',p['body']),body=p['body'],pose_group=group,emotion=emotion_for(line,cid),reason=p.get('reason','')[:150])
                    if result[cid] and all(result[cid][-1][k]==row[k] for k in ('preset_id','body','pose_group','emotion')):continue
                    result[cid].append(row);result[cid]=result[cid][-6:]
        return result

    def apply(self,scene,doc,events,history):
        if not isinstance(events,list):raise ValueError('演出结果缺少 performance_events')
        actors=set(cid for line in scene['lines'] for cid in cast_for(scene['lines'],scene['lines'].index(line),doc['outline']['cast']))
        menus=self.menus(actors,self.costumes(scene,actors));frames=frames_for(scene,doc['outline']['cast'],menus,doc.get('id',''))
        lines={l['id']:l for l in scene['lines']};by_line={f['line_id']:f for f in frames};by_key={};ignored=[]
        costumes=dict(scene.get('outfits') or {});supported={};by_outfit={}
        for f in frames:
            costumes.update((scene.get('outfit_changes') or {}).get(f['line_id'],{}))
            by_outfit[f['line_id']]=dict(costumes)
            supported[f['line_id']]={cid for cid in f['visible'] if costumes.get(cid) and self.options(cid,costumes[cid])}
        for e in events:
            if not isinstance(e,dict):ignored.append(dict(rule='invalid-event'));continue
            lid=e.get('line_id');cid=e.get('actor');pid=e.get('preset_id');f=by_line.get(lid)
            if f is None or cid not in menus or cid not in supported[lid]:ignored.append(dict(rule='unavailable',line_id=lid,actor=cid));continue
            if cid not in f['speakers'] or cid in f['manual']:ignored.append(dict(rule='listener-or-manual',line_id=lid,actor=cid));continue
            p=self.options(cid,by_outfit[lid][cid]).get(pid)
            if not p or (p['kind']!='special' and p['emotion']!=emotion_for(lines[lid],cid)):
                ignored.append(dict(rule='preset-outside-emotion',line_id=lid,actor=cid,preset_id=pid));continue
            if not matches_spoken_terms(p,lines[lid],cid):
                ignored.append(dict(rule='required-spoken-term-missing',line_id=lid,actor=cid,preset_id=pid));continue
            if e.get('evidence',lid)!=lid or not isinstance(e.get('reason'),str) or not e['reason'].strip():
                ignored.append(dict(rule='missing-evidence',line_id=lid,actor=cid));continue
            by_key.setdefault((lid,cid),dict(e,preset_id=pid,reason=e['reason'].strip()[:300]))
        current={};applied=[];fallbacks=[];after=deepcopy(history)
        for f in frames:
            lid=f['line_id'];line=lines[lid];visible=supported[lid]
            current={cid:p for cid,p in current.items() if cid in visible}
            for cid in visible:
                outfit=by_outfit[lid][cid]
                old=current.get(cid);mood=emotion_for(line,cid);speaking=cid in f['speakers']
                if old and old.get('requires_spoken_terms'):
                    current.pop(cid);old=None
                if old and old.get('outfit')!=outfit:
                    equivalent=self.options(cid,outfit).get(old['preset_id'])
                    if equivalent:current[cid]=self.saved(equivalent)
                    else:old=None
                if old is None:
                    p=self.default(cid,mood,outfit=outfit)
                    if p is None:continue
                    current[cid]=self.saved(p)
                    if not speaking:
                        idle={name:{} for name in menus[cid] if re.fullmatch(r'wait\d*',name)}
                        if idle:current[cid].update(body=pick_idle(idle,f"{doc.get('id','')}:{lid}:{cid}"),kind='entry_idle',entry_idle=True)
                if speaking:
                    event=by_key.get((lid,cid))
                    if event:
                        p=self.options(cid,outfit)[event['preset_id']];current[cid]=self.saved(p,reason=event['reason'])
                        if old is None or any(old.get(k)!=current[cid].get(k) for k in ['preset_id','body','face','mouth','rest']):applied.append(event)
                    elif old is None or current[cid].get('entry_idle'):
                        p=self.default(cid,mood,current[cid],outfit=outfit);current[cid]=self.saved(p,fallback=True)
                        if cid not in f['manual']:fallbacks.append(dict(line_id=lid,actor=cid,rule='base-preset-fallback'))
                elif old and 'emotion' in ((line.get('actor_states') or {}).get(cid) or {}) and mood!=old.get('emotion'):
                    p=next((p for p in self.options(cid,outfit).values() if p['kind']!='special' and p['emotion']==mood and p['body']==old['body']),None)
                    if p:
                        current[cid]=self.saved(p)
                        if old.get('entry_idle'):current[cid]['entry_idle']=True
                if cid in f['manual']:current[cid].update(body=f['manual'][cid],manual_body=True)
                action=current[cid]['body']
                if not after.get(cid) or after[cid][-1]!=action:after.setdefault(cid,[]).append(action);after[cid]=after[cid][-6:]
            line['performance_actions']=deepcopy(current)
            line['body_actions']={cid:p['body'] for cid,p in current.items()}
        local_fallback=sorted({cid for i,f in enumerate(frames) for cid in cast_for(scene['lines'],i,doc['outline']['cast']) if cid in self.rows and cid not in supported[f['line_id']]})
        scene['performance_plan']=dict(version=VERSION,catalog_hash=self.fingerprint,events=applied,ignored=ignored,fallbacks=fallbacks,local_fallback_actors=local_fallback,
                                       history_after=after,source='text-preset-selection',images_sent=0)
        return after,ignored+fallbacks
