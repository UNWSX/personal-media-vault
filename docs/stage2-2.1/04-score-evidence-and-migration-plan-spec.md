# 评分来源证据链判定流程与 Migration Plan 数据结构规范

状态：2.1 冻结候选

适用范围：未来 2.4 Migration Engine

关键原则：媒体身份不等于评分数值来源

## 1. 评分模型

| 字段 | 含义 | 范围 | 落盘 |
|---|---|---|---|
| `score` | 历史兼容评分 | 保留原量纲 | 是 |
| `myScore` | 个人评分 | 0–10，步长 0.5 | 是 |
| `doubanScore` | 豆瓣评分 | 0–10，最多一位小数 | 是 |
| `metacriticScore` | Metacritic | 0–100 整数 | 是 |
| `imdbScore` | IMDb | 0–10 | 是 |
| `bangumiScore` | Bangumi | 0–10 | 是 |
| `normScore` | 统一比较评分 | 0–10 或 null | 否 |

`normScore` 不得写入 Markdown。

## 2. 媒体身份与评分来源分离

以下只能证明“条目是什么”或“关联了哪个平台”：

- `type: game`
- `steamAppId`
- `doubanId`
- Steam、豆瓣或其他条目链接
- `datePublished`
- 原始标题、英文别名
- `score > 10`
- 文件目录

这些信息不能单独证明当前 `score` 的具体数值在某次导入中来自哪个平台。

## 3. 证据等级

### L1：确定来源

只有同时满足以下条件才为 L1：

1. 存在明确记录说明该条目的当前评分字段来自豆瓣或 Metacritic。
2. 来源记录与该条目可唯一关联。
3. 记录中的原始评分值与当前 `score` 可比较。
4. 来源记录不是仅由媒体类型、平台 ID 或数值范围推导。

可接受证据：

- 明确字段，例如 `scoreSource: metacritic` 且评分值匹配。
- 可读取的逐条导入记录，包含条目 ID、source、原始评分和导入时间。
- 可追溯的来源 URL，且来源记录明确将当前数值关联到该 URL。
- 旧版应用专项迁移日志，可唯一映射到当前条目并记录写入值和来源。

不能只写“项目代码存在 Metacritic API”就判定所有游戏旧分数为 L1；必须有逐条值和来源的对应关系。

### L1 处理

来源为 Metacritic：

- 有效旧 score 必须是 0–100 数值。
- 写入 `metacriticScore`。
- 保留 `score`。

来源为豆瓣：

- 媒体类型为 movie、teleplay 或 book。
- 有效旧 score 必须在 0–10。
- 写入 `doubanScore`。
- 保留 `score`。

如果来源记录值不等于当前 score，不能为 L1，转 L2 或 L3。

### L2：较强间接证据

只允许候选，不得自动写专用评分：

- 游戏 `score > 10`，但无来源记录。
- 游戏有合法 `steamAppId`，但无评分来源记录。
- 电影/剧集/图书有合法 `doubanId`，但无评分来源记录。
- 有 Steam、豆瓣或第三方平台条目链接，但链接未绑定评分数值。
- 数值格式或旧流程特征与某平台一致，但无逐条来源记录。
- 存在旧导入代码路径，但无法唯一对应历史条目和当时评分值。

处理：

- 保留旧 `score` 原量纲。
- 不写专用评分字段。
- `scoreEvidence.level = "L2"`。
- `scoreEvidence.action = "review"`。
- `scoreReviewRequired = true`。
- 只跳过 `SCORE_PROMOTION` 操作组；`BASE_SCHEMA`、合法状态兼容和可安全识别的 Callout 同步仍可生成可执行计划。

### L3：来源不明

- 只有裸 `score`。
- 纯手动创建。
- 只有 `datePublished`、普通链接、媒体类型或平台 ID。
- 来源记录缺失或无法匹配。
- 非法评分文本。

处理：

