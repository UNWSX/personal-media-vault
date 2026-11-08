# 个人影音库 2.0 后续阶段正式任务书

版本：1.0

状态：供 Codex 后续实施使用，等待用户批准

编制基线：源码提交 `6fd8677`，阶段 1 源码提交 `703e372`

当前权限：只允许文档审查；禁止修改源码、执行迁移或写入真实 Vault

## 1. 本任务书的地位

本任务是后续 2.2–2.5 实施的唯一控制文件。此前由 Gemini 3.8 Flash 和 ChatGPT 免费版共同形成、并经 Codex 整理的文档保留为设计材料；如果与本任务书冲突，以本任务书为准。

本任务书优先依据：

1. 当前真实源码和实际 Markdown。
2. 阶段 1 已实现的安全写入、revision、409、备注草稿和快照能力。
3. Markdown 为唯一正式数据源。
4. 不新增运行依赖。
5. 任何无法证明安全的数据操作默认跳过并报告。

## 2. 对输入任务书的审查结论

### 2.1 必须纠正的问题

| 原要求/假设 | 问题 | 正式决定 |
|---|---|---|
| 可使用 PyYAML/ruamel.yaml | 与“零新增外部依赖”冲突 | 只用 Python 标准库实现受限 YAML 子集 |
| 游戏 `score > 10` 即可判定 Metacritic | 数值范围只能说明量纲，不能证明来源 | 仅 L2 候选，不自动迁移 |
| 有 `steamAppId`/`doubanId` 即可确定评分来源 | 平台 ID 只证明条目关联，不证明评分数值源自该平台 | 仅 L2 候选 |
| L1–L4 四等级评分证据 | 新旧任务书冲突，后两级处理重复，界限不清 | 统一为 L1 确定、L2 候选、L3 不明 |
| 所有评分待确认项会阻断全部迁移 | 会导致基础 schema 迁移也无法推进 | 基础迁移与评分增强拆开；L2/L3 只阻止评分专用字段迁移 |
| 所有迁移必须一步完成 | 风险大且失败难审计 | 拆为基线迁移、评分增强、Callout 同步三个计划操作组 |
| 只更新 revision 即可继续保存 | 会覆盖 Obsidian 新字段 | 2.3 必须实现字段级 `dirtyFields` 合并 |
| 一键全局回滚 | 会覆盖迁移后用户编辑 | 只允许逐文件哈希确认的 CLI 回滚 |
| Callout 全局正则替换 | 可能修改多个正文位置 | 必须唯一匹配且只改状态文本 |
| 现有快照函数可直接用于迁移 | 当前函数会自动清理旧快照，且未验证迁移事务语义 | 新建迁移专用快照流程，不直接套用清理策略 |
| 所有输入文档中的字段名天然正确 | 当前代码存在 `genre`/`tags`、`scoreStar` 等历史差异 | 由兼容层统一映射，不删除旧字段 |
| 文档中的固定 292 条可作为验收值 | 真实库会继续变化 | 每次验收记录实际值，不硬编码数量 |
| BASE_SCHEMA 是否处理状态冲突不明确 | 可能擅自选择旧状态或新状态覆盖另一方 | 冲突状态独立成操作组，BASE_SCHEMA 不修改 |
| 409 只比较本地和最新值 | 无法区分远端修改与双方同时修改 | 使用 base/local/latest 三方比较 |
| “绝不覆盖”可能被理解成可完全消除竞态 | 哈希检查和 os.replace 不是比较交换 | 明确可检测冲突与无法消除的竞态窗口 |
| Dry-run 无写入要求未完整出现 | 可能在预览时修改 mtime 或目录 | 冻结为严格只读，仅写计划和报告 |
| tags/genre 双写缺少语义定义 | 两个字段可能分别表示“题材”和“用户标签” | 只做只读归一化；本阶段不自动双写或迁移 |

### 2.2 当前源码和数据只读核对

本次审查只读检查了阶段 1 最新源码和当前 Vault，未执行正式 Scan。

已确认：

