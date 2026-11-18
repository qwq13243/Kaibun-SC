# Kaibun 本机部署指南（用户与 Agent）

本指南适用于本仓库，先读根目录 README。目标是本机单用户体验，不是公网服务；不要套用 Eidolon-SC 的登录、OAuth、Docker、S3 和数据库部署命令。本项目独立运行，默认前端 3010、后端 8011。

## 1. 确认当前环境与目标

先确认操作系统、实际项目路径和用户希望启用的功能：文字、配音、立绘、背景、视频。先跑通文字，再增加其余功能。检查已有进程，避免启动第二套服务或覆盖原配置；有 `.env` 时保留，不用模板覆盖。

Agent 不要让用户把密钥发到聊天里。可替用户写接口地址、模型名等普通配置；需要新密钥时说明应在服务商控制台创建并自行写入 `.env`，只检查“是否填写”，不显示实际值。已有本地配置且用户授权使用时，无需反复索要相同信息。

## 2. 安装依赖

Windows PowerShell：

```powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
winget install --id OpenJS.NodeJS.LTS -e
```

安装后重新打开终端。无需再次安装已有组件；手动安装来源分别为 [Git](https://git-scm.com/)、[uv](https://docs.astral.sh/uv/getting-started/installation/)、[Node.js](https://nodejs.org/)。macOS 可用 `brew install git uv node`。Linux 用系统包管理器装 Git，按 uv/Node 官方安装说明安装另外两项。Node 需 22 或更高版本，uv 可以管理 Python 3.11+。

检查 `git --version`（仅克隆时需要）、`uv --version`、`node --version`、`npm --version`。Windows 若 PowerShell 阻止 npm.ps1，请用 `npm.cmd`，不必修改全系统执行策略。

项目目录来自用户已经下载的本仓库，或其提供的 Kaibun 仓库地址；**不要编造尚未发布的 GitHub 克隆地址**。所有后续命令中的“项目根目录”指 README 所在目录。

```powershell
# Windows，项目根目录
Copy-Item .env.example .env  # 仅在 .env 不存在时执行
notepad .env
```

macOS/Linux 等价命令是 `cp .env.example .env`，再用本机编辑器填写。

## 3. 配置最小模型连接

填写：

```dotenv
CHAT_LLM_PROTOCOL=chat-completions
CHAT_LLM_BASE_URL=服务商要求的接口根地址
CHAT_LLM_API_KEY=在本机填写
CHAT_LLM_MODEL=服务商实际提供的模型标识
```

协议可选 `chat-completions`、`responses`、`anthropic`；接口根地址通常到 `/v1`，不填写成完整的 `/chat/completions`、`/responses` 或 `/messages` 地址。生成需要文字流式输出，并能按提示返回完整 JSON；Kaibun 不需要原项目聊天工具调用和嵌入服务。

`CHAT_LLM_MAX_TOKENS` 需要足够容纳一幕双语对白。若模型不支持独立思考参数，先将 `CHAT_LLM_REASONING_EFFORT` 留空，不盲目填写其他模型的参数。`CHAT_LLM_EXTRA_BODY` 只接受 JSON 对象，不允许覆盖程序生成的 messages/model 等字段。

模型具体名称、价格和申请步骤以服务商当前官方文档为准，不将本指南中的示例当作可用额度保证。先让用户完成一个短篇，避免第一步就制作一小时长篇。

## 4. 网络连接与代理

下载依赖、GitHub 原始文件、衣装和背景的命令使用终端网络环境。例如用户有正在运行的本地 HTTP 代理时：

```powershell
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'  # 示例，替换为实际代理地址和端口
$env:HTTP_PROXY = $env:HTTPS_PROXY
```

macOS/Linux 用 `export HTTPS_PROXY=http://127.0.0.1:7890`。未使用代理就不要设置；退出终端会清除这些临时环境变量。

模型与配音调用使用 `.env` 里的独立设置：

```dotenv
KAIBUN_PROXY_URL=http://127.0.0.1:7890
KAIBUN_PROXY_SERVICES=chat_llm,tts
```

无需代理的服务从列表中去掉。不用代理时把 URL 留空。这与原项目的 `EIDOLON_PROXY_*` 不同，不要直接复制其变量名。

## 5. 安装、检查、启动

Windows 最便捷方式（项目根目录）：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

脚本会安装锁定的 Python 依赖，首次安装前端依赖并构建，启动后等待本地健康检查成功，再给出页面地址。保留该终端，按 Ctrl+C 会关闭本次启动的服务。修改源码后用 `-Rebuild`。如果新装系统找不到 Python，包装脚本通过 uv 创建项目虚拟环境。

macOS/Linux 推荐两终端手动启动；Windows 也可如此，便于看清错误：

```sh
# 终端一，在项目根目录开始
cd backend
uv sync --locked
uv run python scripts/doctor.py
uv run python server.py
```

```sh
# 终端二，在项目根目录开始
cd frontend
npm ci
npm run build
npm run start
```

启动后打开 <http://localhost:3010>，第三个终端从 `backend` 执行 `uv run python scripts/doctor.py --running`。应看到人物数量和本地前端到后端连接正常；可选素材缺失不作为致命错误。doctor 不调用模型、Fish Audio 或看图服务。

**文字验收**：选一位角色、最短篇幅，输入简单情境；首次可先选“剧本精调”审阅大纲。继续生成后应显示台词、保存并可播放。刷新页面后作品仍存在。只有这一步成功，才能说真实模型创作已跑通；`health` 成功只能证明本地连接正常。

## 6. 配音

29 位角色的 `voice_id` 已附在 `characters/characters.toml`，不必逐个重新填写。用户在 Fish Audio 创建自己的 API key，写入 `FISH_AUDIO_API_KEY` 后重启后端；音色模型名由 `FISH_AUDIO_MODEL` 配置。不要保证某个模型永久免费或所有 ID 永久可用。

先选一位角色，用一两句测试配音，确认可听到声音且音色正确。作品已经完成但此前无语音时，用“配音工具 → 一键生成缺失语音”；不要为试声音重写整个剧本。某个 ID 私有、删除或无权调用时，请在人物配置中替换。音色 ID 与密钥是两回事，只有 ID 不会替用户承担调用费用或授予声音权利。

不需要声音可清空 `FISH_AUDIO_API_KEY`；单个角色想关闭配音，可将其 `voice_id` 设为空字符串。修改配置后重启后端。

## 7. 立绘、运行库和衣装索引

详细来源及命令见 README 的“立绘与衣装”。下载默认衣装通常只需一小部分文件；全角色全部衣装可能占数 GB、需要数小时。先处理用户选择的人物，不默认拉全量。素材下载不调用语言模型；看图标注会发送图片并可能计费，应向用户说明。

从项目根目录执行：

```sh
cd backend
uv run python scripts/fetch_stands.py --character mano
# 用户具有相应 Spine 使用权时：
uv run python scripts/fetch_stands.py --runtime
uv run python scripts/index_wardrobe.py --prepare-images --character mano
# 需要随剧情选衣，且配置了支持看图的模型时：
uv run python scripts/index_wardrobe.py --index --character mano
```

预览使用 Edge 或 Playwright Chromium。未安装 Edge 时，从 `frontend` 执行 `npx playwright install chromium`。运行库下载校验与目录遵循 README；不能只下载衣装就声称立绘已可显示。

默认布局为根目录 `assets/spine/idols/...`，根目录 `spine_manifest.json`。若用户自行修改 `KAIBUN_ASSET_ROOT`，manifest 应在该素材目录的父目录。不要再启动 8765 服务或配置原项目的 S3 地址。

**立绘验收**：重启后端和前端，选择真乃，打开服装面板应看到衣装与预览；播放一幕时能看到立绘。如果只想验证显示，可选「默认衣装」并手选已有衣装，跳过辅助模型的季节判断。缺少素材时姓名/头像兜底不是渲染成功的证据。

## 8. 背景

可直接在网页导入用户自备图片。下载方式见 README，推荐先从已知编号准备少量：

```sh
cd backend
uv run python scripts/fetch_backgrounds.py --ids 00133 00538
uv run python scripts/fetch_backgrounds.py --prepare-index --limit 2
```

编号存在与否取决于来源。不必为了背景搭建 Eidolon-SC 或下载完整剧情库；已有旧项目缓存时，`--scripts` 可以只读它的 `corpus/mirror/scripts`。下载后检查 `data/backgrounds/manifest.json` 的计数；标注完成后才可用于自动选择，手动选择不需要标注。

**背景验收**：网页“场景背景”应能看到已下载或自行导入的缩略图；手动选中并播放后图像出现。自动选图是另一项检查：要有已标注背景，模型不可用时应正常退回本地候选或中性背景。

## 9. 视频、更新与备份

视频导出需要 Node、可运行的 Edge/Chromium 和 FFmpeg。依赖已包含 imageio-ffmpeg；若其二进制不适合本机，可安装 FFmpeg 并在 `.env` 的 `KAIBUN_FFMPEG` 指定完整路径。先导出只有几句台词的一幕，检查画面与声音后再尝试长篇。长篇录制按实际时长执行，不要把耗时当成卡死，也不要先承诺未测过的一小时视频已可用。

改 `.env` 或人物配置：关闭并重启后端；更新前端源码：重新构建并重启前端。使用一键启动时，Ctrl+C 后再运行 `start.ps1 -Rebuild` 即可。手动跑的两个终端分别 Ctrl+C。

备份前关闭服务，备份 `.env`、人物自定义配置、整个 `KAIBUN_DATA_PATH` 及自备素材目录。恢复时先放回这些文件，再启动；不要仅备份 stories.db 而漏掉配音和背景。向 GitHub 提交时只提交代码、文档与许可，排除 `.env`、`.runtime`、data、下载素材、运行库和构建目录。

## 10. 排障

| 表现 | 检查与处理 |
|---|---|
| uv/npm 找不到 | 安装后重新开终端；Windows 用 npm.cmd；uv 有路径但未加入 PATH 时用完整路径执行，不写进发布代码 |
| `.env` 填了仍报缺项 | 检查文件确实为根目录 `.env`，不是 `.env.txt`，以及是否存在同名进程环境变量覆盖；不要打印密钥 |
| 无法启动、端口占用 | 查 3010/8011 的占用进程，只关闭本项目的旧实例；原项目用 3000/8010，不应为本项目停止它 |
| 页面能打开但接口 503 | 后端未启动、配置失败，或 `.runtime/connection.json` 不属于当前运行实例；重启本项目前后端并读日志 |
| 直接访问 8011 返回 403 | 正常，后端要求本机令牌；浏览器使用 3010 的页面和代理，不关闭验证来修复 |
| 模型生成失败 | 看页面的失败阶段，核对模型名、协议、额度、代理、输出上限；诊断回复可能包含故事内容，不公开上传整个 data 目录 |
| 图片标注/换衣失败 | 确认选装模型支持图片、运行库和浏览器可用；可先固定衣装，不需要强行换写作模型 |
| 衣装下载中断或缺失 | 重新运行相同范围可补齐；401/403/429 时停止并按来源要求等待，不并发绕过限制 |
| `--runtime` 下载超时 | 检查 GitHub 原始文件网络和终端 HTTPS_PROXY；按 README 的固定版本链接手动保存，核对哈希 |
| 背景未出现 | 检查下载是否成功、已选场景是否保存、是否用正确数据目录；没有标注时自动选图会回退中性背景 |
| 配音失败 | 检查 Fish key、模型、余额/限制、代理及该 voice_id 的访问权；可更换音色后补齐缺失语音 |
| 视频导出失败 | 查 `data/exports/` 日志、Edge/Chromium 与 FFmpeg，先用单幕短片复现 |

## 给 Agent 的完成标准

报告已经实际验证的部分，区分“配置检查通过”“页面连接正常”“模型生成成功”“声音/立绘/背景/导出验收完成”。不要将未测试的付费接口或可选素材写成已完成。用户只是要操作说明时，给出命令，不自动进行数 GB 的全量下载。用户已经明确授权部署某项功能时，在该范围内继续完成安装和验证，不反复询问同一许可。