- 保留原始 `score`。
- 不推断来源。
- 不修改量纲。
- `scoreEvidence.level = "L3"`。
- `scoreEvidence.action = "keep_legacy"`。
- `scoreReviewRequired = true`。
- 只跳过 `SCORE_PROMOTION` 操作组，不因此阻止基础 schema 迁移。

## 4. 证据判定表

| 证据 | 可确定条目身份 | 可确定评分来源 | 等级 |
|---|---:|---:|---|
| `steamAppId` | 是 | 否 | L2 候选 |
| `doubanId` | 是 | 否 | L2 候选 |
| 普通豆瓣链接 | 是 | 否 | L2 候选 |
| 游戏 score > 10 | 否 | 否 | L2 候选 |
| 明确来源字段且值匹配 | 是 | 是 | L1 |
| 逐条导入日志且值匹配 | 是 | 是 | L1 |
| 只有裸 score | 否 | 否 | L3 |
| 来源记录值与 score 不一致 | 是 | 否 | L2/L3 |
| 只有旧代码 API 路径 | 否 | 否 | L2 候选，不可按条目自动应用 |

## 5. 评分候选判定伪代码

```python
def classify_score(item, evidence_records):
    score = parse_legacy_score(item.raw.get("score"))
    if score is None:
        return L3("INVALID_OR_MISSING_SCORE")

    records = exact_evidence_for(item, score, evidence_records)
    if records.single_consistent_source("metacritic"):
        return L1("metacritic", "metacriticScore", int(score))
    if records.single_consistent_source("douban"):
        return L1("douban", "doubanScore", score)

    candidate_reasons = []
    if item.type == "game" and score > 10:
        candidate_reasons.append("GAME_SCORE_OVER_10_WITHOUT_PROVENANCE")
    if item.steamAppId:
        candidate_reasons.append("STEAM_ID_WITHOUT_SCORE_PROVENANCE")
    if item.doubanId:
        candidate_reasons.append("DOUBAN_ID_WITHOUT_SCORE_PROVENANCE")
    if item.externalLinks:
        candidate_reasons.append("LINK_WITHOUT_SCORE_PROVENANCE")
    if candidate_reasons:
        return L2(candidate_reasons)

    return L3("NO_PROVENANCE")
```

## 6. normScore 计算

使用十进制安全校验。非法值不参与计算。

### game

```python
if valid_metacritic(metacriticScore):
    normScore = metacriticScore / 10
elif valid_score(score):
    if 10 < score <= 100:
        normScore = score / 10
    elif 0 <= score <= 10:
        normScore = score
    else:
        normScore = None
else:
    normScore = None
```

### movie、teleplay、book

```python
if valid_douban(doubanScore):
    normScore = doubanScore
elif valid_score(score) and 0 <= score <= 10:
    normScore = score
else:
    normScore = None
```

统一规则：

- `myScore` 不参与默认 `normScore`。
- `imdbScore`、`bangumiScore` 不参与 v2 默认 `normScore`。
- 不根据游戏类型无条件把旧 score 当 Metacritic。
- 非法值返回 null 并告警。
- 不把 null 显示为 0。

## 7. 日常评分写入映射

| 操作 | 专用字段 | `score` |
|---|---|---|
| 修改 `myScore` | 更新 `myScore` | 不变 |
| 修改电影 `doubanScore` | 更新 `doubanScore` | 同步 0–10 值 |
| 修改剧集 `doubanScore` | 更新 `doubanScore` | 同步 0–10 值 |
| 修改图书 `doubanScore` | 更新 `doubanScore` | 同步 0–10 值 |
| 修改游戏 `metacriticScore` | 更新 `metacriticScore` | 同步 0–100 整数 |
| 修改 `imdbScore` | 更新 `imdbScore` | 不变 |
| 修改 `bangumiScore` | 更新 `bangumiScore` | 不变 |
| 旧客户端仅 `score` | 不生成专用字段 | 原值兼容保存 |

如果同时提交旧 `score` 与主渠道专用字段，主渠道字段为准并同步旧 `score`。