- 媒体数据位于 `02-电影阅读/**/*.md`。
- 当前可编辑媒体类型仍有 `music` 代码入口，但本次实际数据只有 movie、teleplay、game、book。
- 当前真实数据在审查时为 295 条：movie 133、game 92、teleplay 64、book 6。
- 电影、剧集、图书使用 `tags`；游戏使用 `genre`。
- 当前模板和 Dataview 看板依赖 `scoreStar`。
- Callout 已观察到的状态标记为 `📝 状态` 和 `🕹️ 状态`。
- `sync_callout()` 当前使用正则替换，多个匹配时可能全部替换。
- `revision` 为原始文件字节 SHA-256。
- 409 返回 `latest` 和 `conflict: true`。
- `commentDrafts` 和 `saveQueues` 已存在，但尚无完整通用 `dirtyFields`。
- 当前原子写入已包含备份、最终替换前哈希检查和条件回滚。
- 快照函数有 `source_paths`、`label`、`snapshots_root` 参数，并默认清理较旧快照。
- 当前 Metacritic/豆瓣导入有代码路径，但没有逐条历史评分来源审计，因此现有历史数据原则上不能自动达到 L1。

以上结果只说明当前代码状况，不表示阶段 1 已正式验收。

## 3. 冻结的数据模型

### 3.1 媒体身份

未来新创建或完成基线迁移的媒体笔记必须包含：

```yaml
schemaVersion: 2
mediaId: "<小写 UUIDv4>"
```

- `schemaVersion`：Integer，当前目标值为 `2`。
- `mediaId`：UUIDv4 字符串，由 `str(uuid.uuid4())` 生成。
- 已存在的合法 UUIDv4 永久保留。
- 重复、非法或非 v4 的 `mediaId` 不自动修复。
- `schemaVersion > 2` 的文件只读。
- 没有 `schemaVersion`/`mediaId` 的旧笔记仍可读取。
- 基础迁移可以在评分来源待确认时添加身份字段，但不写专用评分。

### 3.2 持久化字段

核心字段：

```yaml
title:
originalTitle:
aliases: []
type:
year:

doubanId:
imdbId:
bangumiId:
steamAppId:

image:
myScore:
doubanScore:
metacriticScore:
imdbScore:
bangumiScore:

status:
progressStatus:
startDate:
finishTime:
tags:
comment:
```

字段必填性：

| 类别 | 规则 |
|---|---|
| 身份必填 | 新建及完成 v2 迁移后必须有 `schemaVersion`、`mediaId` |
| 业务必需 | 新建时 `title`、`type` 必须有；旧文件缺失时保留并警告 |
| 可选字段 | 缺失表示未提供，不自动写空值 |
| 计算字段 | `normScore` 等不落盘 |
| 旧兼容字段 | 保留历史语义，不因新字段出现而删除 |

旧字段继续保留：

```yaml
id:
score:
scoreStar:
finishTime:
status:
genre:
country:
director:
author:
publisher:
actor:
desc:
platform:
purchaseDate:
price:
pages:
isbn:
series:
translator:
currentDate:
dateModified:
imageDataurl:
```

### 3.3 标签兼容

- 规范读取字段为 `tags`。
- 兼容读取 `genre`。
- 运行时按以下顺序生成只读归一化视图：`tags` 原顺序，再追加 `genre` 中未出现项。
- 标签比较使用 Unicode NFC、去首尾空白、移除一个前导 `#` 后的精确值；默认区分大小写。
- 归一化视图只用于读取、筛选和展示，不自动写回 Markdown。
- 现有游戏不自动把 `genre` 字段改名。
- 当前数据表明游戏主要使用 `genre`，其它类型使用 `tags`；不能未经确认就把两者视为同一语义并自动双写。
- 2.3 只提供归一化读取、筛选和展示。标签编辑不纳入本阶段必做范围。
- 新建游戏继续写兼容字段 `genre`；新建其它类型继续写 `tags`。
- 如果未来要加入统一标签编辑，必须先单独冻结“题材”和“自由标签”的语义、差量格式及写回目标，不能直接重写两个列表。
- 不清理未知标签字段。

### 3.4 评分兼容

