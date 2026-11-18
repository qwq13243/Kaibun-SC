"""Sentence-local visual cues. These never create dialogue or change voice identity."""
from copy import deepcopy
import re
from visual_fields import actor_state, expression_valid, body_valid, mouth_valid

MOODS = {'calm', 'happy', 'shy', 'surprised', 'sad', 'angry', 'thinking'}


def normalize_segments(segments, line, cast):
    if not isinstance(segments, list) or not 1 <= len(segments) <= 20:
        raise ValueError('每句请选择1至20个演出区间')
    result = []
    offset = 0
    for seg in segments:
        if not isinstance(seg, dict) or any(not isinstance(seg.get(k), str) or not seg[k].strip() for k in ('ja', 'zh')):
            raise ValueError('请填写每个区间的日文和中文')
        states = seg.get('actor_states', {})
        if not isinstance(states, dict):
            raise ValueError('人物演出设置无效')
        clean = {}
        for cid, state in states.items():
            if cid not in cast:
                raise ValueError('人物演出设置无效')
            clean[cid] = actor_state({k:(v or None) if k=='body_mood' else v for k,v in state.items()})
        emotion = seg.get('emotion', line.get('emotion', 'calm'))
        if emotion not in MOODS or seg.get('body_mood') not in MOODS | {None, ''}:
            raise ValueError('区间表情或动作无效')
        result.append(dict(ja=seg['ja'], zh=seg['zh'], emotion=emotion, body_mood=seg.get('body_mood') or None, actor_states=clean, start=offset))
        for field,check in (('expression',expression_valid),('body_action',body_valid),('mouth_action',mouth_valid),('rest_mouth_action',mouth_valid)):
            if field in seg:
                if not check(seg[field]): raise ValueError('区间表情或动作无效')
                result[-1][field]=seg[field]
        offset += len(seg['ja'])
    if ''.join(s['ja'] for s in result) != line['ja'] or ''.join(s['zh'] for s in result) != line['zh']:
        raise ValueError('演出区间必须完整对应本句台词，不能重复或遗漏')
    for seg in result:
        seg['start'] /= max(1, offset)
    return result


def restore_split_lines(doc):
    """Recognize only the old fine-tune suffix (12 hex); inserted dialogue uses 16."""
    count = 0
    for scene in doc.get('scenes', []):
        lines = scene['lines']
        # Merge deepest children first, preserving their original sequence and cues.
        for child in list(reversed(lines)):
            match = re.fullmatch(r'(.+)-n[0-9a-f]{12}', child['id'])
            if not match:
                continue
            parent = next((l for l in lines if l['id'] == match[1]), None)
            if parent is None:
                continue
            pos = lines.index(parent)
            group = [parent]
            for following in lines[pos + 1:]:
                if re.fullmatch(re.escape(parent['id']) + r'-n[0-9a-f]{12}', following['id']):
                    group.append(following)
                else:
                    break
            if len(group) == 1:
                continue
            segments = []
            for item in group:
                segments.extend(deepcopy(item.get('performance_segments') or [dict(ja=item['ja'], zh=item['zh'], emotion=item.get('emotion', 'calm'), body_mood=item.get('body_mood'), actor_states=item.get('actor_states', {}))]))
            parent['ja'] = ''.join(s['ja'] for s in segments)
            parent['zh'] = ''.join(s['zh'] for s in segments)
            # The original suffix is the evidence; never merge ordinary adjacent speech.
            parent['performance_segments'] = normalize_segments(segments, parent, doc['outline']['cast'])
            parent.pop('last_fine_edit_token', None)
            for item in group[1:]:
                lines.remove(item)
                for key in ('outfit_changes', 'background_changes', 'line_transitions', 'line_transition_lengths'):
                    value = scene.get(key, {}).pop(item['id'], None)
                    if value is not None:
                        scene[key].setdefault(parent['id'], value)
                count += 1
    return count


def recover_split_audio(store, adapter):
    """Reuse the original full audio, or join cached parts locally; no synthesis."""
    import json
    import os
    import subprocess
    from production import cached_audio
    from video_export import ffmpeg_path

    recovered = 0
    for snapshot in (store.directory / 'backups' / 'before-sentence-performance').glob('*.json'):
        old = json.loads(snapshot.read_text(encoding='utf-8'))
        doc = store.get(old['id'])
        if not doc:
            continue
        for scene in doc['scenes']:
            for line in scene['lines']:
                if not line.get('performance_segments'):
                    continue
                original_scene = next((s for s in old['scenes'] if s['index'] == scene['index']), None)
                if not original_scene:
                    continue
                parts = [l for l in original_scene['lines'] if l['id'] == line['id'] or re.fullmatch(re.escape(line['id']) + r'(?:-n[0-9a-f]{12})+', l['id'])]
                if len(parts) < 2 or ''.join(p['ja'] for p in parts) != line['ja']:
                    continue
                for cid in line.get('speakers') or [line['speaker']]:
                    target = dict(line, speaker=cid, id=line['id'] + '~' + cid) if line.get('speakers') else line
                    if cached_audio(store, adapter, doc['id'], target):
                        continue
                    sources = [cached_audio(store, adapter, doc['id'], dict(p, speaker=cid, id=p['id'] + '~' + cid) if p.get('speakers') else p) for p in parts]
                    if not all(sources):
                        continue
                    try:
                        signature, _ = adapter.voice_signature(target)
                        destination = store.audio_path(doc['id'], target['id'], signature)
                        temporary = destination.with_suffix('.restoring.mp3')
                        args = [ffmpeg_path(), '-y', '-v', 'error']
                        for source in sources:
                            args += ['-i', str(source)]
                        args += ['-filter_complex', ''.join(f'[{i}:a]' for i in range(len(sources))) + f'concat=n={len(sources)}:v=0:a=1[out]', '-map', '[out]', '-c:a', 'libmp3lame', str(temporary)]
                        result = subprocess.run(args, capture_output=True, creationflags=0x08000000 if os.name == 'nt' else 0)
                        if result.returncode == 0 and temporary.stat().st_size:
                            temporary.replace(destination)
                            recovered += 1
                        temporary.unlink(missing_ok=True)
                    except (OSError, ValueError):
                        continue
    return recovered