## 8. Plan 文件顶层结构

Plan 是本地 CLI Dry-run 生成的 JSON，不通过 Web API 持久化。

| 字段 | 类型 | 说明 |
|---|---|---|
| `planFormatVersion` | Integer | 初始为 1 |
| `planId` | UUIDv4 String | 计划唯一 ID |
| `planState` | String | `DRAFT`、`FINALIZED`、`APPLYING`、`APPLIED`、`ROLLED_BACK` |
| `createdAt` | ISO-8601 String | UTC 时间 |
| `appVersion` | String | 生成计划的程序版本 |
| `schemaTarget` | Integer | 2 |
| `vaultRoot` | String | 生成时的绝对 Vault 路径 |
| `vaultRootNormalized` | String | Windows 大小写规范化路径 |
| `policy` | Object | 本次安全策略 |
| `scanSummary` | Object | 汇总统计 |
| `files` | Array | 文件计划项 |
| `counters` | Object | 状态计数 |
| `planChecksum` | String | `sha256:<hex>` |

## 9. 文件计划项

| 字段 | 类型 | 说明 |
|---|---|---|
| `relativePath` | String | POSIX 风格 Vault 相对路径 |
| `mediaType` | String | movie/teleplay/game/book |
| `originalSha256` | String | Dry-run 原文件哈希 |
| `originalSize` | Integer | 原文件字节数 |
| `proposedSha256` | String/null | 应用计划后预测哈希 |
| `proposedMediaId` | UUIDv4/null | 预分配 ID |
| `operationGroups` | Array[OperationGroup] | 可独立审核和执行的迁移操作组 |
| `proposedChanges` | Array[PatchOp] | 可执行字段操作 |
| `scoreEvidence` | ScoreEvidence | 评分证据 |
| `statusEvidence` | StatusEvidence | 状态证据 |
| `warnings` | Array[String] | 警告码 |
| `migrationStatus` | String enum | 计划状态 |
| `skippedReason` | String/null | 跳过原因 |
| `preMigrationSha256` | String/null | Apply 前确认值 |
| `postMigrationSha256` | String/null | Apply 后确认值 |
| `backupPath` | String/null | 单篇备份相对路径 |
| `migrationTimestamp` | ISO-8601/null | 实际应用时间 |

路径不得为绝对路径，不得包含 `..`，不得解析到 Vault 外。

`proposedSha256` 必须只由 `status == "READY"` 的 OperationGroup 所引用操作计算。`REVIEW_REQUIRED` 操作不得预先生效。

## 10. PatchOp

```json
{
  "operationId": "op-001",
  "field": "mediaId",
  "operation": "insert",
  "before": null,
  "after": "c3b9e4a8-7d21-4f8a-9e1b-3f2d1e0a9b8c",
  "reason": "MISSING_MEDIA_ID"
}
```

每个 PatchOp 还需包含计划内唯一的 `operationId`，格式建议为 `op-001`。

`operation` 只能是：

- `insert`
- `set`
- `remove`
- `callout_set`

迁移默认不得使用 `remove` 删除旧字段。

## 10.1 OperationGroup

| 字段 | 类型 | 说明 |
|---|---|---|
| `groupType` | String | `BASE_SCHEMA`、`SCORE_PROMOTION`、`CALLOUT_STATUS`、`STATUS_COMPAT` |
| `selected` | Boolean | 用户/策略是否选择该操作组 |
| `status` | String | 计划阶段 `READY`、`REVIEW_REQUIRED`、`EXCLUDED` |
| `reason` | String/null | 状态原因 |
| `operationIds` | Array[String] | 引用 `proposedChanges` 中的 PatchOp |
| `runStatus` | String | 执行阶段 `NOT_STARTED`、`APPLIED`、`SKIPPED`、`CONFLICT`、`ERROR`、`RESTORED` |
| `attemptedAt` | ISO-8601/null | 最近执行时间 |
| `resultDetail` | String/null | 执行结果 |