- `score` 保留原量纲。
- `myScore` 只表示个人评分。
- 专用字段只在来源明确时使用。
- `normScore` 为 API/前端运行时字段，不写 Markdown。
- `scoreStar` 是旧显示字段，不是权威数据。
- 只读代码确认当前新建流程使用以下显示逻辑：
  - 非游戏：`clamp(round(score / 2), 1, 5)`。
  - 游戏且 `score > 10`：`clamp(round(score / 20), 1, 5)`。
  - 游戏且 `score <= 10`：按非游戏规则。
  - Python `round` 的整数舍入行为必须原样保留。
- 但当前 `Library.update()` 修改已有 `score` 时不会更新 `scoreStar`，而模板和 Dataview 仍读取该字段。
- 2.1 只冻结“保留历史值、不得在迁移中重算”。2.3 是否在普通评分写入时同步 `scoreStar`，必须在真实模板和 Dataview 回归测试后单独决定，不能凭经验新增算法。
- 修改 `myScore`、`imdbScore`、`bangumiScore` 不得直接修改 `scoreStar`。
- 新 UI 不以 `scoreStar` 作为权威评分。

评分范围：

| 字段 | 类型 | 范围 |
|---|---|---|
| score | Number | 保留历史量纲 |
| myScore | Decimal | 0–10，步长 0.5 |
| doubanScore | Decimal | 0–10，一位小数 |
| metacriticScore | Integer | 0–100 |
| imdbScore | Decimal | 0–10 |
| bangumiScore | Decimal | 0–10 |
| normScore | Decimal/null | 0–10 |

### 3.5 状态兼容

- `progressStatus` 为语义状态。
- `status` 为旧客户端完成兼容位。
- 枚举和 Callout 文本必须严格按《进度状态与正文 Callout 完整映射表》。
- 旧 `status: false` 不推断进行中状态。
- 未知 `progressStatus` 保留原文，不覆盖。
- 不因状态变为 completed 自动填写 `finishTime`。

## 4. 评分证据与迁移策略

### 4.1 证据等级

- L1：条目、数值与来源三者可唯一对应。
- L2：存在平台身份或间接特征，但没有数值来源证据。
- L3：裸评分或来源不明。

### 4.2 迁移动作拆分

每个文件允许以下独立操作组：

1. `BASE_SCHEMA`
   - 添加 `schemaVersion`。
   - 添加 `mediaId`。
   - 只在状态字段不存在冲突时添加或保留合法状态。
2. `CONFLICTING_LEGACY_STATUS`
   - 仅处理两个状态字段都有值但语义冲突的文件。
   - 默认不选择任一方向；用户明确选择后才允许同步。
3. `CALLOUT_STATUS`
   - 只有唯一匹配时更新正文状态文本。
4. `SCORE_PROMOTION`
   - 仅 L1 证据写入单来源评分。
5. `SCORE_REVIEW`
   - L2/L3 保留旧 score，只生成人工审核项。

允许 L2/L3 文件完成 `BASE_SCHEMA`，不得把“评分待确认”误当作基础 schema 迁移失败。

### 4.2.1 BASE_SCHEMA 状态表

| 文件状态 | BASE_SCHEMA 行为 |
|---|---|
| `status` 和 `progressStatus` 都不存在 | 不生成状态 |
| 只有严格合法 `status` | 按类型生成 `progressStatus` |
| 只有合法 `progressStatus` | 保留，不修改 |
| 两者都存在且语义一致 | 保留 |
| 两者都存在且语义冲突 | 不修改任一状态，生成 `CONFLICTING_LEGACY_STATUS` 审核项 |
| `progressStatus` 未知 | 保留未知值，不回退、不覆盖 |
| `status` 非严格布尔 | 保留原文，不映射、不覆盖 |

`CONFLICTING_LEGACY_STATUS` 永远不会在用户选择方向前自动执行。

### 4.3 禁止推断

不得仅凭以下信息生成 `doubanScore` 或 `metacriticScore`：

- 媒体类型。
- score 数值大小。
- 游戏/电影分类。
- `id`、`doubanId`、`steamAppId`。
- 普通链接或日期。
- 标题别名。
- 脚本中存在某个平台 API。

