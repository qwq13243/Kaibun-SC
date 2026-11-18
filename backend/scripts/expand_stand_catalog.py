"""Inventory installed ShinyColorsDB clips; optionally verify default outfits upstream.

No model calls. Keeps runtime assets out of the source catalog. Existing observations
are reused only by their exact character, outfit, action and sampled image.
"""
import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import ROOT
from asset_index import atomic_json
from visual_fields import is_hold_action

LABELS = {'wait':'待机','anger':'生气','sad':'难过','shy':'害羞','smile':'微笑','surp':'惊讶',
          'think':'思考','serious':'认真','cry':'哭泣','tear':'含泪','close':'闭眼','doubt':'疑惑',
          'hello':'招呼','pain':'疼痛','sleep':'睡眠','skill':'技能姿态','touch':'触碰反应',
          'wink':'眨眼','doya':'得意','cheat':'狡黠','awkward':'尴尬','bad':'不悦','jito':'半眯眼',
          'down':'低落','excite':'兴奋','rebellion':'反抗','joy':'喜悦','puzzle':'困惑',
          'bitter_smile':'苦笑','fear':'害怕','nothing':'无表情','panic':'慌张','trouble':'为难',
          'left':'看向左侧','right':'看向右侧','anger_strong':'强烈生气','shy_surp':'害羞惊讶',
          'shrug':'耸肩','sigh':'叹气','why':'疑问','yawn':'呵欠','decide':'决意','idol':'偶像姿态',
          'red':'脸红','hero':'英雄姿态','play':'玩耍','arm_up_R':'右臂抬起','mask_take':'取口罩'}
SLOTS = {'wait':'calm','anger':'angry','sad':'sad','shy':'shy','smile':'happy','surp':'surprised','think':'thinking','serious':'thinking'}

def label(name):
    key = name.removeprefix('face_')
    stem = re.sub(r'\d+$', '', key)
    return LABELS.get(stem, stem) + key[len(stem):]

def category(name, raw):
    if name.startswith('face_'): return 'expression'
    if name.startswith('lip_'): return 'mouth'
    if name.startswith('eye_'): return 'gaze'
    if name == 'blank' or name in ('hi_off','hi_on','off','on','mask_off','mask_on','cheek_red'): return 'auxiliary'
    if name.startswith(('arm_down','wait_return')) or name.endswith(('_relay','_rtn')): return 'transition'
    if re.fullmatch(r'(yes|no)\d*', name): return 'gesture'
    return 'pose'

