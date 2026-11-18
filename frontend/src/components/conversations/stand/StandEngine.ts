import type { Outfit } from "@/lib/api";
import { CALM, pickAnimations, pickIdle, type Emotion, type StandDirection } from "@/lib/standAnimations";
import { FrameGate, MAX_PIXEL_RATIO, pixelRatio } from "@/lib/standFrames";
import { numberedLimbLayers, repairOutfitRelay, resolveNumberedLimbLayers } from "./standSlots";

import type {
  AnimationState,
  AssetManager,
  ClippingAttachment,
  SceneRenderer,
  Skeleton,
  SkeletonData,
  SlotData,
  Slot,
  SpineRuntime,
  TextureAtlas,
  TrackEntry,
} from "./spineRuntime";

/**
 * 取景（spec「渲染」）：手机「大立绘」贴着头顶、到膝；电脑「半身 ADV」贴着头顶、到腰；
 * 换衣装的大预览是全身。
 */
export type Framing = "knees" | "waist" | "full";

/** 取景的高度，占身体的比例（全身另算，见 `aim`）。 */
const FRAMING_HEIGHT: Record<Exclude<Framing, "full">, number> = { knees: 0.85, waist: 0.66 };
/** 取景的宽度不窄于身体的这个比例，免得竖屏上只剩一张脸。 */
const MIN_WIDTH = 0.7;
/** 头顶上方留的空（全身时四周留的空），占身体高度的比例。 */
const HEADROOM = 0.03;

/** 轨道照原作 ADV：0 身体、1 表情、2 点头、3 视线、5 口型（4 脸红暂不使用）。 */
const TRACK = { body: 0, face: 1, nod: 2, eye: 3, mouth: 5 } as const;
/** 同一条轨道上换动画时的混合时间（秒）。 */
const MIX_SECONDS = 0.3;
/** 一帧最多推进这么久（秒）：卡了一下或者刚恢复时，动作不会一下子跳过去。 */
const MAX_STEP_SECONDS = 0.1;

const UNPACK_PREMULTIPLY_ALPHA_WEBGL = 0x9241;

/** 这台设备上画不了立绘：浏览器不支持 WebGL。 */
export class StandUnavailable extends Error {}

/** 载入好的一套衣装：骨架、它的动画状态和这套衣装有哪些动画。 */
type Pose = {
  /** 这套衣装的素材目录，释放素材时按它找。 */
  url: string;
  skeleton: Skeleton;
  data: SkeletonData;
  state: AnimationState;
  /** 这套衣装的动画，名字 → 时长（秒）。 */
  animations: ReadonlyMap<string, number>;
  limbAlphas?: Map<Slot, number>;
  numberedLimbs: ReturnType<typeof numberedLimbLayers>;
  /** 初始姿势下整个人的范围，取景据此计算。 */
  bounds: { x: number; y: number; width: number; height: number };
  /** 见过的剪裁，按剪裁附件记下它原本的结束插槽（见 `keepClipsEnding`）。 */
  clips: Map<ClippingAttachment, Clip>;
  /** 各条轨道现在放的是什么，只在要放的变了时才重设。 */
  shown: Shown;
};

type Shown = {
  entrySeed?: string;
  /** 身体动作按哪种心情选的：心情变了才换身体动作。 */
  mood: Emotion | null;
  /** Requested final body animation; the current track may still be its relay. */
  body: string | null;
  relay: string | null;
  face: string | null;
  /** 口型的名字和开合，例如 `lip_smile open`。 */
  mouth: string | null;
  eye: string | null;
  /** 最近一次点头的编号。 */
  nod: number | null;
};

type Clip = { slot: Slot; end: SlotData; detached: boolean };

/**
 * 通话层上的立绘（spec「渲染」）：在一块画布上载入一套衣装，每帧推进动画并绘制。不依赖 React。
 *
 * 各条轨道按立绘指令放「选动画」（`pickAnimations`）给出的动画。runtime 的几个已知问题都在这里处理：
 * 剪裁不结束、预乘 alpha、上下文丢失后恢复、React 开发模式下同一块画布上重建引擎。像素比不超过 2、
 * 最多 60 帧，暂停时（通话层收起）不画。
 */
