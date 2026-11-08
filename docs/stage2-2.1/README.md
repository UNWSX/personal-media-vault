# 个人影音库 2.0 · 2.1 冻结文档包

状态：等待人工审查

源码基线：`703e372`（只读核对）

范围：技术规范冻结，不含实现或迁移

## 文档索引

0. [后续阶段正式任务书（控制文件）](00-authoritative-taskbook.md)
1. [第二阶段完整修订版开发任务书](01-complete-revised-development-spec.md)
2. [数据字典与 YAML 局部 Patch 规格说明](02-data-dictionary-and-yaml-patch-spec.md)
3. [进度状态与正文 Callout 完整映射表](03-progress-status-and-callout-map.md)
4. [评分来源证据链判定流程与 Migration Plan 数据结构规范](04-score-evidence-and-migration-plan-spec.md)
5. [Migration Engine 哈希比对、事务保护与两级回滚设计规范](05-migration-engine-transaction-and-rollback-spec.md)

## 修订摘要

- 新增 `00-authoritative-taskbook.md`，作为后续代码实施和验收的最高优先级任务书。
- 已审查 12 项复审意见：确认 11 项需要修订；`tags`/`genre` 的“自动双写”建议未被采纳，改为只读归一化，避免混淆题材与自由标签。

- 以较新的安全优先任务书为准，交付 5 份完整规范。
- 明确保持 Python 标准库，禁止 PyYAML、ruamel.yaml 和任何第三方 YAML 依赖。
- 评分证据冻结为 L1、L2、L3 三级。
- `score > 10`、游戏类型、`steamAppId`、`doubanId` 和普通平台链接只属于 L2 候选证据，不能自动判定评分数值来源。
- 旧 `score` 永远保留原始量纲；专用评分仅在 L1 证据下迁移。
- `normScore` 为运行时字段，禁止落盘。
- 状态枚举、旧 `status` 映射、四键筛选和 Callout 映射已统一。
- 受限 YAML 子集和字节级局部 Patch 边界已冻结。
- Plan 包含原始/预期哈希、预分配 ID、字段操作、评分证据和完整性校验。
- Migration Engine 的 Scan、Dry-run、Apply、Post-audit、两级回滚和 `RESTORE_CONFLICT` 已定义。
- 409 处理要求字段级 `dirtyFields` 草稿保护，不能只更新 revision。

## 已解决的关键风险

1. 防止把游戏高分或平台 ID 错判成 Metacritic/豆瓣评分数值来源。
2. 防止迁移中把旧 `score` 的 0–10 与 0–100 量纲混合或归一化写回。
3. 防止全量 YAML 重序列化删除注释、未知字段和正文格式。
4. 防止 Dry-run 与 Apply 之间、临时文件完成后、原子替换前的外部编辑被覆盖。
5. 防止迁移后用户再次编辑时被 Rollback 静默恢复覆盖。
6. 防止 409 后仅更新 revision 导致旧 Item 覆盖 Obsidian 新字段。
7. 防止 Web API 提供批量迁移或自动回滚。
8. 防止未来 `schemaVersion` 被降级或重复 `mediaId` 被自动重新分配。
9. 防止不同 Callout 模板被宽泛正则误改。

## 仍需人工确认

| 事项 | 风险 | 建议决策 |
|---|---|---|
| 阶段 1 最终验收状态 | 阶段 2.2 门禁是否满足 | 提供正式验收结论 |
| 字段级 `dirtyFields` | 同字段冲突保护是否完整 | 2.3 前冻结字段集合与交互 |
| 历史评分来源日志 | 是否足以达到 L1 | 确定可追溯记录来源 |
| Callout 历史变体 | 自动同步可能漏改或误改 | 2.2 前提供/确认样本集 |
| `music` 等未列类型 | 状态模型可能不完整 | 保留为只读跳过或单独冻结 |
| 快照函数复用 | 事务边界可能与迁移要求不同 | 2.4 前重新核对 |
| Windows 文件锁与 Obsidian | TOCTOU 无法完全消除 | Apply 前强制暂停 Obsidian |
| Journal fsync 策略 | 崩溃恢复状态可能不完整 | 2.4 前冻结持久化细节 |

## 本次执行声明

- 本次只新增第二阶段 2.1 技术文档。
- 未修改任何业务源码。
- 未执行 Scan、Dry-run、Apply 或 Rollback。
- 未创建真实迁移快照或迁移计划。
- 未修改真实 Vault、Markdown、附件、文件名或目录结构。
- 未部署或覆盖现有版本。
- 未进入 2.2。

本文件包完成后停止，等待人工审查和后续明确授权。
