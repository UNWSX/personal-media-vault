# 阶段 2.3 数据与 API 兼容说明

## 1. API 兼容矩阵

| API | 原有行为 | 2.3 兼容结果 |
|---|---|---|
| `/api/library` | 返回媒体条目和 revision | 原字段继续返回，增加兼容字段 |
| `/api/search` | 搜索元数据 | URL 和参数不变 |
| `/api/import` | 导入并创建笔记 | 新笔记同时写新旧兼容字段 |
| `/api/create` | 手动创建 | 增加身份、进度和来源评分 |
| `/api/item` | 更新单字段 | 旧 `score`/`status` 仍可用 |
| `/api/delete` | 可恢复删除 | 不变 |
| `/api/fix-image` | 修复封面 | 使用同一受限 Patch 与身份只读规则 |
| `/api/diag` | 上游诊断 | 不变 |

## 2. `/api/library` 增加字段

原有字段继续保留：

- `path`
- `title`
- `type`
- `score`
- `myScore`
- `status`
- `year`
- `image`
- `comment`
- `finishTime`
- `purchaseDate`
- `price`
- `platform`
- `revision`

新增只读或展示字段：

- `progressStatus`
- `statusGroup`
- `statusValid`
- `progressStatusValid`
- `progressStatusSource`
- `warnings`
- `doubanScore`
- `metacriticScore`
- `imdbScore`
- `bangumiScore`
- `normScore`
- `scoreSource`
- `ratingWarnings`
- `schemaVersion`
- `schemaVersionValid`
- `schemaFuture`
- `mediaId`
- `mediaIdValid`
- `mediaIdDuplicate`
- `identityWarnings`
- `writeAllowed`
- `scoreStar`
- `originalTitle`
- `aliases`
- `startDate`

## 3. `/api/item` 输入兼容

旧客户端可继续发送：

```json
{
  "path": "02-电影阅读/movie/示例.md",
  "revision": "sha256:...",
  "score": 8.7,
  "status": true
}
```

新客户端可发送：

```json
{
  "path": "02-电影阅读/movie/示例.md",
  "revision": "sha256:...",
  "doubanScore": 8.7,
  "progressStatus": "completed"
}
```

兼容同步：

- `doubanScore` -> 旧 `score`
- `metacriticScore` -> 旧 `score`
- `progressStatus` -> 旧 `status`
- 旧 `status` -> 类型相关 `progressStatus`

若同一请求中的 `status` 与 `progressStatus` 语义冲突，返回 400，两个字段都不写。

## 4. 只读条件

以下笔记拒绝普通字段更新和封面修复：

- `schemaVersion > 2`
- `schemaVersion` 不是正整数
- `mediaId` 存在但不是 UUIDv4
- `mediaId` 与另一篇笔记重复

缺少身份字段的旧笔记仍可正常读写，不以缺少身份为由阻断。

## 5. 旧客户端兼容

- `status` 继续表示“已完成”，不是“正在进行”。
- 旧 `score` 暂时保留。
- 专用评分写入时同步 `score`。
- 无法确定来源的历史 `score` 不自动提升到专用评分。
- `music` 继续使用旧二元 `status`，编辑器显示旧版兼容模式。

## 6. 前端兼容

- 旧条目没有 `progressStatus` 时，由后端映射后再筛选。
- 旧评分来源未知时明确显示“旧版评分（来源未确认）”。
- 不同来源的评分不混为同一量纲。
- 未设置或未知历史状态不自动推断为 planned/backlog。

