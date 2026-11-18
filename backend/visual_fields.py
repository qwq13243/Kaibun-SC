"""Exact Spine expression/body selections, independent from speech emotion."""
import json
import re
from functools import lru_cache
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MOODS={'calm','happy','shy','surprised','sad','angry','thinking'}

def is_hold_action(name, raw):
    if re.fullmatch(r'touch\d*',name):return False
    if name.startswith(('face_','lip_','eye_','arm_down','wait_return')) or name.endswith(('_relay','_rtn')):
        return False
    if name in ('blank','hi_off','hi_on','off','on','mask_off','mask_on','cheek_red') or re.fullmatch(r'(yes|no)\d*',name):
        return False
    # Base wait is an authored whole-clip idle; numbered waits need a real hold marker.
    return name=='wait' or any(e.get('name')=='loop_start' for e in raw.get('events',[]))

@lru_cache(maxsize=1)
def catalog():
    return json.loads((ROOT/'config/stand_catalog.json').read_text(encoding='utf-8'))

def expression_valid(value):
    return value is None or value=='blank' or isinstance(value,str) and value in {r['name'] for r in catalog()['expressions']}

def mouth_valid(value):
    return value is None or isinstance(value,str) and bool(re.fullmatch(r'lip_[A-Za-z0-9_]+',value))


def speaking_mouth_valid(name, raw):
    """A speech clip must return to a prior mouth drawing after changing it."""
    if not isinstance(name,str) or not name.startswith('lip_') or re.search(r'_s\d*$',name) or re.fullmatch(r'lip_a\d*',name):return False
    changing_attachments=False
    for slot,tracks in raw.get('slots',{}).items():
        if not any(part in slot.lower() for part in ('mouth','mouse','lip')):continue
        seen=set();previous=object()
        for frame in tracks.get('attachment',[]):
            value=frame.get('name')
            if value==previous:continue
            if value in seen:return True
            seen.add(value);previous=value
        changing_attachments=changing_attachments or len(seen)>1
    if changing_attachments:return False
    # Some rigs animate a single mouth mesh through repeated deformations.
    for skin in raw.get('deform',{}).values():
        for slot,attachments in skin.items():
            if not any(part in slot.lower() for part in ('mouth','mouse','lip')):continue
            for frames in attachments.values():
                seen=set();previous=None
                for frame in frames:
                    value=json.dumps({k:v for k,v in frame.items() if k not in ('time','curve')},sort_keys=True)
                    if value==previous:continue
                    if value in seen:return True
                    seen.add(value);previous=value
    return False

def body_valid(value):
    return value is None or isinstance(value,str) and value in catalog()['body_names']

def actor_state(state):
    if not isinstance(state,dict) or set(state)-{'emotion','body_mood','expression','body_action','mouth_action','rest_mouth_action'}:
        raise ValueError('人物表情或动作字段无效')
    for key,value in state.items():
        valid=(value in MOODS if isinstance(value,str) else False) if key=='emotion' else \
              (value is None or isinstance(value,str) and value in MOODS) if key=='body_mood' else \
              expression_valid(value) if key=='expression' else mouth_valid(value) if key in ('mouth_action','rest_mouth_action') else body_valid(value)
        if not valid: raise ValueError('人物表情或动作无效')
    return dict(state)