def metadata(name, raw):
    times=[]
    def walk(v):
        if isinstance(v, dict):
            if isinstance(v.get('time'),(int,float)): times.append(v['time'])
            for child in v.values(): walk(child)
        elif isinstance(v,list):
            for child in v: walk(child)
    walk(raw)
    duration=max(times,default=0)
    loop=next((e['time'] for e in raw.get('events',[]) if e['name']=='loop_start'),None)
    kind=category(name,raw)
    sustained=is_hold_action(name,raw)
    sample=min(loop+.15,max(0,duration-.01)) if loop is not None else max(0,duration-.01)
    stem=re.sub(r'\d+$','',name)
    return dict(name=name,label=label(name),kind=kind,duration=duration,loop_start=loop,
                sample_time=round(sample,4),sustained=sustained,source_category=SLOTS.get(stem))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-remote',action='store_true')
    parser.add_argument('--review-root',type=Path)
    args=parser.parse_args()
    catalog_path=ROOT/'data/body_direction/catalog.json'
    if not catalog_path.is_file(): catalog_path=ROOT/'config/body_catalog.json'
    previous=json.loads(catalog_path.read_text(encoding='utf-8'))
    base=args.review_root
    annotations=json.loads((base/'motion-annotations-v1.json').read_text(encoding='utf-8')) if base else {'characters':[]}
    by_character={c['character_id']:c for c in annotations['characters']}
    bodies=Counter();faces=Counter();outfits=0
    for p in (ROOT/'assets/spine/idols').glob('**/data.json'):
        raw=json.loads(p.read_text(encoding='utf-8')).get('animations',{})
        outfits+=1
        bodies.update(n for n,v in raw.items() if is_hold_action(n,v))
        faces.update(n for n in raw if n.startswith('face_'))
    checks=[];characters=[];expanded=[]
    if args.verify_remote:
        from scripts.fetch_stands import Fetcher
        fetcher=Fetcher(1)
    for old in previous['characters']:
        cid=old['character_id'];oid=old['outfit']
        path=ROOT/'assets/spine/idols'/oid/'data.json'
        content=path.read_bytes();animations=json.loads(content)['animations']
        source='https://cf-static.shinycolors.moe/spine/idols/'+oid+'/data.json'
        if args.verify_remote:
            remote=fetcher.get(source)
            remote_names=set(json.loads(remote)['animations'])
            checks.append(dict(character=cid,outfit=oid,url=source,identical_animation_names=remote_names==set(animations),
                               remote_sha256=hashlib.sha256(remote).hexdigest(),local_sha256=hashlib.sha256(content).hexdigest()))
            if remote_names!=set(animations): raise RuntimeError('Upstream clip list changed for '+cid+'; inspect before replacing local assets')
        rows=[metadata(n,v) for n,v in animations.items()]
        characters.append(dict(character_id=cid,character_name=old['character_name'],outfit=oid,source_url=source,animations=rows))
        source_row=by_character.get(cid,{})
        observed={a['name']:a for a in source_row.get('actions',[]) if source_row.get('outfit')==oid}
        actions=[]
        for row in rows:
            if not row['sustained']: continue
            name=row['name'];a=copy.deepcopy(observed.get(name,{}))
            # The original full catalog's screenshot can be before the hold in
            # clips without a loop marker. Such frames are not called reviewed holds.
            a.update(row,id=cid+'/'+name,pose_number=len(actions)+1,
                     image='images/'+cid+'/'+name+'.png',neutral_face='face_wait',
                     evaluation_scope='sustained pose only' if row['sustained'] else 'no explicit sustained segment; reference only',
                     review_status='沿用初步标注',source_url=source)
            if observed.get(name) and row['sustained']:
                a['sample_time']=observed[name]['sample_time']
                a['observation_status']='existing-observation-pending-equal-review'
            else:
                a['observation']=dict(description='原始动作：'+row['label']+'；保持姿态尚待统一审查。',compatible=[],avoid=[],intensity='unreviewed',uncertain=True,
                                      limits=['只评估保持姿态；不据名称推断实际形态。'])
                a['observation_status']='unreviewed'
            a['slot']=row['source_category']
            a['slot_label']=(dict(calm='平静类',happy='开心类',shy='害羞类',surprised='惊讶类',sad='难过类',angry='生气类',thinking='思考类').get(row['source_category']) or '独立动作')
            if row['source_category']:
                a['source_emotion']=dict(emotion=row['source_category'],confidence='highest',basis='original-animation-category')
                a['observation']['compatible']=[dict(emotion=row['source_category'],condition='原始数据对应类别',face='搭配对应表情',confidence='highest',basis='original-animation-category'),
                    *[v for v in a['observation']['compatible'] if v['emotion']!=row['source_category']]]
            actions.append(a)
        expanded.append(dict(character_id=cid,character_name=old['character_name'],outfit=oid,actions=actions,
                             model=source_row.get('model'),basis='all installed body pose clips; equal review pending'))
    inventory=dict(version='stand-catalog-v1',source='https://github.com/ShinyColorsDB/ShinyColorsDB-SpineViewer',
                   scope='29 configured default outfits; global name whitelist from installed outfits',
                   installed_outfits=outfits,expressions=[dict(name=n,label=label(n),outfits=faces[n]) for n in sorted(faces)],
                   body_names=sorted(bodies),characters=characters)
    atomic_json(ROOT/'config/stand_catalog.json',inventory)
    # Plain TS keeps the standalone video renderer and test loader usable without JSON import assertions.
    (ROOT/'frontend/src/lib/standCatalog.ts').write_text('// Generated by backend/scripts/expand_stand_catalog.py. Original clip names only.\nexport const standCatalog = '+json.dumps(inventory,ensure_ascii=False)+';\n',encoding='utf-8')
    if base:
        out=base/'expanded';out.mkdir(exist_ok=True)
        expanded_doc=dict(version='motion-review-v3-all-poses',date='2026-11-17',status='draft-for-human-review',
                          scope='all body poses of all 29 default outfits; sustained frames only for hold review',
                          annotation_method='Existing observations retained as requested; no new Terra calls. Non-loop actions excluded.',characters=expanded)
        atomic_json(out/'motion-annotations-all.json',expanded_doc)
        atomic_json(out/'stand-catalog.json',inventory)
        if args.verify_remote or not (out/'upstream-verification.json').is_file(): atomic_json(out/'upstream-verification.json',checks)
    report=dict(default_outfits=len(characters),installed_outfits=outfits,default_body_poses=sum(len(c['actions']) for c in expanded),
                default_sustained_poses=sum(a['sustained'] for c in expanded for a in c['actions']),
                default_expressions=sum(a['kind']=='expression' for c in characters for a in c['animations']),global_expression_names=len(faces),
                verified_remote=len(checks),all_remote_names_match=all(c['identical_animation_names'] for c in checks),new_model_calls=0)
    atomic_json(ROOT/'.runtime/animation-catalog-expansion/report.json',report)
    print(json.dumps(report))

if __name__=='__main__': main()