`SCORE_PROMOTION` 在 L2/L3 时为 `REVIEW_REQUIRED` 或 `EXCLUDED`，不影响 `BASE_SCHEMA=READY`。

## 11. ScoreEvidence

| 字段 | 类型 | 说明 |
|---|---|---|
| `present` | Boolean | 是否有旧 score |
| `rawText` | String/null | 原文本 |
| `parsedValue` | Number/null | 合法解析值 |
| `level` | `L1`/`L2`/`L3` | 证据等级 |
| `source` | `metacritic`/`douban`/`unknown` | 来源 |
| `evidenceType` | String | 证据类型 |
| `evidenceRefs` | Array[String] | 本地证据记录 ID/字段 |
| `proposedField` | String/null | 专用字段 |
| `proposedValue` | Number/null | 拟写值 |
| `action` | String enum | copy/review/keep_legacy |
| `scoreReviewRequired` | Boolean | 仅 Plan/报告使用 |

`scoreReviewRequired` 不得写入 Markdown。

## 12. StatusEvidence

```json
{
  "legacyStatusRaw": "false",
  "legacyStatusValid": true,
  "progressStatusRaw": null,
  "progressStatusValid": true,
  "resolvedStatus": "backlog",
  "action": "set_progressStatus",
  "warnings": []
}
```

## 13. migrationStatus

- `READY`
- `READY_WITH_REVIEW`
- `REVIEW_REQUIRED`
- `SKIPPED`
- `UNCHANGED`
- `CONFLICT`
- `ERROR`
- `APPLIED`
- `RESTORED`
- `RESTORE_CONFLICT`

`REVIEW_REQUIRED` 只描述没有可执行操作组的文件。文件可以基础迁移但评分待审时使用 `READY_WITH_REVIEW`。

## 14. 完整 Plan 示例

```json
{
  "planFormatVersion": 1,
  "planId": "43a649a4-bf45-4f23-ad09-60b8f4c2e9e1",
  "planState": "FINALIZED",
  "createdAt": "2026-11-07T06:00:00Z",
  "appVersion": "2.1.0",
  "schemaTarget": 2,
  "vaultRoot": "<OBSIDIAN_VAULT>",
  "vaultRootNormalized": "d:\\文档\\obsidian库\\影音库",
  "policy": {
    "preserveLegacyScore": true,
    "automaticScoreMigration": "L1_ONLY",
    "preserveUnknownFields": true,
    "preserveBody": true,
    "skipDuplicateKeys": true,
    "requireObsidianPaused": true
  },
  "scanSummary": {
    "files": 1,
    "ready": 0,
    "readyWithReview": 1,
    "reviewRequired": 1,
    "skipped": 0
  },
  "files": [
    {
      "relativePath": "02-电影阅读/game/示例游戏.md",
      "mediaType": "game",
      "originalSha256": "8ed84a33f1e5bb8b81a0fd9add332b7b56f1a5ea123ecc4f47666fd17719f884",
      "originalSize": 2048,
      "proposedSha256": "6c06d22498fb0d96a13cf4be60ada326fe735fd39dc82fdc3d54b8e891804690",
      "proposedMediaId": "b3e17d89-16be-4a45-b958-ec0bb78fb3df",
      "operationGroups": [
        {
          "groupType": "BASE_SCHEMA",
          "selected": true,
          "status": "READY",
          "reason": null,
          "operationIds": ["op-001", "op-002", "op-003"],
          "runStatus": "NOT_STARTED",
          "attemptedAt": null,
          "resultDetail": null
        },
        {
          "groupType": "SCORE_PROMOTION",
          "selected": false,
          "status": "REVIEW_REQUIRED",
          "reason": "L2_SOURCE_NOT_PROVEN",
          "operationIds": [],
          "runStatus": "NOT_STARTED",
          "attemptedAt": null,
          "resultDetail": null
        }
      ],
      "proposedChanges": [
        {
          "operationId": "op-001",
          "field": "schemaVersion",
          "operation": "insert",
          "before": null,
          "after": 2,
          "reason": "MISSING_SCHEMA_VERSION"
        },
        {
          "operationId": "op-002",
          "field": "mediaId",
          "operation": "insert",
          "before": null,
          "after": "b3e17d89-16be-4a45-b958-ec0bb78fb3df",
          "reason": "MISSING_MEDIA_ID"
        },
        {
          "operationId": "op-003",
          "field": "progressStatus",
          "operation": "insert",
          "before": null,
          "after": "backlog",
          "reason": "LEGACY_FALSE_TO_BACKLOG"
        }
      ],
      "scoreEvidence": {
        "present": true,
        "rawText": "87",
        "parsedValue": 87,
        "level": "L2",
        "source": "unknown",
        "evidenceType": "SCORE_OVER_10_WITHOUT_IMPORT_RECORD",
        "evidenceRefs": [],
        "proposedField": null,
        "proposedValue": null,
        "action": "review",
        "scoreReviewRequired": true
      },
      "statusEvidence": {
        "legacyStatusRaw": "false",
        "legacyStatusValid": true,
        "progressStatusRaw": null,
        "progressStatusValid": true,
        "resolvedStatus": "backlog",
        "action": "set_progressStatus",
        "warnings": []
      },
      "warnings": [
        "SCORE_SOURCE_REVIEW_REQUIRED"
      ],
      "migrationStatus": "READY_WITH_REVIEW",
      "skippedReason": null,
      "preMigrationSha256": null,
      "postMigrationSha256": null,
      "backupPath": null,
      "migrationTimestamp": null
    }
  ],
  "counters": {
    "READY": 0,
    "READY_WITH_REVIEW": 1,
    "REVIEW_REQUIRED": 0,
    "SKIPPED": 0,
    "UNCHANGED": 0
  },
  "planChecksum": "sha256:PLACEHOLDER"
}
```