### 4.4 mediaId 与未来 Schema 的操作组限制

| 异常 | BASE_SCHEMA | SCORE_PROMOTION | CALLOUT_STATUS |
|---|---|---|---|
| 重复 mediaId | 不处理 ID；整篇跳过迁移写入 | 跳过 | 跳过 |
| 非法或非 v4 mediaId | 不覆盖；整篇跳过 | 跳过 | 跳过 |
| schemaVersion > 2 | 整篇只读 | 跳过 | 跳过 |
| 受限 YAML 无法安全解析 | 跳过 | 跳过 | 默认跳过 |
| 普通评分来源不明 | 可执行 | 不执行 | 可独立执行 |
| Callout 多个匹配 | 可执行 | 可独立执行 | 不执行 |

未来版本文件不得因为“只不修改 schemaVersion”而被修改其它字段。

### 4.5 music 处理

第二阶段采用“旧功能保留、新模型暂不扩展”：

- 保留旧 `type: music` 的读取、搜索、创建和旧字段编辑能力。
- 不自动为 music 添加 `progressStatus`、专用评分或 `normScore` 规则。
- music 在 2.3 继续使用旧 `status` 二元状态，或明确显示“尚未纳入新版状态模型”。
- music 不进入自动迁移；如未来实际出现，只做报告。
- 不删除现有 music 创建入口。

如用户以后要求完整支持 music，必须单独冻结评分、状态、Callout 和 UI，不随 2.3 顺带扩展。

## 5. 后续实施顺序

### 5.1 阶段 2.1：最终规范验收

交付：

- 本任务书。
- 五份配套规范。
- 代码和真实数据只读审查。
- 待确认事项。

门禁：

- 用户确认字段、状态、评分证据、迁移操作组和回滚边界。
- 未确认不得写代码。

### 5.2 阶段 2.2：受限 Frontmatter Patch 引擎

目标：

- 新建独立模块，不复用当前 `parse_fm()` 做完整解析。
- 返回字段原始行范围。
- 支持 `REMOVE_FIELD`。
- 支持新增、更新和显式删除。
- 检测重复 Key、注释、块标量和简单列表。
- 无法识别时返回结构化问题，不抛“尽力修复”。

不包含：

- 迁移 CLI。
- 批量扫描。
- UI 修改。
- 真实 Vault 写入。

验收：

- 目标字段之外字节完全一致。
- 正文、BOM、CRLF/LF、末尾换行完全一致。
- 重复 Key、不支持语法、非法 UTF-8 不写入。
- 复杂夹层 YAML 用例有结果。
- 与阶段 1 的 revision 和原子写入兼容。

### 5.3 阶段 2.3：后端规范化与前端兼容

目标：

- 中心化归一模型。
- 旧/新评分字段兼容。
- `normScore`。
- `progressStatus`。
- 严格 `status` 校验。
- 标签 `tags`/`genre` 合并。
- `scoreStar` 兼容读取；是否在普通写入后重算需通过模板/Dataview 回归后决定。
- 字段级 `dirtyFields`。
- 409 合并。
- 状态胶囊 UI。
- 分来源评分 UI。

关键要求：

- API URL 不改变。
- 原有字段继续返回。
- 旧客户端可只提交 `status`/`score`。
- 同字段冲突不自动选边。
- 备注草稿与其它字段草稿都保留。
- 保存失败不得显示成功。

### 5.4 阶段 2.4：Migration Engine

目标：

- 独立本地 CLI。
- Scan。
- Dry-run。
- Plan。
- Apply。
- Post-audit。
- Rollback 设计实现。

硬性限制：

