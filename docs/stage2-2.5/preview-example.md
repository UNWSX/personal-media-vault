# 迁移预览报告示例

以下内容依据真实 Vault 的只读 Dry-run 脱敏整理。

## 汇总

```text
文件总数：295
READY_WITH_REVIEW：295
BASE_SCHEMA READY：295
CALLOUT_STATUS READY：51
CALLOUT_STATUS REVIEW_REQUIRED：244
SCORE_REVIEW：293
```

## 示例一

```text
文件：02-电影阅读/movie/示例电影.md

操作组：
BASE_SCHEMA：READY
  schemaVersion: 2
  mediaId: <预分配 UUIDv4>
  progressStatus: completed

SCORE_REVIEW：REVIEW_REQUIRED
  score: 9.4
  evidence: L3_NO_SCORE_PROVENANCE
  不写 doubanScore

CALLOUT_STATUS：REVIEW_REQUIRED
  reason: CALLOUT_VALUE_UNRECOGNIZED
  正文保持不变
```

## 示例二

```text
文件：02-电影阅读/game/示例游戏.md

操作组：
BASE_SCHEMA：READY
  schemaVersion: 2
  mediaId: <预分配 UUIDv4>
  progressStatus: backlog

SCORE_REVIEW：REVIEW_REQUIRED
  score: 86
  evidence: L2_GAME_SCORE_OVER_10_WITHOUT_PROVENANCE
  不写 metacriticScore

CALLOUT_STATUS：READY
  仅替换唯一状态文本
```

## 审核原则

- 基础身份与合法状态迁移可以和评分审核同时存在。
- L2/L3 不会阻止 BASE_SCHEMA。
- 没有逐条来源证据时保留旧 `score`。
- Callout 只有在唯一匹配且当前值可识别时才更新。

