# 个人影音库

一个本地优先的个人影音库应用：**深色封面墙 + 豆瓣搜索导入 + 多来源评分**。
支持直接管理 Obsidian Vault 中的 Markdown，也提供不需要 Obsidian 的独立分享版。
媒体笔记始终是正式数据源，程序读写 front matter 字段，并尽量保持正文与其它字段不变。

> **使用边界**：本项目是非官方的本地工具，与豆瓣、IMDb、烂番茄、
> Metacritic、Steam、Bangumi 等平台没有隶属或合作关系。请仅用于个人、
> 非商业、低频率的元数据管理，并遵守各平台条款。软件不会附带或再许可
> 第三方海报、简介、评分数据库或商标。详见
> [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

发布包根目录提供两个明确入口：

- `双击启动影音库.exe`：启动桌面窗口版。
- `打开本地网页版.exe`：启动 `影音库服务.exe` 并自动用默认浏览器打开。

运行文件和 DLL 收在“程序”目录，媒体数据放在“数据”目录。

## 下载哪个版本

- **普通用户、第一次使用、没有 Obsidian：请下载“Windows 独立版”。**
- **已经拥有 Obsidian Vault，并要把程序放进 `.media-app`：才下载“Obsidian 修复版”。**
- **开发者：使用 GitHub 自动生成的 Source code 压缩包。**

Obsidian 修复版不是可供普通用户直接双击体验的完整桌面安装包。

当前源码与安装包版本：`2.5.19`。

GitHub：<https://github.com/UNWSX/personal-media-vault>

2.5.19 已将更保守的第三方访问规则和进程生命周期修复加入正式 Windows 安装包。个人本地定制版
仍可按自己的需要调整，但默认公开包会使用手动登录态导入、单主机图片请求和安全限速。

新条目的外部评分统一由后台补分队列处理。电影与剧集的自动补分直接包含
作品识别、季/Part、Wikidata、Bangumi/AniList 别名和来源匹配规则；
动画会依次使用原名、中文名和 Bangumi 原始标题建立英文桥接，Bangumi/AniList
瞬时网络失败时会短暂重试。已确认的系列作品仍优先使用固定 IMDb 编号。
豆瓣导入会以详情中的真实媒体类型为准，例如电影筛选返回电视剧条目时会自动
保存为剧集；重复导入旧错误条目时会迁移到正确目录，而不是再创建一份记录。
豆瓣导入保留豆瓣评分。封面下载会在明确列出的豆瓣 `img1`–`img9` 图片主机之间
重试同一条路径，避免单台主机反爬导致导入失败；每次请求仍会重新校验白名单、
解析到公网 IP 并固定连接目标。外部站点未收录或暂时无法访问时，只保留已有字段
并自动重试，不生成猜测分数。

---

## 目录结构

```
app/
  server.py          后端：本地 HTTP 服务 + 豆瓣抓取 + 笔记读写（纯 Python 标准库）
  storage_safety.py  原子写入、版本校验、原文备份与快照
  safe_network.py    图片域名白名单、DNS 固定与重定向复核
  activity_log.py    JSONL 操作日志
  web/
    index.html       页面骨架
    app.js           前端逻辑（约 1200 行，无框架）
    style.css        样式（深/浅两套主题）
  start.bat          开发用启动脚本（带控制台，便于看报错）
  app.ico            图标

shell/
  Program.cs         C# 窗口外壳入口：找/起服务，再开窗口
  MainForm.cs        窗口：WebView2 内嵌 + 深色标题栏 + 关闭时结束服务
  AppHost.csproj     .NET 7 WinForms 工程
  app.ico            图标（会被打进 exe）
```

运行时有三个进程/层：

```
影音库.exe          C# 外壳（WinForms + WebView2）
   └─ 影音库服务.exe  Python 服务（打包后的 server.py，监听 127.0.0.1:8765）
        └─ 你的库/02-电影阅读/*.md
```

---

## 运行方式

### 1. 直接从源码跑（开发用）

需要 Python 3.9+，无第三方依赖。

```bat
cd /d "D:\你的库\.media-app\app"
python server.py
```

会打印地址并自动打开浏览器。可选参数：

| 参数 | 说明 |
|---|---|
| `--vault <路径>` | 指定库根目录（默认：server.py 上一级） |
| `--port <端口>` | 起始端口，被占用会自动往后找（默认 8765） |
| `--no-browser` | 不自动开浏览器 |

### 2. 打包成完整应用

**服务端**

```bat
pip install pyinstaller
pyinstaller --onefile --noconsole --name "影音库服务" ^
  --icon app.ico --add-data "web;web" --paths . server.py
```

**窗口外壳**

```bat
cd shell
dotnet publish -c Release -r win-x64 --self-contained false -o out
```

把 `out\影音库.exe`、`out\影音库.dll`、`out\*.json`、WebView2 的几个 dll 和 `影音库服务.exe` 放在同一目录，双击 `影音库.exe` 即可。

依赖：**.NET 7 桌面运行时** + **WebView2 运行时**（Win10/11 一般自带）。

---

## 数据格式

每条记录是 `库/02-电影阅读/<类型>/<标题>.md`，程序只碰 front matter 里这些字段：

| 字段 | 读写 | 说明 |
|---|---|---|
| `title` | 读 | 标题；显示时会去掉简繁重复段（`xx 繁體` → `xx`） |
| `type` | 读 | `movie` / `teleplay` / `game` / `book` |
| `schemaVersion` | 读写 | 新数据模型版本；新笔记为 `2` |
| `mediaId` | 读写 | 创建时生成的 UUIDv4 稳定身份 |
| `score` | 读写 | 旧版评分兼容字段，量纲按媒体类型保留 |
| `myScore` | 读写 | 我的评分，0–10，0.5 星 = 1 分 |
| `doubanScore` | 读写 | 豆瓣评分，0–10 |
| `metacriticScore` | 读写 | Metacritic 评分，0–100 整数 |
| `imdbScore` | 读写 | IMDb 评分，0–10 |
| `bangumiScore` | 读写 | Bangumi 评分，0–10 |
| `rottenTomatoesScore` | 读写 | 烂番茄指数，0–100 整数 |
| `status` | 读写 | 完成位；界面只提供未完成/已完成两种状态 |
| `progressStatus` | 读写 | 按媒体类型区分的进度状态 |
| `comment` | 读写 | 我的评价 |
| `startDate` | 读写 | 开始日期 `YYYY-MM-DD` |
| `finishTime` | 读写 | 完成日期 `YYYY-MM-DD` |
| `purchaseDate` | 读写 | 购买日期（游戏） |
| `price` | 读写 | 购买价格（数值） |
| `platform` | 读写 | 平台列表（游戏） |
| `image` | 读 | 封面路径（相对库根） |
| 其它 | 只读 | 导演、演员、题材、简介等，用于展示 |

写入时按字段替换，**不会重排其它字段**；状态/评分/封面变化只同步对应看板行。
写前保存原文、使用同目录临时文件，写后严格解析校验并原子替换。

---

## 后端 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/library` | 全部条目（含 `obsidian` 模式标记、`root` 路径） |
| GET | `/api/search?q=&type=` | 豆瓣搜索（movie/teleplay/book/game） |
| POST | `/api/import` | 按豆瓣 id 导入并建笔记 |
| POST | `/api/create` | 手动创建 |
| POST | `/api/item` | 更新单个字段（`{path, ...}`） |
| POST | `/api/delete` | 删除（移到 `.media-app/trash/`，可恢复） |
| POST | `/api/fix-image` | 重新下载封面 |
| POST | `/api/backup` | 立即备份媒体笔记和附件 |
| GET | `/api/diag` | 自检：真的去问一次豆瓣，返回状态码和片段 |
| GET | `/media?p=` | 本地图片 / 豆瓣图床代理 |
| GET | `/static/*` | 前端静态资源（不缓存） |

---

## 几个实现要点

**豆瓣接口**

| 用途 | 接口 |
|---|---|
| 电影/剧集/图书搜索 | `movie.douban.com/j/subject_suggest`、`book.douban.com/j/subject_suggest` |
| 游戏搜索 | `www.douban.com/j/search?cat=3114`（返回 JSON，`items` 里是 HTML 字符串） |
| 详情 | `m.douban.com/rexxar/api/v2/{movie,book}/{id}`（需 Referer） |
| 游戏详情 | 直接解析 `www.douban.com/game/{id}/` 的条目页 |

媒体搜索优先采用与 Obsidian 豆瓣插件相同的 `www.douban.com/j/search` 接口；
搜索页不可用时才回退到 `subject_suggest`，避免上游返回空数组时误显示“没搜到”。

**登录态导入**：游戏接口匿名可能返回 403。用户可以在界面中主动选择
`.obsidian/plugins/obsidian-douban-plugin/data.json`，把插件已有的登录态导入本程序；
程序不会在后台静默读取插件配置。导入后的 Cookie 只在本机使用，并由 Windows
DPAPI 加密保存。

**封面**：下载请求只使用上游返回的原始 URL，不再轮换图片主机或在访问受限后重试其他主机。
下载后用文件头魔数校验（JPEG/PNG/WebP），**不是图片就绝不落盘**；
`/spic/` 这类小图会自动升级成 `/lpic/`。

**标题排序**：中文数字（第X季、X部）按数值比较，`第八季` 不会排到 `第一季` 前面；
续作编号（`漫威蜘蛛侠2`）排在标点/空格变体（`漫威蜘蛛侠 复刻版`）之后；
普通中文标题按拼音。

**窗口外壳**：WebView2 不认 `obsidian://` 协议，点击「在 Obsidian 中打开」时由 C# 侧
调用系统默认程序打开；关闭窗口会按进程树结束内嵌服务，不留后台进程。

---

## 已知限制

- 只支持 Windows（WinForms + WebView2）。
- 游戏搜索依赖豆瓣登录态；没登录时电影/剧集/图书仍可用，游戏搜不到。
- 删除时会把笔记和未被其它条目引用的本地主封面一并移入回收站；
  被多条记录共用的封面会保留。
- 远程图片只允许明确列出的豆瓣、Steam 和 Bangumi 图片主机；不在白名单的远程图片不会通过 `/media` 显示。
- 单文件 exe 首次启动要解压，约 4–5 秒。

---

## 2026-11-07 更新（标题/封面/评分）

**1. 标题清洗重写**（`clean_title` / `cleanTitle`，前后端一致）

- 仅当标题含汉字时才剥离外文；纯英文标题（`NBA 2K24`、`GTA 5`）原样保留
- 按 `/` `|` 分段，第一段含中文就丢掉后面的纯外文段
- 空格分词后从左往右扫：遇到**纯英文词组**、**日文假名**、或与前文重字率 ≥0.3 的**繁体异体尾段**就截断
- `TITLE_SUFFIX`（复刻版/重制版/导演剪辑版…）和数字续作编号一律保留
- 游戏标题缺英文名时，用内置 `TITLE_EN_ALIAS` 表（90+ 条）查 Steam；也可从豆瓣条目页「原名」兜底

**2. 封面管道**

优先级：Steam `library_600x900_2x.jpg` → Steam `library_600x900.jpg` → Bangumi `images.large` → 豆瓣 `/lpic/`。
已移除 `header.jpg`（横图放进 2:3 海报框会变形）。Steam 走 `storesearch`（先国区中文、再英文）拿 appid。

**3. 游戏改用 Metacritic 评分**

- 先查 Steam `appdetails` 的 `metacritic.score`，失败再用 Metacritic 官方搜索 API
- 存 0–100 原始分；`score` 校验对游戏放宽到 0–100
- 前端：详情页显示「Metacritic 评分 92 / 100」，悬停显示 `MC 92`；
  排序/筛选通过 `normScore()` 把 MC÷10 与豆瓣 10 分制混排

**4. 批量升级现有游戏库**

```bat
python server.py --upgrade-games --vault "D:\你的库"
```

或 `POST /api/upgrade-games`。会：清洗标题、换高清竖版封面、抓 MC 写入 `score`、
同步正文 Callout 里的名称/评分/状态。开跑前先探测上游，全不通会直接提示并退出。

**5. 四个修复**

- 完成时间排序：未填日期的永远排在有日期的后面（删掉了假日期 `2026-06-26`）
- 封面缓存：`/media` 请求带 `&t=<mtime>`，换图后立即生效
- `/media` 代理：只有 `douban.com` / `doubanio.com` 才带 Cookie，其它外链只带 UA
- 状态/评分写入时同步正文 Callout（`> **📝 状态**`、`> **⭐ 评分**`）

## 2026-11-07 阶段 1：稳定性与数据安全

- 备注自动保存固定目标笔记路径；切换、关闭和失焦时会提交，连续输入会合并。
- 关闭整个桌面窗口前会等待全部待保存备注；保存失败或超时时窗口保持打开。
- 从 Vault 外运行打包版时，桌面外壳读取 `vault-path.txt` 指定库路径；服务实例同时校验 Vault 路径，避免连接错误实例。
- 保存使用路径锁、文件哈希修订号、同目录临时文件、回读验证和原子替换。
- 检测到 Obsidian 或其他进程修改同一笔记时返回 409，本次保存不覆盖外部内容。
- 每次修改已有 Markdown 前保存原文到 `.media-app/backups/notes/`。
- 顶栏新增“立即备份”按钮，快照保存在 `.media-app/backups/snapshots/`。
- 操作日志写入 `.media-app/logs/media-vault-YYYYMMDD.jsonl`。
- `/media` 只读取 `attachments/` 中的有效图片；远程图片使用固定域名白名单和 DNS 固定连接。
- 写入 API 校验 Host、Origin、JSON 类型、字段类型、路径和请求体大小。
- 豆瓣添加窗口的快速搜索会取消旧请求，并丢弃过期响应。
- 状态、评分和游戏字段保存失败时会回滚或明确显示未保存状态。

备份与恢复步骤见 [`docs/phase1-operations.md`](docs/phase1-operations.md)。

## 2026-11-07 阶段 2.3：数据模型兼容层

- 后端读取统一经过 `app/media_compat.py`：集中处理新旧评分、进度状态、身份字段和标签归一化。
- 旧 `score`、`status` 继续返回并可读写；新版 API 增加专用评分、`progressStatus`、`schemaVersion` 和 `mediaId`。
- 新笔记写入 UUIDv4 `mediaId`、`schemaVersion: 2` 和可确定的初始进度状态。
- 旧笔记缺失的新字段由读取层映射，不在普通打开、刷新或编辑时自动迁移。
- 前端按评分来源显示量纲，并将状态分为未开始、进行中、已完成；`music` 保持旧二元状态。
- 409 合并使用 `baseItem`、`localDraft`、`latestItem` 三方比较。状态和主评分按兼容组冲突处理，无法自动选边时保留本地草稿并等待用户选择。
- 状态 Callout 只在正文代码块之外、恰好一个匹配且当前值可唯一识别时更新；失败不会回退已成功的 Frontmatter 状态，并会向用户显示未同步提示。
- 未来 schema、非法或重复 `mediaId` 的笔记保持只读，不自动改写。

阶段实现与测试说明见 [`docs/stage2-2.3/README.md`](docs/stage2-2.3/README.md)。本阶段没有执行旧数据迁移，也没有写入真实 Vault。

## 2026-11-07 阶段 2.4/2.5：迁移引擎与副本验收

新增本地迁移工具：

```powershell
python app\server.py --migrate-vault "D:\your\vault" --scan
python app\server.py --migrate-vault "D:\your\vault" --dry-run
python app\server.py --migrate-vault "D:\your\vault" --apply --plan ".media-app\migration\plans\plan-<id>.json"
python app\server.py --migrate-vault "D:\your\vault" --rollback --plan ".media-app\migration\plans\plan-<id>.json"
```

Apply 和 Rollback 必须输入完整确认文本：

```text
APPLY <planId>
ROLLBACK <planId>
```

安全规则：

- Web API 只提供只读 Scan 和报告读取，不能 Apply 或 Rollback。
- Dry-run 只写应用元数据，不修改 Markdown 或 mtime。
- Plan 使用固定 UUID、原文件哈希、候选哈希、操作组和不可变 checksum。
- Apply 前创建并复读校验专用快照。
- 替换前、临时文件完成后和替换后都校验哈希。
- 外部修改会使该篇进入 `CONFLICT`，不会用旧 Plan 覆盖。
- 单篇失败不停止后续可安全处理的文件。
- Rollback 只恢复当前内容仍等于 Plan 后哈希的文件；用户迁移后编辑会进入 `RESTORE_CONFLICT`。
- 全局锁防止同一 Vault 并发 Apply。

## 独立版升级数据迁移助手

普通用户从旧版独立包升级到新版时，可使用“影音库数据迁移助手”：

1. 先关闭影音库、网页版和 Obsidian。
2. 选择旧版程序目录或其中的“数据”目录。
3. 选择新版程序目录或其中的“数据”目录。
4. 扫描并检查新增、覆盖、跳过和冲突文件。
5. 确认后执行；程序会先完整备份新版目标目录。

迁移采用合并复制，不删除源目录，也不会删除目标中源数据没有的文件。默认只迁移
`媒体库` / `02-电影阅读`、`attachments` 和 `.media-app`，跳过锁文件、临时文件
与符号链接。迁移结束后会在目标 `.media-app/migration/data-migrations/`
生成 JSON 和 Markdown 报告。

源码入口：

```powershell
python app\data_migration_gui.py
```

详细说明见 [`docs/data-migration-assistant.md`](docs/data-migration-assistant.md)。

详细说明：

- [`docs/stage2-2.4/README.md`](docs/stage2-2.4/README.md)
- [`docs/stage2-2.5/README.md`](docs/stage2-2.5/README.md)
- [`docs/stage2-2.5/operations.md`](docs/stage2-2.5/operations.md)

## 2026-11-07 UI 回归版

- 状态筛选和详情页恢复为二元状态：未完成/已完成。
- 排序菜单位置调整为标题、发行、完成时间、我的评分、网络评分。
- 详情页恢复 1.0 的双栏结构、状态开关和评分布局。
- 游戏外部评分固定为 Metacritic，其余媒体外部评分固定为豆瓣，不再显示“来源未确认”。
- 外部评分只读，不再在详情页手动填写；旧版 `score` 已按用户确认的来源补齐专用字段。
- 电影、剧集支持 `rottenTomatoesScore`，新导入内容会尝试自动查询烂番茄指数。
- 移除详情页“统一比较分”。
- 页面滚动条改为顶栏下方的自绘轨道。
- 修复窗口或非全屏状态下详情页简介塌缩的问题。

详细记录见 [`docs/ui-2.5.1/README.md`](docs/ui-2.5.1/README.md)。

2.5.3 的 IMDb 与评分显示调整见
[`docs/release-2.5.3/README.md`](docs/release-2.5.3/README.md)。

2.5.4 的分季、动画别名、Bangumi 与排序修订见
[`docs/release-2.5.4/README.md`](docs/release-2.5.4/README.md)。

## 烂番茄指数批量补全

批量工具只处理 `movie` 和 `teleplay`，并且必须同时满足以下条件才写入：

- 豆瓣记录的稳定 ID 能定位到原始标题；或笔记已有可信的 `originalTitle` / `aliases`；
- Rotten Tomatoes 公开搜索结果的类型、规范化标题和发行年份完全一致；
- 唯一候选带有 0–100 的影评人指数；
- 笔记修订号在预览后没有变化。

```powershell
python app\batch_rotten_tomatoes.py `
  --vault "D:\your\vault" `
  --dry-run `
  --cache "D:\backup\lookup-cache.json" `
  --output "D:\backup\preview-plan.json"

python app\batch_rotten_tomatoes.py `
  --vault "D:\your\vault" `
  --apply `
  --plan "D:\backup\preview-plan.json" `
  --confirm "APPLY <planId>" `
  --output "D:\backup\apply-report.json"
```

`--dry-run` 只读 Markdown。`--apply` 会先创建包含媒体笔记和附件的快照，再逐篇通过
`Library.update()` 的修订检查、原子写入和写后校验。已有 `rottenTomatoesScore`
的笔记默认跳过；无法唯一匹配的笔记保留原样并写入预览报告。

## IMDb 评分批量补全

电影和剧集的 IMDb 评分使用同一套保守规则：先用 IMDb 官方建议接口定位候选，
再通过 IMDb GraphQL 复核规范化标题、发行年份和媒体类型，只有唯一结果才写入
`imdbScore`。

```powershell
python app\batch_imdb.py `
  --vault "D:\your\vault" `
  --dry-run `
  --cache "D:\backup\imdb-cache.json" `
  --output "D:\backup\imdb-preview.json"

python app\batch_imdb.py `
  --vault "D:\your\vault" `
  --apply `
  --plan "D:\backup\imdb-preview.json" `
  --confirm "APPLY <planId>" `
  --output "D:\backup\imdb-apply.json"
```

已有 `imdbScore` 默认跳过。电影或剧集没有烂番茄可靠数据时，详情页不显示烂番茄模块；
没有 IMDb 评分时同样不显示 IMDb 模块。

## 分季评分、动画别名与 Bangumi

分季剧集使用标题中的季号执行 IMDb 和 Rotten Tomatoes 分季查询。IMDb 季评分取该季
全部有评分剧集的平均值；Rotten Tomatoes 使用对应 `/tv/<series>/sNN` 季页面的
Tomatometer。

动画条目会通过 Bangumi 的日文名和 AniList 的英文名/罗马字名转换查询标题。Bangumi
评分写入 `bangumiScore`。批量工具：

```powershell
python app\batch_enhanced_ratings.py `
  --vault "D:\your\vault" `
  --dry-run `
  --proxy "http://127.0.0.1:7897" `
  --output "D:\backup\ratings-preview.json" `
  --cache "D:\backup\ratings-cache.json"
```

`--proxy` 仅用于当前网络无法直连 Bangumi 的情况，可按实际代理地址替换。
批量补分默认且至少保持 1 秒请求间隔；遇到 403、429、验证码或其他访问限制时应停止执行。

标题排序采用“系列分组、组内按发行日期从旧到新”；搜索命中标题时优先只展示标题结果，
避免剧情介绍提到角色时出现不相关作品。网络评分排序优先使用 IMDb，缺失时回退豆瓣。
不存在的豆瓣、烂番茄、IMDb 或 Bangumi 评分不会显示“未收录”。

## 中文/日文片名到外部评分编号

对 IMDb 或烂番茄无法直接通过中英文标题搜到的作品，评分工具会通过 Wikidata 查找
稳定的 IMDb ID 和 Rotten Tomatoes ID，再按编号获取评分。该方案覆盖中文电影、
奥特曼系列及部分日本动画作品，也避免了仅依赖标题翻译造成的误匹配。

```powershell
python app\batch_enhanced_ratings.py `
  --vault "D:\your\vault" `
  --dry-run `
  --missing-only `
  --proxy "http://127.0.0.1:7897" `
  --output "D:\backup\missing-ratings-preview.json"
```

只有 Wikidata 条目的类型、标题和年份能够可靠匹配时才使用其外部 ID。来源网站本身没有
评分或评分字段为空的记录不会猜测写入。

## 2026-11-08 独立版豆瓣登录模块

独立版新增内置豆瓣登录窗口。添加媒体时可以直接登录豆瓣，也可以导入 Obsidian
豆瓣插件的 `data.json`。登录态使用当前 Windows 用户的 DPAPI 加密，只保存在本机，
API 和日志不会返回 Cookie。没有登录态时继续使用公开搜索回退。

相关源码：

- `app/douban_auth.py`
- `shell/DoubanLoginForm.cs`
- `/api/douban/session`
- `/api/douban/logout`

详细说明见 [`docs/release-2.5.12/README.md`](docs/release-2.5.12/README.md)。

2.5.13 进一步校正登录状态界面：已登录时不再显示登录状态条和登录入口；
搜索框与类型切换框统一高度，右侧自绘滑轨也恢复拖动、轨道点击、滚轮和
键盘交互。说明见 [`docs/release-2.5.13/README.md`](docs/release-2.5.13/README.md)。

## 许可

项目原创代码采用 [MIT License](LICENSE)。该许可证不覆盖第三方代码、图片、
简介、评分数据、API 内容或商标。使用第三方服务时请遵守对应条款，并阅读
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
