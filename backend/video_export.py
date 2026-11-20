"""Local MP4 export, reusing finished videos and cached voice files."""
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import nullcontext
import threading
import wave
import traceback
import uuid
from array import array
from pathlib import Path

from paths import SPINE_RUNTIME
from production import cached_audio, voice_inventory, voice_lines, voice_targets, Production
from stage_states import cast_for, scene_shots


def ffmpeg_path():
    found = os.environ.get('KAIBUN_FFMPEG') or shutil.which('ffmpeg')
    if found and Path(found).is_file():
        return str(found)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    local = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Python'
    candidates = list(local.glob('Python*/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg*.exe'))
    if candidates:
        return str(candidates[0])
    raise ValueError('未找到 FFmpeg，请安装或在 KAIBUN_FFMPEG 中指定路径')


def subtitle_pages(text, limit=60):
    import re
    pages = []
    while len(text) > limit:
        window = text[:limit]
        stops = [m.end() for m in re.finditer(r'[。！？!?；;，、,]', window)]
        split = stops[-1] if stops and stops[-1] >= limit // 3 else limit
        pages.append(text[:split]); text = text[split:]
    if text: pages.append(text)
    return pages or ['']


def calibration_lead(exe, video):
    """Locate the first real frame after the magenta leader, using decoded frame timestamps."""
    flags = 0x08000000 if os.name == 'nt' else 0
    result = subprocess.run([exe, '-hide_banner', '-v', 'info', '-i', str(video), '-t', '180',
                             '-vf', 'crop=16:16:0:0,scale=1:1,format=rgb24,showinfo',
                             '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1'],
                            capture_output=True, creationflags=flags)
    if result.returncode:
        raise ValueError('无法校准导出视频的音画起点，请重新导出')
    frames = result.stdout
    timestamps = [float(value) for value in re.findall(rb'pts_time:([\d.eE+-]+)', result.stderr)]
    marker_seen = False
    for index, timestamp in enumerate(timestamps):
        pixel = frames[index * 3:index * 3 + 3]
        if len(pixel) != 3:
            break
        marker = pixel[0] > 200 and pixel[1] < 65 and pixel[2] > 200
        if marker:
            marker_seen = True
        elif marker_seen:
            return timestamp
    raise ValueError('未找到完整的导出校准帧，请重新导出，不复用旧录制缓存')


def pose_for(lines, index, cast, cid):
    from stage_states import pose_for as resolve
    return resolve(lines, index, cast, cid, cast_for)