- Web API 只读。
- 每个 Plan 绑定 Vault 绝对路径和 schemaTarget。
- Dry-run 生成 UUID。
- 原文件、临时文件、最终文件三阶段校验。
- 每个操作组可单独选择。
- 评分待确认不阻止基础迁移，但不得自动写专用评分。
- Apply 前后均记录 journal。
- 快照失败禁止 Apply。
- 所有能够检测到的外部修改必须识别并跳过，禁止用旧计划覆盖已变化文件。
- 最终替换前再次检查源哈希，并通过互斥锁和暂停 Obsidian 编辑缩小竞争窗口。
- `os.replace()` 只保证原子替换，不提供“哈希匹配才替换”的比较交换语义；对不遵守锁协议的外部编辑器，不宣称绝对无竞争。
- 无法确认当前状态时放弃写入。

Dry-run 安全边界：

1. 只读取媒体文件。
2. 不修改 Markdown 内容。
3. 不修改 Markdown mtime。
4. 不修改附件。
5. 不重命名文件或改变目录结构。
6. 不执行 Patch 或快照恢复。
7. 只在 `.media-app/migration/` 写计划、报告等应用元数据。
8. 计划生成失败不得留下可被误认为有效的完整 Plan。

### 5.5 阶段 2.5：Vault 副本验收

只在真实 Vault 的完整副本执行：

- Scan。
- Dry-run。
- Apply。
- Obsidian 打开。
- Dataview 检查。
- 旧 API/新 API 检查。
- 状态、评分、标签和 Callout 检查。
- 快照恢复。
- 单篇/全局回滚。
- 外部编辑冲突。
- 文件哈希审计。

正式 Vault 在人工验收通过前保持只读。

### 5.6 每阶段统一交付物与退出条件

2.2–2.5 每次阶段交付必须包含：

- 修改文件清单。
- 新增/修改函数或模块说明。
- 旧行为和新行为对照。
- 测试总数、通过数、失败数、跳过数。
- 未覆盖用例及原因。
- 数据兼容性说明。
- 已知问题和人工待确认项。
- 完整 Git 提交号。
- 可复现的测试命令。

2.2 额外要求：

- 每个 YAML 样例同时提供原始文件与 Patch 后文件。
- 证明未修改字段、正文、BOM、行尾完全字节一致。
- 不得只用“单元测试全过”代替字节对照证据。

## 6. Migration Engine 设计决策

### 6.1 数据目录

- 正式迁移元数据归属 Vault 的 `.media-app/migration/`。
- 迁移快照归属 `.media-app/backups/migrations/<planId>/`。
- 临时版测试允许显式指定独立 data root，禁止混入正式迁移记录。
- 不得依赖程序启动目录猜测数据目录。

### 6.2 快照

- 迁移专用快照不复用当前“保留最近 10 份”自动清理逻辑。
- 只有用户或明确保留策略可以清理迁移快照。
- manifest 必须记录：
  - planId
  - relativePath
  - bytes
  - SHA-256
  - createdAt
  - source app version
- 每个快照写入后重新读取校验。
- 任一失败禁止 Apply。

### 6.3 原子写入和回滚

- 每次写入前检查当前 SHA-256。
- 临时文件必须同目录。
- fsync 后回读验证。
- 最终替换前再次检查源哈希。
- 替换后验证最终哈希和 YAML。
- 验证失败时仅当目标仍等于本次写入内容才回滚。
- 如果目标已被外部修改，保留外部内容并标记 `RESTORE_CONFLICT`。
- 最后一次源哈希检查与 `os.replace()` 之间仍存在无法彻底消除的 TOCTOU 窗口。
- 因此不得承诺对外部编辑器绝对互斥；只能保证检测到的变化被拒绝，无法确认时放弃写入，并要求 Apply 期间暂停 Obsidian 编辑。

### 6.4 Plan

Plan 必须包含：

- `planFormatVersion`
- `planId`
- `planState`：`DRAFT`、`FINALIZED`、`APPLYING`、`APPLIED`、`ROLLED_BACK`
- `createdAt`
- `appVersion`
- `schemaTarget`
- `vaultRoot`
- `files[]`
- `planChecksum`
- `policy`
- `counters`

每个文件计划必须有：

- `relativePath`
- `originalSha256`
- `originalSize`
- `proposedSha256`
- `proposedMediaId`
- `operationGroups`
- `proposedChanges`
- `scoreEvidence`
- `warnings`
- `migrationStatus`
- `skippedReason`