示例中的 `proposedSha256` 和 checksum 必须在真实 Dry-run 中计算，不能照抄占位值。

## 15. Plan checksum

1. 复制 Plan 对象并移除 `planChecksum`。
2. 使用 UTF-8 无 BOM。
3. JSON Key 排序。
4. 分隔符为 `,` 和 `:`，不产生额外空格。
5. `ensure_ascii=false`。
6. 对规范化 UTF-8 字节计算 SHA-256。
7. 最后写入 `sha256:<hex>`。

校验时必须重新执行相同算法。

## 16. Plan 完整性

拒绝 Apply：

- Plan checksum 不匹配。
- `planId` 非 UUIDv4。
- `planState` 不是 `FINALIZED`。
- `vaultRoot` 与当前 Vault 不一致。
- `schemaTarget` 不是 2。
- `relativePath` 含 `..` 或绝对路径。
- 文件重复出现。
- proposedMediaId 非法或与库存冲突。
- proposedChanges 未知字段/未知 operation。
- operationGroups 引用了不存在的 operationId，或同一 operationId 被重复引用。
- proposedSha256 无法由计划 Patch 重算得出。
- Plan 已应用或已回滚。
- Plan 含没有可执行操作组的 `REVIEW_REQUIRED` 却被要求自动执行。
- `READY_WITH_REVIEW` 中未标记为 `READY` 的操作组被意外执行。

## 17. 测试用例

- 明确 MC 来源且值一致 -> L1。
- 明确豆瓣来源且值一致 -> L1。
- 仅 steamAppId -> L2。
- 仅 doubanId -> L2。
- 游戏 score > 10 但无日志 -> L2。
- 裸 score -> L3。
- 来源值不匹配 -> 不可 L1。
- 计划 checksum 篡改 -> 拒绝。
- 路径穿越 -> 拒绝。
- proposedMediaId 重复 -> 拒绝。
- 同一 Plan 二次 Apply -> 拒绝。
- Plan 中状态未知 -> REVIEW_REQUIRED。
