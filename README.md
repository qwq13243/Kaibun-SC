# Kaibun-SC · 怪文书

闪耀色彩同人剧本生成与播放工具，支持双语对白、角色配音、立绘演出及视频导出。

Kaibun 是从 [Eidolon-SC](https://github.com/Sallyn0225/eidolon-sc) 提取、修改的独立本地应用，以《偶像大师 闪耀色彩》的同人对白创作为主要使用场景。它使用你配置的语言模型构思大纲、逐幕写作，再将日语对白、中文字幕与可选的配音、人物演出组合起来播放。

**这是非官方粉丝项目，与 Bandai Namco Entertainment、《偶像大师》系列及其权利人无关联，也不代表其立场。代码开源不代表取得角色、剧情、立绘或声音的使用授权。**

需要 Agent 帮助部署时，让它先读本 README，再读 [部署指南](docs/DEPLOYMENT.md)。指南包含安装、模型配置、素材下载、检查步骤和常见故障处理。给 Agent 的请求示例：“请读取 README 和 docs/DEPLOYMENT.md，帮我在本机部署怪文书，先跑通文字，再配置配音和立绘。”

## 能做什么

- 输入故事想法、出场人物、补充要求和篇幅，生成分幕大纲与日中双语对白。
- 自动逐幕保存，支持暂停、重启后继续生成，以及在原作品后续写。
- 首页直接生成时，逐幕写作与配音、演出准备并行推进；暂停只停止写作，已写好内容继续配音和准备演出。继续后恢复写作，沿用进行中的配音和已完成素材。配音失败不打断后续写作，正文和制作都完成后才标记已完成。
- 正文每次请求最多连续写 5 幕，不足 5 幕则写完剩余部分；每次接写及暂停、断线、重启后的恢复都发送完整已写正文和当前幕草稿，不以摘要替代。完整台词收到后即保存，一幕完成即可并行配音；输出提前结束时从保存位置继续，不重写前文。完整上下文受所用模型容量限制，程序不会静默删减前文。
- 已识别的临时网络中断会有限次自动恢复。大纲或正文遇到模型服务 HTTP 429 时会等待后有限次重试；如果持续限流，可以稍后点击继续。正文恢复保留本批剩余幕的边界，读取完整正文和已保存草稿，不重新生成已完成幕。
- 剧本精调的正文生成使用相同的 5 幕批次。底部全篇调整读取完整原稿及当前修改预览，每批最多处理 5 幕；单句、单幕调整保留原有上下文范围和额外参考幕设置。预览不自动应用，也不提前重配音。
- 先审阅大纲，再编辑剧本；可以逐句修改、调整场景和台词顺序，或让模型生成修改预览，确认后再应用。保留最近 10 次模型修改的回退记录。
- 导入 TXT、Markdown、DOCX 和含文字的 PDF。推荐每行 `人物：台词`，用 `第1幕 地点` 分场；双语可写作 `人物：日文 || 中文`。单语内容需要先确认翻译，再准备配音。
- 播放、暂停、前后跳句，显示中文字幕和日语原文；支持多人同声、表情、站位和句内演出区间。
- 可选 Fish Audio 配音，按台词内容、读音、音色和模型缓存；可补齐缺失配音、重新生成当前句。
- 新故事和续写可在「服装安排」选择「随剧情换装」、默认或春夏秋冬。角色单独手选的衣装优先；续写只对新幕应用本次安排。网页保存的自定义衣装立即生效，未自定义的分类沿用内置设置；缺少对应素材时尝试可用的默认衣装。只有「随剧情换装」会请辅助模型按故事设定和大纲判断分类。
- 已生成作品的「场景服装」支持单人换装，或批量应用默认及四季衣装。「同时应用到后续幕」默认勾选，取消后只改当前幕。换装保留原演出和配音；确认当前衣装缺少动作时，播放和导出使用可用替代动作，在剧本中标红并可定位到对应台词。换回兼容衣装后恢复原动作。批量操作会覆盖范围内已有的个人衣装和幕内换装，缺少对应衣装素材的角色保持原样。
- 准备演出按幕合并：每个待处理场景发起一次辅助模型请求，联合选择背景和完整演出预设。预设选择只发送已保存的情绪强度、身体幅度、姿态及用途文字标注。模型根据原有台词和情绪选择面部、静止嘴型、说话口型及身体组合；低幅变化灵活采用，相似大姿态优先延续。旁观身体保持及手动设置优先。
- 已完成旧幕沿用原素材和动作；新幕或确实修改的场景才重新准备，每幕独立保存，失败继续复用完成部分。辅助模型输出上限留空时不发送限制参数；请求数和 token 配额通过单独队列控制，校内接口默认每分钟 10 次、300000 tokens，可用 `KAIBUN_BACKGROUND_REQUESTS_PER_MINUTE` 和 `KAIBUN_BACKGROUND_TOKENS_PER_MINUTE` 配置。队列预算不是输出上限。
- 将全篇或单幕导出为 MP4。视频按播放时间录制，长篇导出需要相应时间和本机资源。

发行版附带默认衣装的保持姿态文字说明，优先使用自己数据目录下的本地标注；不包含姿态截图或游戏素材。

没有立绘时显示姓名或你自行配置的头像；没有背景素材或索引时使用中性背景；没有配置语音密钥/角色音色时使用字幕播放。这些可选素材不会阻止故事完成。

不包含原项目的聊天、通话、账户、记忆、日程和社交功能；不需要安装或运行 Eidolon-SC。

## 运行环境

- Python 3.11 或以上，推荐由 [uv](https://docs.astral.sh/uv/getting-started/installation/) 管理。
- Node.js 22 或以上，以及 npm。
- 文本创作：一个支持 Chat Completions、Responses 或 Anthropic Messages 的模型服务。
- 配音可选：Fish Audio API 密钥，以及你有权使用的音色。
- 视频导出/衣装预览可选：Microsoft Edge，或 Playwright Chromium；导出依赖 FFmpeg，Python 依赖已包含 `imageio-ffmpeg`，也可用 `KAIBUN_FFMPEG` 指定本机程序。

语言模型、配音和看图标注可能产生第三方服务费用。故事长度是创作目标，实际时长取决于台词和播放节奏。

## 快速开始

1. 下载本仓库到任意目录，或执行 `git clone https://github.com/qwq13243/Kaibun-SC.git` 后进入 `Kaibun-SC` 目录。
2. 将根目录 `.env.example` 复制为 `.env`，填写 `CHAT_LLM_BASE_URL`、`CHAT_LLM_API_KEY`、`CHAT_LLM_MODEL`。按服务选择 `CHAT_LLM_PROTOCOL`；接口地址一般包含服务要求的 `/v1` 前缀。
3. Windows 在项目根目录执行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\start.ps1
   ```

4. 打开 <http://localhost:3010>。

首次启动会安装依赖并构建前端。更新前端源码后，用 `start.ps1 -Rebuild` 重新构建。**保持这个终端打开，按 Ctrl+C 会关闭本次启动的前后端。**启动日志位于 `.runtime/backend.log` 和 `.runtime/frontend.log`，不会进入仓库。通用启动入口为 `python start.py`，重建用 `python start.py --rebuild`；Windows 包装脚本自动使用项目虚拟环境中的 Python。

也可以分两个终端手动运行（Windows、Linux、macOS）：

```sh
# 终端一：在项目根目录
cd backend
uv sync --locked
uv run python server.py
```

```sh
# 终端二：在项目根目录
cd frontend
npm ci
npm run build
npm run start
```

后端默认监听 `127.0.0.1:8011`，前端默认监听 `127.0.0.1:3010`。启动时在 `.runtime/connection.json` 写入本机前后端使用的随机访问令牌；浏览器通过前端访问，不需要把令牌贴进页面。不要公开这个文件。

部署后在另一个终端检查（从项目根目录执行）：

```sh
cd backend
uv run python scripts/doctor.py --running
```

该检查只验证本地配置和前后端连接，不调用付费模型。最终还需要在页面生成一个短篇，确认模型实际可用。

**当前版本面向本机单人使用，不提供远程登录或多人隔离。不要将端口转发、隧道或反向代理开放给其他人。**后端访问令牌用于本机前后端验证，并非完整的多人账户系统。


### 非发言角色的表情与动作

自动演出继续沿用写作模型原有的台词切分，不新增长发言内部的自动变化。已有手动句内微调保持原功能。

剧本写作和模型修改沿用原有七类 `emotion` 及台词切分，写作模型只负责原有剧本字段。细分演出由小模型结合正文与 `config/performance_presets.json` 中已保存的标注选择，每个发言情绪提供对应预设，特殊用途候选跨情绪提供。完整组合分别保存供播放、跳句及导出使用；模型省略后续变化时延续已有预设。手动微调可独立覆盖面部、身体及两种嘴型。其他衣装可读取预先准备的 `config/performance_outfit_compatibility.json` 复用同角色预设，个别缺项只影响对应候选；新增衣装尚无兼容表时按实际组件回退。已有完成场景沿用保存结果。设计背景见 [角色演出预设方案](docs/ENSEMBLE_PRESETS_PROPOSAL.md)。

同一情绪下的重复身体已合并，面部和口型差异保留为同身体变体；空出的主预设从文字标注明确适配的其他身体中补充，缺少独立替代时停用重复名额。相似身体附有 `pose_group`，小模型同时读取本人近期发言的实际选择，按请求、解释、回忆、告白等表达阶段安排变化，并检查机械往返；不设置固定轮换次数或用齐指标。

当前 29 套默认衣装的审查清单保留 550 个有保持循环或属于基础待机的姿态，另有 403 个独立表情条目；其他衣装仍按其实际素材支持。起手、收手、点头摇头、口型、视线及显隐控制分别记录。非说话角色入镜在实际可用的待机中稳定选择，直接显示保持段；轮到本人发言后按台词判断，说完延续最后姿态。平静发言可使用贴合语境的完整预设及特殊动作。相同身体继续保持循环，其他轨道可独立更新。当前分工及后续节省 token 的建议见 [演出流程整理](docs/POSE_REVIEW_PROPOSAL.md)。

微调中默认勾选“将非发言角色的表情与动作延续到后续台词”。同一场景内，角色持续在画面中且尚未发言时，保持自己的表情与动作；本人发言、离开画面、进入下一幕或被后续明确设置覆盖时结束。取消勾选只影响当前句。句内多个片段以最后的有效设置向后延续，表情和动作分别继承，不跟随主说话人变化。正常播放、跳句和视频导出使用相同规则。新生成剧本也会根据场景为非发言角色保存独立反应；已有作品可用微调设置，不自动改写正文。

雏菜和日花已改用日常衣装作为默认值，两套原泳装默认衣装不再进入自动选装候选，仍可手动选择。已完成故事保存的衣装保持原样，需要时可在“场景服装”中更换。

整句日文对白被括号包裹且含句子标点时，配音前处理会仅去掉最外层括号，避免整句被当成舞台提示而没有声音；保存的正文、对白类型和切分保持。单独的“（笑う）”“（小声で）”等提示继续过滤。

夏葉的语音专用姓名写法为 `ナツハ`，与随请求发送的 `na0tsu1ha0` 读音规则匹配；显示文本不变。涉及该姓名的旧配音可能需要使用“补齐缺失语音”重新准备，不影响其他台词。

## 更新方法

更新前先结束正在进行的生成、配音或导出任务，在启动终端按 **Ctrl+C** 关闭服务，并备份 `.env`、自己修改过的人物配置、整个数据目录及自备素材。

### 使用 Git 下载的用户

在项目根目录执行：

```powershell
git pull --ff-only
cd backend
uv sync --locked
cd ../frontend
npm ci
cd ..
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Rebuild
```

服务启动后刷新浏览器；页面仍显示旧界面时按 **Ctrl+F5**。如果 `git pull` 提示本地文件存在修改，先保留并合并自己的修改，再继续更新，不要强制覆盖人物配置或使用重置命令清除它们。

macOS/Linux 用户使用相同的 Git 和依赖更新命令，然后在 `frontend` 执行 `npm run build`，按照上方的两终端方式重新启动前后端。

### 使用 ZIP 下载的用户

从 [GitHub Releases](https://github.com/qwq13243/Kaibun-SC/releases) 下载新版源码，解压到新目录，避免覆盖混用旧代码。将自己的 `.env`、数据目录和自备素材放回对应位置；人物配置有更新时，将自己的音色等自定义设置合并到新版配置中。自定义目录可继续保留在原位置，但应检查 `.env` 中的相对路径是否仍然正确。

不要复制旧的 `.runtime`、`node_modules`、`.venv` 或 `.next`。按照“快速开始”在新目录安装依赖、构建并启动。

### 更新后的作品处理

- 原有剧本、配音和素材数据可以继续使用；更新代码不会重新创作故事。
- 渲染修复不会改变已经下载的旧视频，需要重新导出。
- 更换音色或修正读音后，可在配音工具中补齐缺失语音，或重新生成指定台词。
- 已保存到故事里的服装不会因默认衣装更新而自动替换，可在“场景服装”中修改。
- 新增配置请参照 `.env.example` 按需补充，**不要用示例文件覆盖已有 `.env`**。

历次更新内容见 [更新记录](CHANGELOG.md)。

## 配置与数据

完整字段见 [.env.example](.env.example)。主要配置如下：

| 配置 | 用途 |
|---|---|
| `CHAT_LLM_*` | 大纲、对白、模型调整使用的模型与协议 |
| `KAIBUN_BACKGROUND_LLM_*` | 衣装/背景选择和看图标注；连接、密钥和模型留空时沿用 CHAT |
| `FISH_AUDIO_API_KEY`、`FISH_AUDIO_MODEL` | 可选配音服务 |
| `KAIBUN_CHARACTERS_PATH` | 人物配置，默认 `characters/characters.toml` |
| `KAIBUN_ASSET_ROOT` | 自备立绘素材根目录，默认 `assets` |
| `KAIBUN_DATA_PATH` | 全部运行数据的根目录，默认 `data` |
| `KAIBUN_PROXY_URL`、`KAIBUN_PROXY_SERVICES` | 可选 HTTP/SOCKS 代理与需要代理的服务 |
| `KAIBUN_FFMPEG` | 可选 FFmpeg 程序路径 |

相对路径统一相对于项目根目录解析，不依赖终端当前所在目录。故事数据库、配音、导出视频、诊断信息、背景和衣装缓存都放在数据根目录下。备份前先关闭服务，再备份整个数据目录及你自己的配置和素材。

人物配置保留姓名、读音、简介、印象色等资料，并附带原配置中 29 位角色的 Fish Audio `voice_id`。只填自己的 `FISH_AUDIO_API_KEY` 即可尝试调用；音色 ID 是服务上的引用标识，不是 API 密钥，也不包含声音模型或录音。模型可能被删除、变为私有或限制调用，遇到不可用时更换为自己有权使用的音色。制作人音色可用 `KAIBUN_PRODUCER_VOICE_ID` 配置；冬优子的额外偶像声线可用 `KAIBUN_FUYUKO_IDOL_VOICE_ID` 配置，不填则沿用角色音色。

人物资料在 `characters/sheets/`；创作规则在 `backend/CREATIVE_GUIDE.md`，人物补充在 `backend/character_notes.json`。这些是可修改的同人创作资料，不是官方剧情库，也不保证生成内容符合原作。

需要头像时，在对应人物配置中增加相对于人物配置文件的路径，例如 `avatar = "avatars/mano.jpg"`，并自行放入你有权使用的图片。

日花默认固定使用本音，写作模型不判断声线；可在微调中手动切换本音／偶像声线，再生成对应语音。偶像声线默认保留原音色引用，可用 `KAIBUN_NICHIKA_IDOL_VOICE_ID` 替换。多人同声时日花声线与冬优子声线分别设置。

## 可选素材与来源

发布包不包含游戏原始立绘、完整剧情库、背景原图、角色声音样本、个人故事或配音缓存。第三方资源是否可获取、可用于何种目的，应以权利人和来源方条款为准；公开可下载不等于允许再分发。

### 立绘与衣装

接入素材、在网页挑选并直接保存默认与季节衣装的步骤见[衣装与演出指南](docs/OUTFITS_AND_PREVIEWS.md)。

独立版提供自己的下载入口，使用与原项目相同的 [ShinyColorsDB](https://github.com/ShinyColorsDB) 公开目录和 CDN。**第一次建议只下载真乃的默认衣装**，确认能显示后再扩大范围。以下命令从项目根目录开始，均在 `backend` 下执行，不调用付费模型：

```sh
cd backend
# 一位角色的默认衣装：仅3个素材文件，便于先试通。
uv run python scripts/fetch_stands.py --character mano
# 全部29位角色的默认衣装；已完成文件直接复用。
uv run python scripts/fetch_stands.py
# 某位角色的全部衣装（可能有数十至上百套）。
uv run python scripts/fetch_stands.py --character mano --all
# 全角色全部衣装：数据量可能达到数GB，逐个限频下载，可能需要数小时。
uv run python scripts/fetch_stands.py --all
```

`--character` 可重复，例如 `--character mano --character hiori`。人物 id 见 `characters/characters.toml`。下载成功会输出 `READY`，最后输出 `FINISHED ready=... missing=...`；返回码 0 表示本次所选范围完整，2 表示有缺失或来源暂不可用。重新运行相同命令可接着补齐。下载器单连接、请求间隔至少 1 秒；遇到 401/403/429 会停止，不能用并发或连续重试绕过限制。文件缺失时本版保留已下载内容，不尝试绕过来源限制。

Spine 运行库是另一项可选依赖。请先阅读其许可说明；具备相应使用权时，可从固定的官方源提交下载：

```sh
# 仍在 backend 目录；仅下载运行库并核对 SHA-256，不下载衣装。
uv run python scripts/fetch_stands.py --runtime
```

若 GitHub 原始文件下载超时，可以配置终端 `HTTPS_PROXY` 后重试；也可以从 [固定版本源文件](https://github.com/EsotericSoftware/spine-runtimes/blob/654c20e5b0e523040b6366bbd1042510d2645134/spine-ts/build/spine-webgl.js) 自行保存文件，放入下述目录。许可文本随仓库保留，运行库不自动随代码发布。

立绘兼容原项目使用的 Spine 3.6 数据组织方式。目录示例：

```text
assets/
  spine/idols/stand/1030010010/
    data.json
    data.atlas
    data.png
spine_manifest.json
```

`spine_manifest.json` 放在素材根目录的父目录。它是数组，每项至少含 `idolId` 和 `path`，例如 `{"idolId":1,"path":"spine/idols/stand/1030010010/","dressName":"衣装名称"}`。人物配置中的 `idol_id` 与其对应，`default_outfit` 写为 `stand/1030010010`。该目录约定继承自原项目及其使用的 [ShinyColorsDB](https://github.com/ShinyColorsDB) 素材目录。

**Spine 运行库不随本发布包分发。**如果你有相应使用权，请自行提供匹配的 `spine-webgl.js`，放在 `frontend/public/vendor/spine-3.6/`。来源、原版本信息与许可证见该目录的说明及 [LICENSING.md](LICENSING.md)。原项目的 AGPL 附加许可不替代 Esoteric Software 自身的许可要求。

准备衣装预览和文字索引（项目根目录执行）：

```sh
cd backend
uv run python scripts/index_wardrobe.py --prepare-images --character mano
# 下列步骤会向已配置的模型发送衣装图片，可能计费：
uv run python scripts/index_wardrobe.py --index --character mano
```

可用 `--character mano` 指定人物，去掉它处理默认范围，`--all` 处理整个本地目录。看图模型由 `KAIBUN_BACKGROUND_LLM_*` 配置，需支持图片输入。Microsoft Edge 已安装时可直接用于预览；否则在 `frontend` 目录运行 `npx playwright install chromium`。生成故事时选择「默认衣装」或自行指定季节即可跳过模型选装。已完成场景不会自动重新选装。

衣装选择兼容 `outfits` 字典和 `choices` / `scenes` 场景列表等模型输出。返回格式有误时，会携带具体原因自动请求修正一次；仍失败则保留正文和已完成配音，可继续重试。选装诊断保存在数据目录的 `diagnostics/`，可能包含模型回复，不随代码发布。

下载后重启后端；运行库新增后也重启前端。**Kaibun 自己提供素材代理，不需要原项目的 8765 静态服务、S3 桶、OAuth 或 `EIDOLON_STAND_ASSETS_URL`。**

### 背景

最直接的方式是在界面的“场景背景”中导入自己的 PNG、JPEG 或 WebP。不准备剧情库也可以按已知编号下载背景，编号必须是 5 位：

```sh
cd backend
# 小范围示例，编号是否仍可下载以来源实际响应为准。
uv run python scripts/fetch_backgrounds.py --ids 00133 00538
# 下载后标注常用背景，自动选图才可检索到；此步骤会调用看图模型。
uv run python scripts/fetch_backgrounds.py --prepare-index --limit 2
```

编号可从 [公开剧情查看器](https://viewer.shinycolors.moe/) 所用剧情 JSON 的 `bg` 字段获取；不要通过扫描整个编号空间批量探测资源。你也可以使用已有、合法取得的剧情缓存，从其中的背景编号建立下载清单：

```sh
cd backend
uv run python scripts/fetch_backgrounds.py --scripts /path/to/your/cached/scripts
# 下载后再标注少量常用背景；此步骤调用模型看图：
uv run python scripts/fetch_backgrounds.py --prepare-index --limit 30
```

下载脚本继承原项目的公开剧情查看器地址 [viewer.shinycolors.moe](https://viewer.shinycolors.moe/)，保存来源、引用与校验信息，单连接限频，遇到拒绝访问或限流会停止。仓库不附带用于建立清单的剧情缓存。你可以完全不使用此下载功能。

已经在本机部署过 Eidolon-SC 的用户，可将 `--scripts` 指向旧项目的 `corpus/mirror/scripts`；只读其中的背景引用，不读取聊天数据库。本应用不需要原项目的剧情向量化/嵌入模型，也不需要为了背景而下载完整剧情库。背景文件、缩略图和标注位于 `KAIBUN_DATA_PATH/backgrounds`；有文件但没标注时可手动选图，自动选图会使用中性背景兜底。

### 字体与声音

界面使用系统字体，不需要构建时下载字体。配音由使用者配置的 Fish Audio 服务生成；本包附带角色音色引用 ID，不附带 API 密钥、原作音频、音色模型或参考录音。可在 [Fish Audio](https://fish.audio/) 查询和更换音色；能否访问及是否收费，以服务当前状态和条款为准。

## 来源、修改与权利

本项目基于 [Eidolon-SC](https://github.com/Sallyn0225/eidolon-sc)，沿用其人物/读音处理、模型协议适配、立绘渲染和部分界面代码。2026-11-14 的独立版整理包括：拆除聊天应用依赖、统一配置和数据路径、修复场景身份冲突及导入结构、恢复衣装选择逻辑、补齐无素材/无语音模式、明确关闭数据库连接、限制本机监听并增加后端访问验证。

- 项目代码与文档采用 **GNU AGPL-3.0-or-later**，见 [LICENSE](LICENSE)。保留原项目版权及许可声明；发布修改版时应保留来源与修改说明。
- Spine 的 AGPL 第 7 节附加许可及适用范围见 [LICENSING.md](LICENSING.md)。第三方组件保留各自许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- 《偶像大师 闪耀色彩》及其角色、设定、原作台词、立绘、背景、声音等相关权利归 **Bandai Namco Entertainment Inc. 及各自权利人**。代码许可证不覆盖这些权利。
- 仓库人物资料、创作指导和同人补充不构成官方设定声明；生成结果可能出错，其使用与发布仍需核对所涉权利。
- 若修改后通过网络向其他使用者提供服务，应按 AGPL 第 13 节提供运行版本的对应源代码。不要用上游仓库链接代替你实际修改版本的源码。
- 收到权利人的有效要求时，应处理相关内容；不要将本项目的粉丝性质或免责声明理解为授权。

## 隐私

故事、提示词、人物资料和修改要求会发送给你选择的模型服务；图片标注会发送相关图片；配音会向语音服务发送需要朗读的文本。服务商的数据政策由各服务商规定。

`.env`、`.runtime/`、`data/`、自备素材和构建产物已加入忽略规则。提交代码前仍应检查实际变更；忽略规则不能清除已经提交过的敏感文件及其历史。运行数据只保存在你配置的目录中，本项目没有账户同步或遥测功能。