每个 OperationGroup 必须记录：

- `groupType`
- `selected`
- `planStatus`：`PLANNED`、`REVIEW_REQUIRED`、`EXCLUDED`
- `operationIds`
- `reason`
- 执行时新增 `runStatus`：`NOT_STARTED`、`APPLIED`、`SKIPPED`、`CONFLICT`、`ERROR`、`RESTORED`
- `attemptedAt`
- `resultDetail`

执行统计按组分别汇总，不把评分待审误计为基础迁移失败。

同一 Plan 的幂等规则：

- Plan 在正式启用前计算不可变 `planChecksum`。
- 操作组选择必须在 `FINALIZED` 前完成；直接手工修改已签名 Plan 会使 checksum 失效。
- Apply 只接受 `FINALIZED` Plan。
- 同一 `planId` 完整 Apply 后禁止再次 Apply。
- 已标记 `APPLIED` 的 OperationGroup 不得重复执行。
- 进程崩溃后，仅允许显式 resume 同一 `APPLYING` Plan，并跳过 journal 中已成功的组。
- 任何恢复写入都必须生成独立审计记录。

### 6.5 CLI 权限

- Apply/Rollback 仅 CLI。
- Plan 必须唯一。
- 同一 planId 不得重复 Apply。
- 需要 `APPLY <planId>` 或 `ROLLBACK <planId>` 明确确认。
- Web API 只能 Scan/Preview/Report 只读。

## 7. 正文 Callout 实施决策

- 当前支持范围限于明确的行首 Callout 状态标记。
- 初始安全标记：
  - `> **📝 状态**：`
  - `> **🕹️ 状态**：`
- 只读样本已确认四类媒体都有状态 Callout，但标签形式目前只识别上述两种 Emoji 标记，不是四种标记。
- 冒号后允许零个或多个普通空格；空格不属于替换值。
- 冒号后只接受已知状态显示值；带额外说明、标签或注释时不自动修改。
- CRLF 和 LF 均支持，但更新后保持原换行。
- 同一文件中两个状态标记视为歧义，不选择第一个或最后一个。
- Callout 内存在其它自定义字段时，只处理状态行，其它行不动。
- 每次修改前扫描正文，要求恰好一个匹配。
- 只替换冒号后的值。
- 0 个或多个匹配：正文不变，Frontmatter 可保存，报告 `CALLOUT_NOT_UPDATED`。
- 代码块、引用嵌套和自定义 Callout 默认不修改。
- 状态胶囊保存后，先完成 Frontmatter，再尝试独立 Callout Patch；正文失败不得让 Frontmatter 状态回退。
- 如果用户要求“二者全成功”，则 API 可返回部分成功状态，但 UI 必须明确显示“字段已保存，正文未同步”。

## 8. 409 与草稿合并

必须维护：

```text
baseItem       开始编辑时的原始基准
localDraft     用户当前本地值
latestItem     409 返回的服务端最新值
dirtyFields: Set<string>
```

三方比较规则：

| 情况 | 结果 |
|---|---|
| 本地字段未修改 | 采用 latestItem |
| 本地修改，远端等于 base | 保留 localDraft |
| 远端修改，本地等于 base | 采用 latestItem |
| 本地和远端都修改且结果相同 | 视为一致 |
| 本地和远端都修改且结果不同 | 字段冲突 |
| 值无法可靠比较 | 字段冲突 |

可编辑字段按统一注册表声明。阶段 2.3 必做范围：

- comment
- score（仅旧客户端兼容）
- myScore
- doubanScore
- metacriticScore
- imdbScore
- bangumiScore
- status
- progressStatus
- finishTime
- startDate
- platform
- purchaseDate
- price

兼容组必须作为原子冲突单元：

- status group：`status`、`progressStatus`
- primary rating group：旧 `score` 与当前类型主渠道专用评分
- user rating：`myScore` 独立
- tags group：`tags`、`genre`，在当前阶段只读归一化，不提供写入

