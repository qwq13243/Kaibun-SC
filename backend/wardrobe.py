"""Independent, image-backed story wardrobe; assets are read from the local mirror."""

import asyncio
import hashlib
import json
import re
import shutil
from pathlib import Path

import httpx

from support.llm import merge_extra_body
from paths import data_root
from outfit_policy import StoryOutfits
from asset_index import OutfitIndex


class WardrobeError(ValueError):
    """Locally authored explanation safe to display in the story UI."""


OUTFIT_ID = re.compile(r"(?:stand|stand_costume)/\d{10}\Z")


class Wardrobe:
    def __init__(self, adapter, directory=None):
        self.adapter = adapter
        self.directory = Path(directory or data_root() / 'wardrobe').resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.entries = {}
        self.by_character = {cid: [] for cid in adapter.characters}
        self._bounds = {}
        self._clips = {}
        self._holds = {}
        self._pending = {}
        self._worker = None
        manifest = adapter.asset_root.parent / "spine_manifest.json"
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            entries = []
        idols = {c.idol_id: cid for cid, c in adapter.characters.items() if c.idol_id is not None}
        for entry in entries:
            cid = idols.get(entry.get("idolId"))
            oid = entry.get("path", "").removeprefix("spine/idols/").rstrip("/")
            if not cid or not OUTFIT_ID.fullmatch(oid):
                continue
            root = adapter.asset_root / "spine" / "idols" / oid
            if not all((root / name).is_file() for name in ("data.json", "data.atlas", "data.png")):
                continue
            self.entries[oid] = dict(entry)
            if oid not in self.by_character[cid]:
                self.by_character[cid].append(oid)
        # A configured default can be usable even when a mirror's manifest is older.
        for cid, character in adapter.characters.items():
            oid = character.default_outfit
            if oid and OUTFIT_ID.fullmatch(oid) and oid not in self.entries:
                root = adapter.asset_root / "spine" / "idols" / oid
                if all((root / name).is_file() for name in ("data.json", "data.atlas", "data.png")):
                    self.entries[oid] = {"dressName": "默认衣装", "idolId": character.idol_id}
                    self.by_character[cid].append(oid)

        self.index = OutfitIndex(self)

    def outfits(self, cid):
        return [self.outfit(cid, oid) for oid in self.by_character.get(cid, [])]

    def outfit(self, cid, oid):
        if not isinstance(oid, str) or oid not in self.by_character.get(cid, []):
            return None
        if oid not in self._bounds:
            try:
                source = json.loads((self.adapter.asset_root / "spine" / "idols" / oid / "data.json").read_text(encoding="utf-8"))
                if isinstance(animations := source.get('animations'), dict):
                    from visual_fields import is_hold_action
                    self._clips[oid] = list(animations)
                    self._holds[oid] = [name for name, clip in animations.items() if is_hold_action(name, clip)]
                data = source["skeleton"]
                width, height = float(data["width"]), float(data["height"])
                if not 0 < width < 100000 or not 0 < height < 100000:
                    raise ValueError()
                self._bounds[oid] = dict(width=width, height=height)
            except (OSError, ValueError, KeyError, TypeError):
                self._bounds[oid] = None
        name = self.entries[oid].get("dressName", oid)
        kind = "舞台衣装" if oid.startswith("stand_costume/") else "日常衣装"
        return dict(id=oid, url=f"/api/kaibun/stand/{oid}/",
                    animation_names=self._clips.get(oid),
                    hold_actions=self._holds.get(oid,[]),
                    thumbnail=f"/api/kaibun/wardrobe/{oid}.png", bounds=self._bounds[oid],
                    label=f"{name} · {kind}")

    def thumbnail_path(self, oid):
        if not isinstance(oid, str) or oid not in self.entries:
            return None
        path = self.directory / "thumbnails" / f"{oid}.png"
        return path if path.is_file() else None

    async def ensure_thumbnail(self, oid):
        if not isinstance(oid, str) or oid not in self.entries:
            raise WardrobeError("没有这套本地衣装")
        if path := self.thumbnail_path(oid):
            return path
        if oid not in self._pending:
            self._pending[oid] = asyncio.get_running_loop().create_future()
        future = self._pending[oid]
        if not self._worker or self._worker.done():
            self._worker = asyncio.create_task(self._render_loop())
        return await asyncio.shield(future)

    async def _render_loop(self):
        while self._pending:
            await asyncio.sleep(0.05)  # Coalesce the visible thumbnails into one local browser.
            batch = dict(self._pending)
            try:
                await self._render(list(batch))
                for oid, future in batch.items():
                    path = self.thumbnail_path(oid)
                    if path:
                        future.set_result(path)
                    else:
                        future.set_exception(WardrobeError(f"衣装缩略图未能渲染：{oid}，可以重试"))
            except asyncio.CancelledError:
                for future in self._pending.values():
                    future.cancel()
                self._pending.clear()
                raise
            except Exception as error:
                reason = error if isinstance(error, WardrobeError) else WardrobeError("本地衣装图片准备失败，可以重试")
                for future in batch.values():
                    future.set_exception(reason)
            finally:
                for oid in batch:
                    self._pending.pop(oid, None)

    async def _render(self, ids, high_resolution=False):
        node = shutil.which("node")
        if not node:
            raise WardrobeError("本地衣装预览需要 Node.js，当前未找到，安装后可重试")
        root = Path(__file__).resolve().parents[1]
        helper = self.directory / "render.cjs"
        helper.write_text(RENDERER, encoding="utf-8")
        process = await asyncio.create_subprocess_exec(
            node, str(helper), str(root), str(self.adapter.asset_root.resolve()),
            str(self.directory / "details" if high_resolution else self.directory), "768" if high_resolution else "256",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=0x08000000 if __import__("os").name == "nt" else 0)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(json.dumps(ids).encode()), timeout=max(90, len(ids) * 15))
        except (asyncio.CancelledError, TimeoutError):
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        if process.returncode:
            # Browser output is diagnostic data, never returned as instructions or provider errors.
            (self.directory / "render-error.txt").write_bytes(stdout[-6000:])
            raise WardrobeError("本地衣装预览渲染失败，请检查浏览器与素材后重试")

    def initial_images(self):
        selected = [c.default_outfit for c in self.adapter.characters.values()
                    if c.default_outfit in self.entries]
        for cid in ("amana", "hana", "tenka", "rinze", "kaho", "nichika"):
            selected.extend([oid for oid in self.by_character.get(cid, []) if oid.startswith("stand/")][:12])
        return list(dict.fromkeys(selected))

    async def prepare_images(self, ids=None):
        ids = self.initial_images() if ids is None else list(dict.fromkeys(ids))
        if any(oid not in self.entries for oid in ids):
            raise WardrobeError("图片准备包含未知衣装")
        # Batch requests share the existing single local renderer and disk cache.
        for start in range(0, len(ids), 12):
            await asyncio.gather(*(self.ensure_thumbnail(oid) for oid in ids[start:start + 12]))
        return ids

    async def prepare(self, doc):
        requested = doc.get('requested_outfits') or {}
        if not isinstance(requested, dict):
            raise WardrobeError('指定衣装需要按角色保存')
        for cid, oid in requested.items():
            if not self.outfit(cid, oid):
                raise WardrobeError(f'指定衣装不属于角色：{cid}')
        scenes = doc.get('scenes', [])
        cast = (doc.get('outline') or {}).get('cast', [])
        mode = doc.get('outfit_mode', 'fixed' if requested or any(s.get('outfits') for s in scenes) else 'story')
        if mode not in ('fixed', 'story'):
            raise WardrobeError('衣装模式需要为fixed或story')
        plans = [dict(s.get('outfits') or {}) for s in scenes]
        changes = [json.loads(json.dumps(s.get('outfit_changes') or {})) for s in scenes]
        for i, scene in enumerate(scenes):
            line_ids = {line.get('id') for line in scene.get('lines', [])}
            if any(not self.outfit(cid, oid) for cid, oid in plans[i].items()):
                raise WardrobeError('已选衣装失效，请重新选择')
            for lid, mapping in changes[i].items():
                if lid not in line_ids or not isinstance(mapping, dict) or any(not self.outfit(cid, oid) for cid, oid in mapping.items()):
                    raise WardrobeError('已保存的场内换装记录无效')
        policy = StoryOutfits(self.adapter.characters, self.outfit, self.outfits, self.adapter)
        fixed = {}
        if mode == 'fixed':
            fixed.update(requested)
            for plan in plans:
                for cid, oid in plan.items():
                    fixed.setdefault(cid, oid)
        previous = {}
        for index, scene in enumerate(scenes):
            plans[index] = await policy.scene_choices(doc, scene, cast, previous, fixed)
            previous.update(plans[index])
            for line in scene.get('lines', []):
                previous.update(changes[index].get(line['id'], {}))
        for scene, plan in zip(scenes, plans):
            scene['outfits'] = plan
        return plans


    def _contact_sheets(self, cid, options, images):
        from PIL import Image, ImageDraw, ImageFont

        result = []
        for offset in range(0, len(images), 12):
            group = images[offset:offset + 12]
            digest = hashlib.sha256((cid + "|" + "|".join(
                f"{p}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in group)).encode()).hexdigest()[:24]
            target = self.directory / "sheets" / f"{digest}.jpg"
            if not target.is_file():
                canvas = Image.new("RGB", (1024, ((len(group) + 3) // 4) * 410), "white")
                draw = ImageDraw.Draw(canvas)
                font = ImageFont.load_default(size=16)
                for j, path in enumerate(group):
                    with Image.open(path) as original:
                        image = original.convert("RGBA")
                        image.thumbnail((248, 378))
                        x, y = (j % 4) * 256, (j // 4) * 410
                        canvas.paste(image, (x + (256 - image.width) // 2, y), image)
                    draw.text((x + 5, y + 382), f"{offset + j + 1:03d} {options[offset + j]['id']}", fill="#111111", font=font)
                target.parent.mkdir(exist_ok=True)
                canvas.save(target, "JPEG", quality=88)
            result.append(target)
        return result

    async def close(self):
        if self._worker and not self._worker.done():
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)


async def complete_with_images(adapter, system, user, images):
    """The configured dialogue model, with image content through its own protocol."""
    if not images or any(not isinstance(image, str) or not re.fullmatch(r"data:image/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=]+", image) for image in images):
        raise WardrobeError("衣装看图请求缺少有效图片")
    settings = adapter.settings
    from urllib.parse import urlsplit
    headers = {"x-goog-api-key": settings.api_key} if urlsplit(settings.base_url).hostname == "aiplatform.googleapis.com" and not settings.api_key.startswith("ya29.") else {"Authorization": f"Bearer {settings.api_key}"}
    body = {"model": settings.model, "stream": False}
    if settings.protocol == "responses":
        endpoint = "/responses"
        body.update(store=False, instructions=system, input=[dict(role="user", content=[dict(type="input_text", text=user)] +
                    [dict(type="input_image", image_url=image) for image in images])])
        if settings.max_tokens is not None:
            body["max_output_tokens"] = settings.max_tokens
        if settings.reasoning_effort is not None:
            body["reasoning"] = {"effort": settings.reasoning_effort}
    elif settings.protocol == "anthropic":
        endpoint = "/messages"
        headers = {"x-api-key": settings.api_key, "anthropic-version": "2023-06-01"}
        content = [dict(type="text", text=user)]
        for image in images:
            mime, data = image[5:].split(";base64,", 1)
            content.append(dict(type="image", source=dict(type="base64", media_type=mime, data=data)))
        body.update(system=system, max_tokens=settings.max_tokens or 16384, messages=[dict(role="user", content=content)])
        if settings.reasoning_effort == "none":
            body["thinking"] = {"type": "disabled"}
        elif settings.reasoning_effort is not None:
            body.update(thinking={"type": "adaptive", "display": "omitted"}, output_config={"effort": settings.reasoning_effort})
    elif settings.protocol == "chat-completions":
        endpoint = "/chat/completions"
        body["messages"] = [dict(role="system", content=system), dict(role="user", content=[dict(type="text", text=user)] +
                            [dict(type="image_url", image_url={"url": image}) for image in images])]
        if settings.max_tokens is not None:
            body["max_tokens"] = settings.max_tokens
        if settings.reasoning_effort is not None:
            body["reasoning_effort"] = settings.reasoning_effort
    else:
        raise WardrobeError("当前对话接口没有可用的看图协议")
    # Optional provider settings may not replace the actual image-bearing request.
    content_fields = {key: body[key] for key in ("input", "messages", "instructions", "system", "store") if key in body}
    body = merge_extra_body(body, settings.extra_body)
    body.update(content_fields)
    body["model"], body["stream"] = settings.model, False
    try:
        response = await adapter.client.post(settings.base_url.rstrip("/") + endpoint, headers=headers, json=body)
        if not response.is_success:
            raise WardrobeError(f"当前对话模型的衣装看图请求未完成（HTTP {response.status_code}），请确认模型支持图片后重试")
        data = response.json()
        from support.billing import bill
        usage=data.get('usage') or {}
        if isinstance(usage,dict):
            if settings.protocol=='chat-completions':
                detail=usage.get('prompt_tokens_details') or {}
                bill('tokens',usage.get('prompt_tokens'),usage.get('completion_tokens'),detail.get('cached_tokens') if isinstance(detail,dict) else None)
            elif settings.protocol=='responses':
                detail=usage.get('input_tokens_details') or {}
                bill('tokens',usage.get('input_tokens'),usage.get('output_tokens'),detail.get('cached_tokens') if isinstance(detail,dict) else None)
            elif settings.protocol=='anthropic':
                total=sum(usage.get(key) or 0 for key in ('input_tokens','cache_read_input_tokens','cache_creation_input_tokens'))
                bill('tokens',total,usage.get('output_tokens'),usage.get('cache_read_input_tokens'))
        if data.get("error"):
            raise WardrobeError("当前对话模型未接受衣装图片，请确认视觉支持后重试")
        if settings.protocol == "responses":
            if data.get("status") in ("failed", "incomplete"):
                raise WardrobeError("衣装看图结果尚未完整生成，可以重试")
            text = "".join(part.get("text", "") for item in data.get("output", []) for part in item.get("content", []) if part.get("type") == "output_text")
        elif settings.protocol == "anthropic":
            if data.get("stop_reason") in ("max_tokens", "refusal"):
                raise WardrobeError("衣装看图结果未完整生成，可以重试")
            text = "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")
        else:
            choice = data["choices"][0]
            if choice.get("finish_reason") in ("length", "content_filter"):
                raise WardrobeError("衣装看图结果未完整生成，可以重试")
            content = choice["message"].get("content", "")
            text = content if isinstance(content, str) else "".join(p.get("text", "") for p in content if p.get("type") == "text")
        if not text.strip():
            raise WardrobeError("衣装看图接口没有返回文字结果，请确认模型支持图片后重试")
        return text
    except WardrobeError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        raise WardrobeError("当前对话模型的衣装看图接口返回异常，可以重试") from None


RENDERER = r'''
const fs=require('node:fs/promises'),path=require('node:path'),http=require('node:http');
const [root,assets,dest,resolution]=process.argv.slice(2);
const width=Number(resolution)||256,height=width*1.5;
const {chromium}=require(path.join(root,'frontend/node_modules/playwright'));
const ts=require(path.join(root,'frontend/node_modules/typescript'));
const roots={'/src/':path.join(root,'frontend/src'),'/vendor/':path.join(root,'frontend/public/vendor'),'/assets/':assets};
const mime={'.js':'text/javascript','.json':'application/json','.atlas':'text/plain','.png':'image/png'};
const html=`<!doctype html><meta charset="utf-8"><script type="importmap">{"imports":{"@/":"/src/"}}</script>
<script src="/vendor/spine-3.6/spine-webgl.js"></script><style>html,body{margin:0;background:transparent}canvas{display:block;width:${width}px;height:${height}px}</style><canvas></canvas>
<script type="module">import {StandEngine} from '/src/components/conversations/stand/StandEngine';let engine;
window.render=async(id)=>{engine?.dispose();engine=new StandEngine(document.querySelector('canvas'),window.spine,'full');await engine.load({id,url:'/assets/spine/idols/'+id+'/'});await new Promise(r=>setTimeout(r,550));};window.ready=true;</script>`;
const server=http.createServer(async(req,res)=>{try{const url=decodeURIComponent(new URL(req.url,'http://localhost').pathname);if(url==='/'){res.setHeader('content-type','text/html');return res.end(html)}const pre=Object.keys(roots).find(p=>url.startsWith(p));if(!pre){res.writeHead(404);return res.end()}let file=path.resolve(roots[pre],url.slice(pre.length));if(!file.startsWith(roots[pre]+path.sep)){res.writeHead(404);return res.end()}if(pre==='/src/'){if(!file.endsWith('.ts'))file+='.ts';res.setHeader('content-type','text/javascript');return res.end(ts.transpileModule(await fs.readFile(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText)}const body=await fs.readFile(file);res.setHeader('content-type',mime[path.extname(file)]||'application/octet-stream');res.end(body)}catch{res.writeHead(404);res.end()}});
(async()=>{let input='';for await(const chunk of process.stdin)input+=chunk;const ids=JSON.parse(input);await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
try{let error;for(const channel of ['msedge',undefined])try{browser=await chromium.launch({...(channel?{channel}:{}),headless:true,args:['--enable-unsafe-swiftshader','--use-angle=swiftshader']});break}catch(e){error=e}if(!browser)throw error;
let next=0;await Promise.all(Array.from({length:Math.min(4,ids.length)},async()=>{const page=await browser.newPage({viewport:{width,height},deviceScaleFactor:1});page.setDefaultTimeout(30000);await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>window.ready);while(next<ids.length){const id=ids[next++];try{await page.evaluate(id=>window.render(id),id);const target=path.join(dest,'thumbnails',id+'.png');await fs.mkdir(path.dirname(target),{recursive:true});await page.locator('canvas').screenshot({path:target+'.part',type:'png',omitBackground:true});await fs.rename(target+'.part',target)}catch{console.log('failed '+id)}}await page.close()}))
}finally{await browser?.close();server.close()}})().catch(e=>{console.log(String(e?.name||'render failed'));server.close();process.exitCode=1});
'''
