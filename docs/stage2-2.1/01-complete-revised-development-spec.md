# 个人影音库 2.0 第二阶段完整修订版开发任务书

文档状态：2.1 冻结候选

冻结范围：数据模型、兼容规则、迁移原则与验收标准

当前授权：仅允许技术文档修订

源码基线：`703e372`（只读核对，不代表阶段 1 已获最终验收）

## 1. 文档效力与冲突处理

本文件综合较新的《第二阶段修订任务书》和较早的《第二阶段修订版》要求。两者冲突时遵循以下顺序：

1. Markdown 是唯一正式数据源。
2. 不得引入第三方运行依赖。
3. 不得执行未经单独授权的代码修改或数据迁移。
4. 安全、可追溯和可恢复优先于自动化完成率。
5. 较新任务书中明确的安全规则优先。

本文件已修正的主要冲突：

- 原稿曾考虑 PyYAML/ruamel.yaml；冻结方案改为 Python 标准库实现的受限 YAML 子集。
- 原稿要求 4 份文档；较新任务书要求 5 份完整文档，本交付采用 5 份。
- 原稿把 `score > 10`、游戏类型、`steamAppId`、`doubanId` 直接视为确定评分来源；冻结方案只将其视为候选证据，不能自动迁移评分。
- 原稿写“一键全局恢复”；冻结方案要求本地 CLI、指定 `planId`、逐篇校验、人工确认，遇到用户后续编辑必须标记 `RESTORE_CONFLICT`。
- 原稿允许在 409 后只更新 revision 再保存；冻结方案要求字段级草稿保护，不能以旧 Item 整体覆盖新 Item。
- 原稿的状态表与 Callout 表存在措辞差异；本交付以本文档及配套状态规范中的唯一枚举表为准。

## 2. 系统边界与不可修改约束

- 正式数据仅存于 Obsidian Vault 的 Markdown Frontmatter 和正文。
- 不引入 SQLite、MySQL、MongoDB、云端数据库或独立正式索引。
- 不改变 Vault 根目录、`02-电影阅读/`、`attachments/` 及现有类型子目录。
- 不批量重命名 Markdown，不修改现有 Obsidian 双链，不清理未知文件。
- 后端仍为 Python 标准库 HTTP 服务；前端仍为 HTML/CSS/原生 JavaScript；桌面壳仍为 C# WinForms + WebView2。
- 不引入 PyYAML、ruamel.yaml 或其他第三方 YAML 解析库。
- 缓存、计划、报告、备份、日志、快照和迁移记录只能作为辅助数据。
- `schemaVersion`、`mediaId`、专用评分和 `progressStatus` 必须能直接由 Obsidian 和 Dataview 读取。

## 3. 当前阶段与阶段门禁

| 阶段 | 工作内容 | 当前权限 | 进入条件 |
|---|---|---|---|
| 2.1 | 数据模型与规则冻结 | 允许，仅文档 | 当前任务 |
| 2.2 | 受限 YAML Patch 引擎 | 未授权 | 2.1 人工验收通过并另行授权 |
| 2.3 | 后端兼容层与前端 UI | 未授权 | 2.2 字节级保护测试通过 |
| 2.4 | Migration Engine | 未授权 | 2.3 联调验收通过 |
| 2.5 | 真实 Vault 副本全量验收 | 未授权 | 2.4 实现和隔离测试通过 |

2.1 完成后必须停止。不得因文档交付完成而自动进入 2.2。

## 4. 唯一数据模型摘要

### 4.1 身份与版本

新创建或迁移后的媒体笔记必须包含：

```yaml
schemaVersion: 2
mediaId: "c3b9e4a8-7d21-4f8a-9e1b-3f2d1e0a9b8c"
```

- `schemaVersion` 为整数，当前只接受目标值 `2`。
- `mediaId` 为小写标准 UUIDv4 字符串。
- `mediaId` 创建后永久不变。
- 已有合法 UUIDv4 原样保留。
- 已有非法值或非 v4 UUID 不改写，转人工处理。
- 重复 `mediaId` 全部标记严重异常，不自动合并、不重新分配。
- `schemaVersion > 2` 视为未来版本，所有写入禁用。
- 旧笔记缺少这两个字段仍应可读；迁移时由独立 Migration Engine 添加。

### 4.2 评分

持久化评分字段：

| 字段 | 范围 | 来源 |
|---|---|---|
| `score` | 原量纲 | 历史兼容字段 |
| `myScore` | 0–10，步长 0.5 | 用户个人评分 |
| `doubanScore` | 0–10，一位小数 | 豆瓣 |
| `metacriticScore` | 0–100 整数 | Metacritic |
| `imdbScore` | 0–10 | IMDb |
| `bangumiScore` | 0–10 | Bangumi |

运行时字段：

| 字段 | 范围 | 是否落盘 |
|---|---|---|
| `normScore` | 0–10 或 null | 否 |