当前 UI 不提供编辑的字段必须在字段注册表中明确标为只读，包括但不限于 title、originalTitle、aliases、year、type、image、director、author、publisher、country、desc、pages、isbn、series、translator。title 和 image 仍由旧 API 兼容写入，必须继续接受 revision 校验，但不进入新版前端 dirtyFields。若未来 UI 开放这些字段，必须先加入同一草稿保护模型。

冲突流程：

1. 收到 409。
2. 保存本地 dirtyFields 的值。
3. 用 latest 更新基准。
4. 对比本地基准与远端同字段。
5. 无冲突字段合并。
6. 有冲突字段标记。
7. 用户解决后提交最小字段集。
8. 备注输入框内容和光标不被刷新覆盖。
9. 仅 revision 更新不算完成。

## 9. 阶段 2.1 文档关系

本文件是控制任务书，以下文档作为详细规范：

- `02-data-dictionary-and-yaml-patch-spec.md`
- `03-progress-status-and-callout-map.md`
- `04-score-evidence-and-migration-plan-spec.md`
- `05-migration-engine-transaction-and-rollback-spec.md`

这些文件必须在本任务书批准后同步修正冲突，不能反转本文件的安全规则。

## 10. 后续修改原则

- 先测试、后批量修改。
- 小范围、按模块改造。
- 不引入框架或数据库。
- 不重写现有正常功能。
- 所有兼容映射集中在一个模块。
- 每个写入操作先备份或快照。
- 所有字节保护测试先于 UI 迁移。
- 所有批量迁移先在真实 Vault 副本进行。
- 每次提交都必须包含修改清单、测试报告和兼容性说明。
- 未获得阶段验收前不得自动开启下一阶段。

## 11. 当前待确认

| 项目 | 当前结论 | 下一步 |
|---|---|---|
| 阶段 1 验收 | 代码存在候选实现，文档不替代人工验收 | 先完成正式验收或明确风险接受 |
| `scoreStar` | 当前模板依赖；创建公式已确认，更新语义未统一 | 迁移不重算；2.3 回归后再决定是否写入 |
| 历史评分来源 | 缺少逐条来源记录 | 默认 L2/L3 |
| 快照清理 | 当前函数自动保留近期快照 | 迁移专用快照不可直接复用 |

## 11.1 对复审查意见的逐条裁定

| 审查项 | 裁定 | 正式处理 |
|---|---|---|
| BASE_SCHEMA 与状态冲突边界 | 正确，必须修 | 状态冲突不修改，独立成人工审核组 |
| 409 三方比较与 dirtyFields | 正确，必须修 | base/local/latest 三方合并；建立可编辑字段注册表 |
| SHA-256 与原子替换边界 | 正确，必须修 | 删除“绝对覆盖”承诺，明确无法完全消除 TOCTOU |
| Dry-run 不修改内容/mtime | 正确，必须补 | 冻结完整 Dry-run 安全边界 |
| tags/genre 双写风险 | 问题判断正确，建议方案不完全正确 | 暂不做自动双写；只做只读归一化 |
| scoreStar 算法必须核实 | 正确 | 已核实创建公式，但更新语义未统一；迁移不重算 |
| Callout 标记数量描述 | 正确，必须修 | 四类媒体有样本，实际识别两种 Emoji 标记 |
| music 处理范围 | 正确 | 采用 legacy-only：保留旧功能，不扩展新模型 |
| mediaId 异常与操作组关系 | 正确，必须修 | 非法/重复 ID 和未来 Schema 整篇跳过 |
| 操作组级状态和幂等 | 正确，必须修 | 每组记录 selected/status/runStatus，同 Plan 不重复执行 |
| 核心字段必填性 | 正确，必须修 | 区分身份必填、业务必需、可选、计算和旧兼容 |
| 阶段退出条件 | 正确，必须修 | 每阶段统一交付修改清单、测试计数、兼容说明和提交号 |

## 12. 立即停止条件

在本任务书获得用户明确确认前：

- 不修改源码。
- 不创建 Patch 引擎。
- 不创建 Migration Engine。
- 不运行 Scan/Dry-run/Apply/Rollback。
- 不修改真实 Vault。
- 不部署。
- 不进入 2.2。