class VideoExport:
    def __init__(self, store, adapter, production=None):
        self.store, self.adapter = store, adapter
        self.jobs = {}
        self.tasks = {}
        self.cancel_events = {}
        self.directory = store.directory / 'exports'
        self.directory.mkdir(exist_ok=True)
        self.production = production or Production(store, adapter)

    async def stop_story(self, story_id):
        """Wait for recording threads as well as async voice work before cleanup."""
        keys = {key for table in (self.tasks, self.jobs, self.cancel_events) for key in table
                if key == story_id or re.fullmatch(re.escape(story_id) + r'-scene-\d+', key)}
        for key in keys:
            self.cancel_events.setdefault(key, threading.Event()).set()
        # Cancelling asyncio.to_thread does not stop its worker. Signal the
        # worker cooperatively, then await the job before deleting any output.
        await asyncio.gather(*(self.tasks[key] for key in keys if key in self.tasks), return_exceptions=True)
        for key in keys:
            self.tasks.pop(key, None)
            self.jobs.pop(key, None)
            self.cancel_events.pop(key, None)

    def fingerprint(self, doc):
        voices = []
        for line in voice_lines(doc, self.adapter):
            path = cached_audio(self.store, self.adapter, doc['id'], line)
            if not path:
                return None
            stat = path.stat()
            voices.append((line['id'], path.name, stat.st_size, stat.st_mtime_ns))
        data = {'version': 31, 'scenes': doc['scenes'], 'outline': doc.get('outline'), 'voices': voices}
        missing = self.requested_outfits(doc) - self.available_outfits(doc)
        if missing:
            data['unavailable_outfits'] = sorted(missing)
        return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def requested_outfits(self, doc):
        cast = (doc.get('outline') or {}).get('cast', [])
        outfits = {(cid, self.adapter.characters[cid].default_outfit) for cid in cast if cid in self.adapter.characters}
        for scene in doc['scenes']:
            for outfit_map in [scene.get('outfits') or {}, *(scene.get('outfit_changes') or {}).values()]:
                outfits.update(outfit_map.items())
        return {(cid, oid) for cid, oid in outfits if oid}

    def available_outfits(self, doc):
        if not SPINE_RUNTIME.is_file():
            return set()
        return {(cid, oid) for cid, oid in self.requested_outfits(doc) if self.adapter.outfit(cid, oid)}

    def output_path(self, doc):
        key = doc.get('export_id', doc['id'])
        try:
            name = json.loads((self.directory / (key + '-latest.json')).read_text(encoding='utf-8'))['file']
            if Path(name).name == name:
                return self.directory / name
        except (OSError, ValueError, KeyError): pass
        return self.directory / (key + '.mp4')

    def cached(self, doc):
        path = self.output_path(doc)
        meta = self.directory / (doc.get('export_id', doc['id']) + '.json')
        if doc.get('status') != 'ready' or not path.is_file() or not path.stat().st_size:
            return False
        try:
            fingerprint = self.fingerprint(doc)
            return bool(fingerprint and json.loads(meta.read_text(encoding='utf-8')).get('fingerprint') == fingerprint)
        except (OSError, ValueError):
            return False

    def status(self, doc):
        job = self.jobs.get(doc.get('export_id', doc['id']))
        if job and job.get('status') == 'working':
            return job
        if self.cached(doc):
            return {'status': 'ready', 'progress': 100, 'message': '已使用此前导出的视频'}
        return job if job and job.get('status') in ('failed', 'interrupted') else {'status': 'idle', 'progress': 0, 'message': ''}

    def start(self, doc):
        sid = doc.get('export_id', doc['id'])
        if sid in self.tasks and not self.tasks[sid].done():
            return self.jobs[sid]
        if self.cached(doc):
            return self.status(doc)
        self.cancel_events[sid] = threading.Event()
        self.jobs[sid] = {'status': 'working', 'message': '正在检查素材', 'progress': 0}
        self.tasks[sid] = asyncio.create_task(self.run(doc))
        return self.jobs[sid]

    async def run(self, doc):
        sid = doc['id']
        job = self.jobs[doc.get('export_id', sid)]
        try:
            exe = ffmpeg_path()
            targets, ready = voice_inventory(self.store, self.adapter, doc)
            missing = [line for line in targets if line['id'] not in ready]
            for index, line in enumerate(missing):
                if self.cancel_events[doc.get('export_id', sid)].is_set():
                    raise ValueError('导出已停止')
                job['message'] = f'正在补全缺失语音 {index + 1} / {len(missing)}'
                try:
                    await self.production.produce_voice(sid, line)
                except Exception:
                    raise ValueError('缺失语音补全失败，请检查语音服务后重试') from None
            fingerprint = self.fingerprint(doc)
            if self.cancel_events[doc.get('export_id', sid)].is_set():
                raise ValueError('导出已停止')
            entries = []
            for scene in doc['scenes']:
                for line in scene['lines']:
                    if line.get('speaker') == 'narrator':
                        continue
                    audios = []
                    for voice in voice_targets(line, self.adapter):
                        audio = cached_audio(self.store, self.adapter, sid, voice)
                        if not audio:
                            raise ValueError('有配音尚未准备好，请先继续完成这篇故事的语音')
                        audios.append((voice['speaker'], audio))
                    entries.append((scene, line, audios))
            if not entries:
                raise ValueError('这篇故事还没有可以导出的对话')
            job['message'] = '正在合成画面与声音'
            export_id = doc.get('export_id', sid)
            output = self.directory / (export_id + '.mp4')
            checkpoint = self.directory / 'checkpoints' / export_id / str(fingerprint)
            await asyncio.to_thread(self.record, doc, entries, exe, output, job, checkpoint)
            meta = self.directory / (export_id + '.json')
            temp = meta.with_suffix('.json.tmp')
            temp.write_text(json.dumps({'fingerprint': fingerprint}), encoding='utf-8')
            temp.replace(meta)
            await asyncio.to_thread(self.clear_checkpoint, checkpoint)
            job.update(status='ready', progress=100, message='导出完成')
        except Exception as exc:
            (self.directory / (doc.get('export_id', sid) + '-error.log')).write_text(traceback.format_exc(), encoding='utf-8')
            job.update(status='failed', message=str(exc) if isinstance(exc, ValueError) else '导出失败，请检查本地素材与视频组件后重试')

    def clear_checkpoint(self, checkpoint):
        try:
            root = self.directory.resolve() / 'checkpoints'
            expected = root / checkpoint.parent.name / checkpoint.name
            if checkpoint.resolve() == expected:
                shutil.rmtree(expected, ignore_errors=True)
                expected.parent.rmdir()
        except (OSError, RuntimeError):
            pass

    def record(self, doc, entries, exe, output, job, checkpoint):
        # Completed clips survive failures, cancellation and server restarts.
        checkpoint.mkdir(parents=True, exist_ok=True)
        parts = []
        for start in [0]:
            if self.cancel_events.get(doc.get('export_id', doc['id']), threading.Event()).is_set():
                raise ValueError('导出已暂停，已完成片段已保存')
            target = checkpoint / f'{start:05}.mp4'
            if not target.is_file() or not target.stat().st_size:
                part_doc = dict(doc, recording_directory=str(checkpoint / f'work-{start:05}'))
                job['message'] = f'正在录制第 {start + 1} 至 {len(entries)} 句，已完成片段会保留'
                self.record_part(part_doc, entries, exe, target, job)
            parts.append(target)
            job.update(progress=int(len(entries) / len(entries) * 90), message=f'已保存 {len(entries)} / {len(entries)} 句')
        listing = checkpoint / 'concat.txt'
        listing.write_text(''.join("file '" + part.as_posix().replace("'", "'\\''") + "'\n" for part in parts), encoding='utf-8')
        job['message'] = '正在封装已保存片段'
        final = checkpoint / ('complete-' + uuid.uuid4().hex + '.mp4')
        result = subprocess.run([exe, '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing), '-c', 'copy', '-movflags', '+faststart', str(final)], capture_output=True, creationflags=0x08000000 if os.name == 'nt' else 0)
        if result.returncode:
            (checkpoint / 'merge-error.log').write_bytes(result.stderr)
            raise ValueError('视频封装失败，已录制片段已保留，重试会直接封装')
        try:
            os.replace(final, output)
            (self.directory / (doc.get('export_id', doc['id']) + '-latest.json')).unlink(missing_ok=True)
        except PermissionError:
            # A video player can hold the previous MP4 open on Windows.
            alternate = output.with_name(output.stem + '-' + uuid.uuid4().hex[:10] + '.mp4')
            os.replace(final, alternate)
            (self.directory / (doc.get('export_id', doc['id']) + '-latest.json')).write_text(json.dumps({'file': alternate.name}), encoding='utf-8')

    def record_part(self, doc, entries, exe, output, job):
        import json
        import re
        cancelled = self.cancel_events.get(doc.get('export_id', doc['id']), threading.Event())
        node = shutil.which('node')
        if not node:
            raise ValueError('视频导出需要 Node.js，请先安装后重试')
        flags = 0x08000000 if os.name == 'nt' else 0
        cast = (doc.get('outline') or {}).get('cast', [])
        items, sounds = [], []
        active_scene = None
        available_outfits = self.available_outfits(doc)
        for scene, line, audio_files in entries:
            new_scene = scene is not active_scene
            if new_scene:
                active_scene = scene
                shots = scene_shots(scene, cast, doc['id'])
            frame = shots[line['id']]
            outfits, background = frame['outfits'], frame['background']
            decoded = []
            voice_durations = {}
            for actor, path in audio_files:
                result = subprocess.run([exe, '-v', 'error', '-i', str(path), '-f', 's16le', '-ac', '2', '-ar', '24000', 'pipe:1'], capture_output=True, creationflags=flags)
                if result.returncode:
                    raise ValueError('有语音文件无法读取，请重新准备这句配音')
                data = array('h'); data.frombytes(result.stdout); decoded.append(data)
                if data:
                    voice_durations[actor] = len(data) / 48000
            length = max((len(a) for a in decoded), default=0)
            if len(decoded) == 1:
                mixed = decoded[0]
            else:
                mixed = array('h', [0]) * length
                for data in decoded:
                    for i, value in enumerate(data):
                        mixed[i] = max(-32768, min(32767, mixed[i] + value // len(decoded)))
            sounds.append(mixed)
            thought = frame['thought']
            duration = length / 48000 + .45 if length else (min(20, max(5, len(line.get('zh', '')) * .15)) if thought else min(12, max(3, len(line.get('zh', '')) * .115)))
            shot = frame['cast']
            actors = []
            for cid in shot:
                character = self.adapter.characters.get(cid)
                oid = outfits.get(cid) or getattr(character, 'default_outfit', None)
                if (cid, oid) not in available_outfits:
                    actors.append(dict(id=cid, name=getattr(character, 'short_name', None) or getattr(character, 'name', cid), outfit=None, speaking=False, isSpeaker=cid in (line.get('speakers') or [line.get('speaker')]), mood='calm', bodyVariant=0, bodyMood=None))
                    continue
                visual = frame['poses'][cid]
                actors.append(dict(**visual, id=cid, outfit=oid, height=((self.adapter.outfit(cid, oid) or {}).get('bounds') or {}).get('height', 1000), speaking=cid in voice_durations and not thought, voiceDuration=voice_durations.get(cid, 0), isSpeaker=cid in (line.get('speakers') or [line.get('speaker')])))
            char = self.adapter.characters.get(line.get('speaker'))
            color = getattr(char, 'color', None) or '#324d74'
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', color): color = '#324d74'
            subtitle = line.get('zh', '')
            bg = self.adapter.backgrounds.image_path(background)
            items.append(dict(actors=actors, line={key:line[key] for key in ("speaker", "speakers", "emotion", "actor_states", "body_action", "last_fine_edit_token") if key in line}, segments=line.get("performance_segments") or [], zh=subtitle, pages=subtitle_pages(subtitle), transition=((scene.get('transition', 'cut') if new_scene and items else scene.get('line_transitions', {}).get(line['id'], 'cut')) if items else 'cut'), transition_length=(scene.get('transition_length', 'short') if new_scene else scene.get('line_transition_lengths', {}).get(line['id'], 'short')), color=color, background='/backgrounds/' + bg.name if bg else '', cue_duration=(length / 48000 if length else duration), duration=duration, variant=(int(line['id'].split('-')[-1]) // 2 if line['id'].split('-')[-1].isdigit() else 0)))
        root = Path(__file__).resolve().parents[1]
        folder = Path(doc['recording_directory'])
        folder.mkdir(parents=True, exist_ok=True)
        with nullcontext(str(folder)) as folder:
            temp = Path(folder)
            if not ((temp / 'record.webm').is_file() and (temp / 'offsets.json').is_file()):
                proc = subprocess.Popen([node, str(root / 'backend' / 'video_record.cjs'), str(root), str(self.adapter.asset_root.resolve()), str((self.adapter.backgrounds.directory / 'originals').resolve()), str(temp)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
                assert proc.stdin and proc.stdout and proc.stderr
                proc.stdin.write(json.dumps(items, ensure_ascii=False).encode('utf-8')); proc.stdin.close()
                def cancel_process():
                    while proc.poll() is None:
                        if cancelled.wait(.25):
                            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, creationflags=flags) if os.name == 'nt' else proc.terminate()
                            return
                threading.Thread(target=cancel_process, daemon=True).start()
                for raw in proc.stdout:
                    try:
                        count = json.loads(raw)['progress']
                        job.update(progress=int(count / len(items) * 85), message=f'正在录制第 {count} / {len(items)} 句')
                    except (ValueError, KeyError): pass
                errors = proc.stderr.read()
                if cancelled.is_set():
                    proc.wait()
                    raise ValueError('导出已停止，可以重新导出')
                if proc.wait():
                    (self.directory / 'video-error.log').write_bytes(errors[-8000:])
                    raise ValueError('立绘演出录制失败，请检查本地素材和浏览器')
            timing = json.loads((temp / 'offsets.json').read_text(encoding='utf-8'))
            wav = wave.open(str(temp / 'audio.wav'), 'wb')
            wav.setparams((2, 2, 24000, 0, 'NONE', 'not compressed'))
            written = 0
            for offset, sound in zip(timing['offsets'], sounds):
                target = round(offset * 24000)
                if target > written:
                    wav.writeframes(b'\0' * ((target - written) * 4)); written = target
                wav.writeframes(sound.tobytes()); written += len(sound) // 2
            total = round(timing['elapsed'] * 24000)
            if total > written: wav.writeframes(b'\0' * ((total - written) * 4))
            wav.close()
            probe = subprocess.run([exe, '-i', str(temp / 'record.webm')], capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=flags)
            match = re.search(r'Duration: (\d+):(\d+):([\d.]+)', probe.stderr)
            recorded = (int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])) if match else timing['elapsed']
            lead = calibration_lead(exe, temp / 'record.webm') if timing.get('calibration_marker') else max(0, timing.get('recording_start', recorded - timing['elapsed']))
            job['message'] = '正在生成视频文件'
            result = subprocess.run([exe, '-y', '-v', 'error', '-ss', str(lead), '-i', str(temp / 'record.webm'), '-i', str(temp / 'audio.wav'), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '17', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', '-shortest', str(temp / 'result.mp4')], capture_output=True, creationflags=flags)
            if result.returncode:
                (temp / 'ffmpeg-error.log').write_bytes(result.stderr)
                raise ValueError('视频合成失败，录制画面已保存，可重试；详情见 ffmpeg-error.log')
            os.replace(temp / 'result.mp4', output)