规则：

- 旧 `score` 保持原量纲，不在迁移中统一转换。
- `myScore` 与所有外部评分独立。
- 只有 L1 确定来源证据才允许从旧 `score` 生成专用评分。
- `steamAppId`、豆瓣 ID、媒体类型、普通链接、`score > 10` 都不能单独证明评分数值来源。
- 无合法评分时 `normScore = null`，不得默认为 0。

### 4.3 进度状态

新增：

```yaml
progressStatus: planned
```

合法枚举按媒体类型限制：

- movie：`planned`、`completed`
- teleplay：`planned`、`watching`、`paused`、`completed`
- game：`backlog`、`playing`、`paused`、`completed`、`abandoned`
- book：`planned`、`reading`、`paused`、`completed`

旧 `status` 保留：

- `status: true` 映射 `completed`。
- 电影、剧集、图书的 `status: false` 映射 `planned`。
- 游戏 `status: false` 映射 `backlog`。
- 不得从 `false` 推断 `watching`、`playing`、`reading` 或 `paused`。
- `progressStatus` 写为 `completed` 时 `status` 必须同步 `true`；其他合法状态同步 `false`。

## 5. 兼容层要求

### 5.1 API

保留所有阶段 1 URL 和原有语义：

```text
/api/library
/api/item
/api/create
/api/delete
/api/search
/api/fix-image
/api/diag
/api/import
/api/backup
/media
/api/ping
```

旧字段继续返回。新增字段只能追加，不能删除旧字段。API 必须同时提供原始评分和运行时 `normScore`。

### 5.2 写入优先级

状态：

- 只提交合法 `progressStatus`：计算并同步 `status`。
- 只提交严格布尔 `status`：按类型映射并同步 `progressStatus`。
- 同时提交且语义一致：接受。
- 同时提交且语义冲突：拒绝，返回明确错误。

评分：

- 修改 `myScore` 不改变任何外部评分或旧 `score`。
- 修改影视或图书的 `doubanScore` 时，同步旧 `score` 为该 0–10 值。
- 修改游戏的 `metacriticScore` 时，同步旧 `score` 为该 0–100 整数。
- 修改 IMDb 或 Bangumi 评分不改变旧 `score`。
- 旧客户端只提交 `score` 时，保留原值，不猜测来源。

### 5.3 409 草案保护

遇到 409 时：

1. 接收服务端 `latest`。
2. 更新本地基准和新 revision。
3. 保留所有 `dirtyFields`。
4. 比较同字段远端值与本地基准。
5. 无字段冲突时，将本地草稿合并到最新基准。
6. 同字段冲突时显示冲突，等待用户选择。
7. 只提交用户实际修改的字段。
8. 备注草稿独立保留，不能被远端旧备注覆盖。

仅更新 revision 不等于冲突合并完成。

## 6. 受限 YAML 与局部 Patch

解析器支持：

- 顶层单行键值。
- 单/双引号字符串。
- 整数、十进制数、严格小写布尔、null。
- `|`、`|-` 块标量。
- 简单缩进字符串列表。
- 简单字符串内联数组。
- 整行注释和安全识别的行内注释。

明确不支持：

- 锚点和别名。
- 多级映射或复杂对象。
- 自定义标签。
- 复杂跨行结构。
- 无法证明字段边界的结构。

写入规则：

- 只替换目标字段行范围。
- 不在目标 Key 不存在时覆盖同名缩进行。
- 新字段插入 Frontmatter 结束 `---` 前。
- 不重序列化整个 Frontmatter。
- 保留注释、空行、未知字段、双链、正文、BOM 和换行风格。
- 重复顶层 Key 必须整篇跳过。
- 不支持语法默认整篇跳过；只有能够证明目标字段边界完全独立时才允许局部写入。
- 删除字段只接受显式 `REMOVE_FIELD` 哨兵。

## 7. 迁移引擎边界

迁移流程固定为：

```text
Scan → Dry-run → 人工核查 → Apply → Post-audit
```

- Web API 只能只读 Scan/Preview/Report 查询。
- Plan 文件和正式 Apply 只能由本地 CLI 生成或执行。
- Apply 前必须创建并校验快照。
- Plan 中预分配 UUID，Apply 不得重新生成。
- 每个文件在 Plan 中记录原始 SHA-256、预期后 SHA-256 和变更项。
- Apply 前、临时文件完成后、原子替换前均检查源文件哈希。
- 哈希比较不能消除所有 TOCTOU；必须要求用户暂停 Obsidian 编辑，并在最终替换前再次检查。
- 单篇失败只影响该篇。
- 迁移后用户再次编辑时，Rollback 必须标记 `RESTORE_CONFLICT` 并保留用户当前文件。
- Web API 不得提供批量 Apply 或自动回滚。

## 8. 正文 Callout 边界