export class StandEngine {
  /** Optional common camera scale used by multi-character story shots. */
  fullFrameHeight?: number;
  /** Export ensemble camera: one native-unit scale and ground-relative crop for all actors. */
  groundFrame?: { height: number; bottom: number };
  private readonly gl: WebGLRenderingContext;
  private readonly renderer: SceneRenderer;
  private readonly assets: AssetManager;
  private readonly listeners = new AbortController();
  private readonly gate = new FrameGate();
  private pose: Pose | null = null;
  private raf = 0;
  private last = 0;
  private paused = false;
  /** 静帧：画出一帧就停（落地页的「减少动态效果」档）。 */
  private still = false;
  private lost = false;
  private disposed = false;
  private direction: StandDirection = CALM;
  private reducedMotion = false;
  /** 第几次要求载入衣装：只有最后一次要的那套会换上。 */
  private loads = 0;
  /** 最后一次要的那套衣装的素材目录。 */
  private wanted: string | null = null;
  /** 正在下载的衣装，按素材目录。 */
  private readonly fetching = new Map<string, Promise<void>>();

  /**
   * 在 `canvas` 上准备好绘制；浏览器不支持 WebGL 时抛出 `StandUnavailable`。
   * `framing` 可以随时改，下一帧起按新的取景画。
   */
  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly spine: SpineRuntime,
    public framing: Framing,
    /** 画布的像素比上限：落地页要的比通话层低。 */
    private readonly maxPixelRatio: number = MAX_PIXEL_RATIO,
  ) {
    const signal = this.listeners.signal;
    // 要注册在创建 ManagedWebGLRenderingContext 之前：恢复时浏览器按注册顺序调用监听，
    // 先重新开启「上传时预乘 alpha」，runtime 自己的监听再重新上传贴图，否则恢复后会出白边。
    // 恢复不了时画布一直是空的，通话层上就只剩背景图和纱，这次通话不再重试。
    // runtime 自己的监听销毁时摘不掉，但它管的贴图和着色器在销毁时都已移出恢复清单，留着也不会再上传什么。
    canvas.addEventListener(
      "webglcontextlost",
      () => {
        this.lost = true;
        this.stop();
      },
      { signal },
    );
    canvas.addEventListener(
      "webglcontextrestored",
      () => {
        this.gl.pixelStorei(UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
        this.lost = false;
        this.start();
      },
      { signal },
    );
    const context = new spine.webgl.ManagedWebGLRenderingContext(canvas, { alpha: true, premultipliedAlpha: true });
    if (!context.gl) {
      this.listeners.abort();
      throw new StandUnavailable("浏览器不支持 WebGL");
    }
    this.gl = context.gl;
    this.lost = this.gl.isContextLost();
    // 贴图上传时预乘 alpha，绘制时也按预乘处理，否则部分衣装脸上有白边、关节处有细线。
    this.gl.pixelStorei(UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    this.renderer = new spine.webgl.SceneRenderer(canvas, context, true);
    this.assets = new spine.webgl.AssetManager(context);
  }

  /**
   * 按立绘指令摆姿势（spec「选动画」）。立绘还没到时先记下，到了直接按最新的指令开始画。
   * 减少动态效果从下一个身体动作（心情变了时）开始生效，不打断正在放的。
   */
  direct(direction: StandDirection, reducedMotion: boolean): void {
    this.direction = direction;
    this.reducedMotion = reducedMotion;
    if (this.pose) this.follow(this.pose);
  }

  /**
   * 载入一套衣装，按当前的立绘指令摆好姿势后开始绘制；已经穿着另一套时，换上这一套并释放上一套的素材。
   * 素材加载不到、数据读不了时 reject，身上那套照旧。接连换了几套时只换上最后要的那一套，
   * 前面几套到了也不换，素材直接释放。
   */
  async load(outfit: Outfit): Promise<void> {
    const { url } = outfit;
    const ticket = ++this.loads;
    this.wanted = url;
    if (this.pose?.url === url) return;
    try {
      await this.fetchOutfit(url);
    } catch (error) {
      // 同一套的每次载入共用一次下载，一起失败，素材释放一次就够。销毁之后才失败的，把陆续到的也释放掉。
      if (this.disposed) this.assets.dispose();
      else this.release(url);
      throw error;
    }
    if (this.disposed) {
      // 销毁之后才到的素材也要释放（再调一次 dispose 只会处理这些新到的）。
      this.assets.dispose();
      return;
    }
    if (ticket !== this.loads) {
      // 又要了别的衣装。要的还是这一套时，后来的那次载入会用上这些素材。
      if (this.wanted !== url) this.release(url);
      return;
    }
    let pose: Pose;
    try {
      pose = this.read(outfit);
    } catch (error) {
      this.release(url);
      throw error;
    }
    const previous = this.pose;
    this.pose = pose;
    if (previous) this.release(previous.url);
    this.follow(this.pose);
    this.start();
  }

  /** 读出已经下载好的一套衣装：骨架摆成初始姿势，量出整个人的范围。数据读不了时抛出。 */
  private read({ id, url }: Outfit): Pose {
    const json = `${url}data.json`;
    const atlas = `${url}data.atlas`;
    const spine = this.spine;
    const loader = new spine.AtlasAttachmentLoader(this.assets.get(atlas) as TextureAtlas);
    const data = new spine.SkeletonJson(loader).readSkeletonData(this.assets.get(json) as string);
    const skeleton = new spine.Skeleton(data);
    repairOutfitRelay(id, data, skeleton);
    if (data.findSkin("normal")) skeleton.setSkinByName("normal");
    skeleton.setToSetupPose();
    skeleton.updateWorldTransform();
    const offset = new spine.Vector2();
    const size = new spine.Vector2();
    skeleton.getBounds(offset, size, []);
    const stateData = new spine.AnimationStateData(data);
    stateData.defaultMix = MIX_SECONDS;
    return {
      url,
      skeleton,
      data,
      state: new spine.AnimationState(stateData),
      animations: new Map(data.animations.map((a) => [a.name, a.duration])),
      numberedLimbs: numberedLimbLayers(skeleton),
      bounds: { x: offset.x, y: offset.y, width: size.x, height: size.y },
      clips: new Map(),
      // 立绘到之前的点头已经过去了，不补。
      shown: { mood: null, body: null, relay: null, face: null, mouth: null, eye: null, nod: this.direction.nod?.id ?? null },
    };
  }

  /**
   * 下载一套衣装的 `data.json` 和 `data.atlas`（连同图集里的贴图）。同一套正在下载时共用这一次。
   * 等两份都有了结果再往下走：先失败的那份不能让另一份在引擎销毁之后才把贴图传上去。
   */
  private fetchOutfit(url: string): Promise<void> {
    let fetching = this.fetching.get(url);
    if (!fetching) {
      fetching = Promise.allSettled([
        new Promise<void>((resolve, reject) =>
          this.assets.loadText(`${url}data.json`, () => resolve(), (_, message) => reject(new Error(message))),
        ),
        new Promise<void>((resolve, reject) =>
          this.assets.loadTextureAtlas(`${url}data.atlas`, () => resolve(), (_, message) => reject(new Error(message))),
        ),
      ])
        .then((results) => {
          for (const result of results) if (result.status === "rejected") throw result.reason;
        })
        .finally(() => this.fetching.delete(url));
      this.fetching.set(url, fetching);
    }
    return fetching;
  }

  /**
   * 释放一套衣装的素材（按素材目录找到的文字、图集和贴图）。3.6 的 `AssetManager.remove` 只把条目设成 null，
   * 之后销毁时 `removeAll` 会对 null 调 dispose 报错，所以这里自己 dispose，再连条目一起删掉。
   * 贴图 dispose 时会把自己移出 `ManagedWebGLRenderingContext.restorables`，上下文恢复时不会把旧衣装再传一遍；
   * 图集 dispose 时已经释放过它的贴图，贴图再 dispose 一次只是重复删除，WebGL 会忽略。
   */
  private release(url: string): void {
    const assets = this.assets.assets;
    for (const path of Object.keys(assets)) {
      if (!path.startsWith(url)) continue;
      const asset = assets[path];
      if (typeof asset === "object" && asset) asset.dispose?.();
      delete assets[path];
    }
  }

  /** 静帧：只画一帧初始姿势就停下，之后换衣装也只再画一帧。在载入衣装之前定下。 */
  setStill(still: boolean): void {
    this.still = still;
  }

  /** 暂停（通话层收起时）不画，也不推进动画；恢复后从当前姿势接着画。 */
  setPaused(paused: boolean): void {
    this.paused = paused;
    if (paused) this.stop();
    else this.start();
  }

  /**
   * 停止绘制并释放素材。不调用 `loseContext()`：React 开发模式下 effect 会执行两次，
   * 在同一块画布上重建引擎时拿到的还是同一个上下文，丢掉它新引擎就画不出来了。
   */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.stop();
    this.listeners.abort();
    this.pose = null;
    this.assets.dispose();
    this.renderer.dispose();
  }

  /**
   * 让各条轨道放选出来的动画，只动要放的变了的轨道。衣装缺了选出来的动画（例如 `wait`、`blank`）时，
   * 这条轨道保持原样。
   */
  private follow(pose: Pose): void {
    const { state, shown } = pose;
    const want = pickAnimations(this.direction, pose.animations, this.reducedMotion);
    if(this.direction.idleOnly&&!this.reducedMotion){
      const valid=new Map([...pose.animations].filter(([n])=>n==='wait'||(/^wait\d+$/.test(n)&&!!this.event(pose.data,n,'loop_start'))));
      want.body=pickIdle(valid,this.direction.idleSeed);
    }
    const entering=!!this.direction.entrySeed && shown.entrySeed!==this.direction.entrySeed;
    shown.entrySeed=this.direction.entrySeed;
    const idleEntrance=!!(this.direction.idleOnly||this.direction.idleEntrance);
    if (shown.body !== want.body || (entering && idleEntrance)) {
      shown.mood = this.direction.mood;
      this.playBody(pose, want.body, entering && idleEntrance);
    }
    if (shown.face !== want.face) {
      shown.face = want.face;
      // 衣装连 `blank` 都没有时清空这条轨道，同样把脸交还给身体动作。
      if (pose.animations.has(want.face)) state.setAnimation(TRACK.face, want.face, true).mixDuration = 0;
      else state.setEmptyAnimation(TRACK.face, MIX_SECONDS);
    }
    const mouth = want.mouth && `${want.mouth.name} ${want.mouth.open ? "open" : "shut"}`;
    if (want.mouth && shown.mouth !== mouth) {
      shown.mouth = mouth;
      // 闭嘴照原作：把同一个说话口型冻结在第 0 帧。不用 `lip_*_s`：它不管嘴部网格的变形，
      // 身体动作里的嘴部变形会透出来，停了嘴还在微微动。冻结用 `animationEnd = 0` 而不是
      // `timeScale = 0`：3.6 的混合进度按 timeScale 走，为 0 时混合永远走不完，上一段说话口型会一直在底下播。
      const entry = state.setAnimation(TRACK.mouth, want.mouth.name, want.mouth.open);
      entry.mixDuration = 0;
      if (!want.mouth.open) entry.animationEnd = 0;
    }
    if (shown.eye !== want.eye) {
      shown.eye = want.eye;
      if (want.eye) state.setAnimation(TRACK.eye, want.eye, true);
      else state.setEmptyAnimation(TRACK.eye, MIX_SECONDS);
    }
    if (want.nod && shown.nod !== want.nod.id) {
      shown.nod = want.nod.id;
      // 播一次就清空这条轨道：`yes` 播完时不在中立姿势。播完才开始清空：3.6 的 addEmptyAnimation
      // 在 delay 为 0 时会把混合时间扣两次，点头的最后 0.6 秒就没了。
      const nod = state.setAnimation(TRACK.nod, want.nod.name, false);
      state.addEmptyAnimation(TRACK.nod, MIX_SECONDS, nod.animation.duration);
    }
  }

  /**
   * 换身体动作。带 `loop_start` 事件的，开头播一次，之后从 `loop_start` 起循环尾段；没有的整段循环。
   * 按素材的 relay 收势后接目标动作的原生进入段，不插入额外 wait。
   * 不能跳到 loop_start 混合姿态：手臂的位移、显隐和形变必须随进入段同步推进。
   */
  private playBody({ state, data, animations, shown }: Pose, name: string, idleEntrance=false): void {
    if (!animations.has(name)) {
      if (state.getCurrent(TRACK.body)) state.setEmptyAnimation(TRACK.body, MIX_SECONDS);
      shown.body = name;
      shown.relay = null;
      return;
    }
    const current = state.getCurrent(TRACK.body);
    if ((!current || idleEntrance) && /^wait\d*$/.test(name)) {
      const marker=this.event(data,name,"loop_start");
      const entry=state.setAnimation(TRACK.body,name,true);
      entry.mixDuration=0;
      if(marker) {entry.animationStart=marker.time;entry.trackTime=0;}
      shown.body=name;shown.relay=null;
      return;
    }
    if (current?.animation.name === name && !current.next) {
      shown.body = name;
      shown.relay = null;
      return;
    }
    const relay = current && this.event(data, current.animation.name, "relay");
    const loopStart = this.event(data, name, "loop_start");
    let entry: TrackEntry;
    if (current && shown.relay === current.animation.name) {
      // Preserve the active lowering animation; only replace its queued destination.
      state.disposeNext(current);
      entry = state.addAnimation(TRACK.body, name, false, Math.max(current.animation.duration, 0.000001));
    } else if (relay && animations.has(relay.value)) {
      const lowering = state.setAnimation(TRACK.body, relay.value, false);
      shown.relay = relay.value;
      // Delay 0 starts defaultMix seconds early in Spine 3.6, truncating the authored motion.
      entry = state.addAnimation(TRACK.body, name, false, Math.max(lowering.animation.duration, 0.000001));
    } else {
      entry = state.setAnimation(TRACK.body, name, false);
      shown.relay = null;
    }
    shown.body = name;
    if (!loopStart) {
      entry.loop = true;
      return;
    }
    entry.listener = {
      complete: (played) => {
        if (played.loop) return;
        played.loop = true;
        played.animationStart = loopStart.time;
        played.trackTime = 0;
      },
    };
  }

  /** 动画 `name` 里第一个叫 `eventName` 的事件：它的时间和字符串参数。 */
  private event(data: SkeletonData, name: string, eventName: string): { time: number; value: string } | null {
    for (const timeline of data.findAnimation(name)?.timelines ?? []) {
      if (!(timeline instanceof this.spine.EventTimeline)) continue;
      for (const event of timeline.events)
        if (event.data.name === eventName)
          return { time: event.time, value: event.stringValue || event.data.stringValue || "" };
    }
    return null;
  }

  private start(): void {
    if (this.raf || !this.pose || this.paused || this.lost || this.disposed) return;
    this.last = 0;
    this.raf = requestAnimationFrame(this.frame);
  }

  private stop(): void {
    cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  private readonly frame = (now: number): void => {
    this.raf = requestAnimationFrame(this.frame);
    if (!this.gate.shouldDraw(now)) return;
    const step = this.last ? Math.min((now - this.last) / 1000, MAX_STEP_SECONDS) : 0;
    this.last = now;
    if (this.pose) this.draw(this.pose, step);
    if (this.still) this.stop();
  };

  private draw(pose: Pose, step: number): void {
    const { canvas, gl, renderer } = this;
    const ratio = pixelRatio(window.devicePixelRatio, this.maxPixelRatio);
    const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
    const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    gl.viewport(0, 0, width, height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);

    pose.state.update(step);
    this.applyPose(pose);
    pose.skeleton.updateWorldTransform();
    this.keepClipsEnding(pose);
    this.aim(pose, width / height);
    renderer.begin();
    renderer.drawSkeleton(pose.skeleton, true);
    renderer.end();
  }

  private applyPose(pose: Pose): void {
    // Display-only handoff decisions must not become the next animation's
    // authored state: many clips never key the hand slot's alpha at all.
    for(const [slot,alpha] of pose.limbAlphas||[])slot.color.a=alpha;
    pose.state.apply(pose.skeleton);
    pose.limbAlphas=this.resolveLimbLayers(pose.skeleton, pose.numberedLimbs);
    const mouth=pose.state.getCurrent(TRACK.mouth);
    if(mouth){
      const deformed=new Set((mouth.animation.timelines||[]).flatMap(t=>'frameVertices' in t&&'slotIndex' in t&&typeof t.slotIndex==='number'?[t.slotIndex]:[]));
      for(const slot of pose.skeleton.slots){
        if(/mouth|mouse/i.test(slot.data.name||'')&&!deformed.has(slot.data.index)&&slot.attachmentVertices)slot.attachmentVertices.length=0;
      }
    }
  }

  private resolveLimbLayers(skeleton: Skeleton, numbered: Pose["numberedLimbs"]): Map<Slot,number> {
    const authored=new Map<Slot,number>();
    const setAlpha=(slot:Slot,alpha:number)=>{
      if(slot.color.a===alpha)return;
      if(!authored.has(slot))authored.set(slot,slot.color.a);
      slot.color.a=alpha;
    };
    resolveNumberedLimbLayers(numbered.paired, setAlpha);
    const groups=new Map<string,{slot:Slot;variant:string}[]>();
    for(const slot of skeleton.slots){
      if(numbered.slots.has(slot))continue;
      const name=slot.data.name||'',tokens=name.toLowerCase().split('_');
      const side=/(?:^|_)([LR])\d*(?:_|$)/i.exec(name)?.[1].toUpperCase();
      const part=tokens.includes('hand')?'hand':tokens.includes('arm')?['top','bottom','wrist'].find(p=>tokens.includes(p)):undefined;
      if(name.startsWith('_')||!side||!part||!slot.getAttachment()||slot.color.a<=0.00001)continue;
      let variant=/(?:^|_)(pocket|up|down|fold)\d*(?:_|$)/i.exec(name)?.[1].toLowerCase();
      // A front/back/fist hand can share its forearm's branch even when its
      // sprite name does not say up/down. Follow the actual rig hierarchy.
      for(let bone=slot.bone;!variant&&bone;bone=bone.parent||undefined){
        variant=/(?:^|_)(pocket|up|down|fold)\d*(?:_|$)/i.exec(bone.data.name)?.[1].toLowerCase();
      }
      const key=`${part==='hand'?'hand':'arm_'+part}:${side}`;
      const rows=groups.get(key)||[];rows.push({slot,variant:variant||''});groups.set(key,rows);
    }
    for(const [key,rows] of groups){
      if(!key.startsWith('arm_')||rows.length<2||!rows.some(r=>r.slot.color.a<0.99999))continue;
      const winner=rows.reduce((best,row)=>row.slot.color.a>=best.slot.color.a?row:best);
      for(const row of rows)setAlpha(row.slot,row===winner?1:0);
      if(key.startsWith('arm_bottom:')){
        const hands=groups.get('hand:'+key.split(':')[1])||[];
        const matching=hands.filter(hand=>hand.variant===winner.variant);
        for(const hand of hands){
          // A fully opaque hand is sometimes intentionally reused by an up
          // pose despite its down-named slot (for example Rinze's sad1).
          if(hand.variant!==winner.variant&&(matching.length||hand.slot.color.a<0.99999))setAlpha(hand.slot,0);
        }
        if(matching.length===1)setAlpha(matching[0].slot,1);
        else if(matching.length>1&&matching.some(hand=>hand.slot.color.a<0.99999)){
          const chosen=matching.reduce((best,hand)=>hand.slot.color.a>=best.slot.color.a?hand:best);
          for(const hand of matching)setAlpha(hand.slot,hand===chosen?1:0);
        }
      }
    }
    // Hand gesture sprites may cross-fade while the forearm keeps the same
    // branch. Resolve that handoff independently of an arm-layer handoff.
    for(const [key,rows] of groups){
      if(!key.startsWith('hand:'))continue;
      const visible=rows.filter(row=>row.slot.color.a>0.00001);
      if(visible.length<2||!visible.some(row=>row.slot.color.a<0.99999))continue;
      const winner=visible.reduce((best,row)=>row.slot.color.a>=best.slot.color.a?row:best);
      for(const row of visible)setAlpha(row.slot,row===winner?1:0);
    }
    return authored;
  }

  /** 按取景对准镜头：贴着头顶，高度是身体的一段，宽度不窄于身体的约 70%，左右居中；全身时整个人居中。 */
  private aim({ bounds }: Pose, aspect: number): void {
    const camera = this.renderer.camera;
    if (this.groundFrame) {
      const {height, bottom} = this.groundFrame;
      camera.position.x = bounds.x + bounds.width / 2;
      camera.position.y = bounds.y + bottom + height / 2;
      camera.setViewport(height * aspect, height);
      return;
    }
    if (this.framing === "full") {
      // 全身：整个人放进画面，四周留一点空，上下左右居中。
      const viewHeight = Math.max(this.fullFrameHeight ?? 0, Math.max(bounds.height, bounds.width / aspect) * (1 + 2 * HEADROOM));
      camera.position.x = bounds.x + bounds.width / 2;
      camera.position.y = this.fullFrameHeight ? bounds.y + viewHeight / 2 : bounds.y + bounds.height / 2;
      camera.setViewport(viewHeight * aspect, viewHeight);
      return;
    }
    const viewHeight = this.fullFrameHeight ?? Math.max(bounds.height * FRAMING_HEIGHT[this.framing], (bounds.width * MIN_WIDTH) / aspect);
    const top = bounds.y + bounds.height * (1 + HEADROOM);
    camera.position.x = bounds.x + bounds.width / 2;
    camera.position.y = top - viewHeight / 2;
    camera.setViewport(viewHeight * aspect, viewHeight);
  }

  /**
   * 绕开 spine-ts 3.6 的剪裁 bug（不改 runtime）：渲染器遇到没有附件的插槽直接跳过，也就跳过了
   * 「剪裁到此结束」的判断，结束插槽当下没有附件时剪裁永远不结束，后面画的东西全被裁掉
   * （2215 套里有 304 套在初始姿势下就会触发，例如「花風Smiley」的眼睛、睫毛、眉毛）。
   * 每帧把结束插槽改成「剪裁开始之后、原结束插槽为止，最后一个真的会画出来的插槽」；
   * 范围里这一帧什么都不画时，这一帧摘掉剪裁附件（动画挂回去了就再摘），有东西画了再挂回去。
   */
  private keepClipsEnding({ skeleton, clips }: Pose): void {
    const { ClippingAttachment, RegionAttachment, MeshAttachment } = this.spine;
    for (const slot of skeleton.slots) {
      const attachment = slot.getAttachment();
      if (attachment instanceof ClippingAttachment && attachment.endSlot && !clips.has(attachment)) {
        clips.set(attachment, { slot, end: attachment.endSlot, detached: false });
      }
    }
    const order = skeleton.drawOrder;
    for (const [clip, c] of clips) {
      const current = c.slot.getAttachment();
      if (current !== clip && !(c.detached && current === null)) {
        c.detached = false; // 动画给这个插槽换上了别的附件，不再归这里管
        continue;
      }
      const from = order.indexOf(c.slot);
      const to = order.findIndex((slot) => slot.data === c.end);
      if (from < 0 || to < 0) continue;
      let last: SlotData | null = null;
      for (let i = to; i > from; i--) {
        const drawn = order[i].getAttachment();
        if (drawn instanceof RegionAttachment || drawn instanceof MeshAttachment) {
          last = order[i].data;
          break;
        }
      }
      if (last) {
        clip.endSlot = last;
        if (c.detached) c.slot.setAttachment(clip);
        c.detached = false;
      } else {
        // 已经摘过也要再摘：有的衣装（例如「ザ・冬優子イズム」）的动画每帧都把剪裁附件挂回去，
        // 眨眼时范围里什么都不画，挂回去的剪裁就会把后面的眼皮、眉毛、手臂都裁掉。
        c.slot.setAttachment(null);
        c.detached = true;
      }
    }
  }
}
