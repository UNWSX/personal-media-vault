# 阶段 2.3：后端规范化与前端兼容

状态：实现完成，等待阶段验收  
阶段范围：新旧数据读取兼容、普通编辑写入、字段级冲突处理和 UI 接入  
未包含：阶段 2.4 Migration Engine、Dry-run、Apply、真实 Vault 迁移

## 1. 阶段目标

本阶段把新旧数据差异集中到兼容层，保证旧 Markdown 无需批量改写即可读取，并让后续 Migration Engine 使用同一套语义。

核心原则：

- Markdown 仍是唯一正式数据源。
- 旧字段继续保留，API 原有 URL 和主要返回字段不变。
- 普通编辑只写用户实际修改的字段。
- 旧笔记不会因为打开、刷新或版本升级而自动迁移。
- 不同来源的评分保持各自量纲。
- 外部修改冲突无法判定的字段不自动选边。

## 2. 后端兼容模型

新增 `app/media_compat.py`，集中负责：

- `status` 严格布尔读取。
- 旧 `status` 到类型相关 `progressStatus` 的映射。
- `progressStatus` 合法枚举与四类聚合状态。
- 旧 `score` 与 `myScore`、`doubanScore`、`metacriticScore`、`imdbScore`、`bangumiScore`。
- 运行时 `normScore` 与 `scoreSource`。
- `schemaVersion`、UUIDv4 `mediaId` 与只读判定。
- `tags`/`genre` 只读合并去重。
- 普通 API 写入的字段校验和兼容同步。

所有读取映射和写入转换都集中在兼容层，没有散落在多个 API 分支。

## 3. 数据结构

### 新笔记

新建媒体笔记时增加：

```yaml
schemaVersion: 2
mediaId: "UUIDv4"
progressStatus: planned
```

按实际情况写入：

```yaml
doubanScore: 8.7
metacriticScore: 91
imdbScore: 8.2
bangumiScore: 8.4
startDate: 2026-11-07
```

这些字段均可选，除新建时的身份字段和可确定的初始状态外，不会强制补齐。

### 旧笔记

旧笔记缺少 `schemaVersion`、`mediaId`、`progressStatus` 或专用评分时，读取层即时兼容：

- `status: true` -> `completed`
- `status: false` -> `planned`
- 游戏 `status: false` -> `backlog`
- 合法显式 `progressStatus` 优先于旧 `status`
- 未知历史状态保留原文并标记，不自动纠正
- 游戏旧 `score` 按记录的实际量纲计算 `normScore`，但不推断为 Metacritic

缺失字段不会因读取被写回 Markdown。

## 4. 评分规则

| 字段 | 量纲 | 写入校验 |
|---|---:|---|
| `myScore` | 0–10 | 0.5 步长 |
| `doubanScore` | 0–10 | 最多 1 位小数 |
| `metacriticScore` | 0–100 | 整数 |
| `imdbScore` | 0–10 | 最多 1 位小数 |
| `bangumiScore` | 0–10 | 最多 1 位小数 |
| `score` | 旧值 | 保持旧 API 兼容 |

修改当前媒体类型的主渠道评分时，旧 `score` 同步为相同值：

- 非游戏：`doubanScore` 同步 `score`
- 游戏：`metacriticScore` 同步 `score`

修改 `myScore`、`imdbScore` 或 `bangumiScore` 不会修改旧 `score`。`normScore` 只在运行时计算，不写入 Markdown。

## 5. 进度状态

| 类型 | 合法 `progressStatus` |
|---|---|
| movie | `planned`, `completed` |
| teleplay | `planned`, `watching`, `paused`, `completed` |
| game | `backlog`, `playing`, `paused`, `completed`, `abandoned` |
| book | `planned`, `reading`, `paused`, `completed` |

筛选聚合：

- 未开始：`planned`, `backlog`
- 进行中：`watching`, `playing`, `reading`, `paused`, `abandoned`
- 已完成：`completed`

后端的详细页和 Callout 仍显示具体状态，例如“已弃坑”不会只显示为“进行中”。

## 6. 字段级冲突

前端为每个条目维护：

```text
baseItem
localDraft
latestItem
```

409 后按以下规则处理：

| 本地与远端变化 | 结果 |
|---|---|
| 本地未改 | 接受远端 |
| 远端未改 | 保留本地并重试 |
| 两边都改且相同 | 视为一致 |
| 两边都改且不同 | 显示字段冲突，等待用户选择 |

原子兼容组：

- 状态组：`status`、`progressStatus`
- 主评分组：`score` 与当前类型主渠道评分

用户未解决字段冲突前，关闭整个桌面窗口会被阻止。重试只提交冲突中的最小字段集。

## 7. 正文 Callout

状态同步只处理以下已确认模板：

```markdown
> **📝 状态**：未看
> **🕹️ 状态**：待玩
```

安全边界：

- 仅修改正文中代码块之外的匹配行。
- 同一正文必须有且只有一个匹配。
- Callout 图标必须与媒体类型一致。
- 当前值必须能唯一识别。
- 带括号说明、额外标签或未知文本时不修改。
- LF、CRLF 和文件末尾是否有换行保持原样。
- Frontmatter 已成功写入而 Callout 未同步时，API 返回 `calloutWarning`，前端显示“字段已保存，但 Callout 未同步”。

## 8. 身份与只读规则

- `mediaId` 接受大小写不同的 UUIDv4 字符串，重复检测不区分大小写。
- 非法 UUID、非法 `schemaVersion`、未来 schema、重复 `mediaId` 的笔记保持只读。
- 只读提示显示在详情页。
- 本阶段不自动修复、替换或迁移这些身份字段。

## 9. 当前未执行事项

- 没有向真实 Vault 写入任何 Markdown。
- 没有运行 Scan、Dry-run、Apply 或 Rollback。
- 没有批量添加 `schemaVersion` 或 `mediaId`。
- 没有批量提升旧评分到专用字段。
- 没有部署或覆盖现有安装版本。