- 只更新明确的状态值。
- 当前阶段 1 已观察到的安全标记是 `> **📝 状态**：` 和 `> **🕹️ 状态**：`。
- 同一文件必须恰好存在一个可识别状态标记，才能自动更新。
- 找不到或多个匹配时保留正文并报告。
- 不修改自定义 Callout、其他正文、图片、双链和未知字段。

完整规则见《进度状态与正文 Callout 完整映射表》。

## 9. 阶段内工作与验收

### 2.1 数据模型与规则冻结

验收：

- 五份文档内容一致。
- 所有字段有类型、范围、默认值和兼容规则。
- 来源证据、状态、Callout、Patch、Plan、回滚规则无冲突。
- 所有未确认代码或数据细节进入“待确认事项”。
- 未修改源码和真实 Vault。

### 2.2 YAML Patch 引擎

只有人工书面授权后开始。必须通过字节级保护、重复 Key、复杂语法、编码、换行和 BOM 测试。

### 2.3 后端兼容层与 UI

只有 2.2 验收通过后开始。必须保留旧 API、显示评分来源、支持状态胶囊和字段级 409 草稿保护。

### 2.4 Migration Engine

只有 2.3 验收通过后开始。必须先实现隔离测试，再生成 Dry-run Plan。

### 2.5 Vault 副本验收

只能在完整副本中进行全量扫描、Dry-run、Apply、Obsidian/Dataview 检查、API 检查、回滚、冲突和哈希审计。正式 Vault 在人工验收前禁止迁移。

## 10. 2.1 阶段测试设计

必须覆盖：

- 全部 YAML 支持/不支持语法。
- 评分字段校验与 `normScore`。
- L1/L2/L3 来源证据。
- 四类媒体全部合法与非法状态。
- Callout 唯一匹配和多重匹配。
- Plan 完整性、路径边界、重复执行。
- Dry-run 不改 mtime。
- Apply 前和替换前外部修改。
- 单篇回滚、全局回滚和用户迁移后编辑冲突。
- 重复 `mediaId`、未来 schemaVersion、非法 UTF-8。

测试必须只使用隔离目录或 Vault 副本。

## 11. 当前源码只读核对结果

在提交 `703e372` 可读取到以下实现事实，仅用于冻结设计，不构成阶段 1 验收声明：

- 媒体根目录为 `02-电影阅读/`。
- `revision` 是笔记原始字节的 SHA-256。
- `/api/item` 支持可选 revision；冲突返回 HTTP 409、`conflict: true` 和 `latest`。
- 原子写入在备份后和替换前再次校验修订号，并采用条件回滚。
- `commentDrafts` 与 `saveQueues` 已存在；关闭检查会读取队列实际返回值。
- `sync_callout()` 当前支持 `📝 状态` 和 `🕹️ 状态` 标记。
- 当前结构化快照函数签名包含 `source_paths`、`label` 和可选 `snapshots_root`。
- 当前是否已有完整字段级 `dirtyFields` 模型仍待确认。
- 当前导入路径存在 Metacritic/豆瓣写入逻辑，但没有逐条历史评分来源审计日志；现有旧数据不能仅凭代码路径判定 L1。

## 12. 待确认事项

| 事项 | 需要检查 | 风险 | 人工决定 |
|---|---|---|---|
| 阶段 1 是否最终验收 | 独立验收记录与最新测试报告 | 影响阶段 2 前置门禁 | 书面确认后再进入 2.2 |
| 完整字段级 dirtyFields | 当前前端 save/409 路径 | 字段冲突可能不完整 | 2.3 前冻结字段集合 |
| Callout 历史变体 | 真实数据中所有状态标记格式 | 正则过宽可能误改正文 | 2.2 前建立样本集 |
| 历史评分来源记录 | 旧导入日志、计划、外部备份 | 无法达到 L1 时只能人工审核 | 确定是否有可追溯记录 |
| 快照函数复用方式 | 最新函数签名和事务边界 | 快照范围或失败语义不符 | 2.4 重新核验后适配 |
| schemaVersion 未来版本 | 现有值分布 | 可能误写新版本文件 | 2.4 必须全部只读跳过 |
| 音乐等未列入类型 | 是否存在历史 `music` 条目 | 状态枚举缺失可能损坏兼容 | 2.3 前决定透明保留或单独冻结 |

## 13. 2.1 停止条件

本轮完成文档后立即停止。禁止：

- 修改任何源码。
- 新建或运行迁移引擎。
- 执行 Scan、Dry-run、Apply 或 Rollback。
- 创建真实迁移快照。
- 修改真实 Vault。
- 部署或覆盖程序。
- 自动进入 2.2。

最终目标不是尽快完成迁移，而是建立不会损坏 Obsidian 数据、不会污染评分量纲、不会静默覆盖并发编辑且可审计恢复的数据规范。
