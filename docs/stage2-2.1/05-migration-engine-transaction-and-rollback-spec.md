# Migration Engine 哈希比对、事务保护与两级回滚设计规范

状态：2.1 冻结候选

目标阶段：2.4 实现、2.5 Vault 副本验收

## 1. 边界

- 迁移只面向 `02-电影阅读/**/*.md`。
- 不修改附件，除非未来另行批准并冻结独立规范。
- 不移动、不重命名、不删除媒体笔记。
- Web API 只能只读。
- Scan、Dry-run、Apply、Rollback 只能由本地 CLI 执行。
- 程序启动不得自动扫描迁移或写入。
- 正式 Apply 必须由用户针对具体 `planId` 明确确认。

## 2. CLI

```bash
python server.py --migrate-vault "<OBSIDIAN_VAULT>" --scan
python server.py --migrate-vault "<OBSIDIAN_VAULT>" --dry-run
python server.py --migrate-vault "<OBSIDIAN_VAULT>" --apply --plan ".media-app/migration/plan-xxx.json"
python server.py --migrate-vault "<OBSIDIAN_VAULT>" --rollback --plan ".media-app/migration/plan-xxx.json"
```

Apply 前交互确认：

```text
APPLY <planId>
```

Rollback 前交互确认：

```text
ROLLBACK <planId>
```

不接受空回车、`yes` 或模糊确认。

## 3. 文件发现规则

扫描：

- 正常媒体根目录：`02-电影阅读/`。
- 递归扩展名：`.md`。
- 忽略目录：
  - `.media-app/`
  - `.media-app-stage1/`
  - `attachments/`
  - `.git/`
  - `.obsidian/`
  - 任何备份/快照/迁移报告目录
  - 以点开头的隐藏工作目录
- 不跟随指向 Vault 外的符号链接。
- 符号链接、junction 或其他重解析点默认跳过并报告。
- 已知媒体类型路径：movie、teleplay、game、book。
- 未知类型值或未知目录：只读纳入异常报告，默认跳过迁移。
- 同一解析绝对路径只能出现一次。

目录排除规则必须可配置，但不得扩大到未经批准的非媒体目录。

## 4. Scan

Scan 只读：

- 文件总数。
- 媒体根目录数量。
- type 分布。
- schemaVersion 分布。
- mediaId 缺失、合法、非法、重复。
- 旧 score 值和候选量纲。
- L1/L2/L3 证据分布。
- 旧 status 合法/非法。
- progressStatus 合法/未知。
- YAML 语法异常。
- 重复 Key。
- 不支持语法。
- 可自动迁移、需人工审核、跳过数量。

Scan 不修改内容和 mtime。

## 5. Dry-run

Dry-run：

1. 执行只读 Scan。
2. 对每个可处理文件构建内存 Patch。
3. 计算原始 SHA-256。
4. 为缺少 ID 的文件生成 UUIDv4，并只写入 Plan。
5. 预生成完整后文件字节和 `proposedSha256`。
6. 计算 `planChecksum`。
7. 将 Plan 和预览报告写入应用专用目录。

Dry-run 禁止：

- 写 Markdown。
- 改 mtime。
- 写 `schemaVersion` 或 `mediaId`。
- 写附件。
- 重建 Obsidian 链接。
- 自动将 `REVIEW_REQUIRED` 条目变为可执行。

建议目录：

```text
.media-app/migration/
├─ plans/
├─ reports/
└─ apply-journals/
```

## 6. Plan 验证

Apply 前逐项验证：

- Plan JSON 可严格解析。
- `planFormatVersion` 受支持。
- `planChecksum` 正确。
- `planId` 为合法 UUIDv4。
- `planState` 为 `FINALIZED`。
- `vaultRoot` 与当前 Vault 解析路径一致。
- `schemaTarget == 2`。
- `relativePath` 为 Vault 内 POSIX 相对路径。
- 拒绝 `..`、绝对路径、NUL、盘符路径、目录逃逸和重解析点。
- 文件唯一。
- `originalSize` 与哈希格式合法。
- `proposedChanges` 字段和操作合法。
- `operationGroups` 引用关系完整，只执行 `READY` 组。
- `proposedSha256` 能由 Patch 结果重算得到。
- `proposedMediaId` 唯一，且不与现有合法 ID 冲突。
- `migrationStatus` 为可应用状态；`REVIEW_REQUIRED` 不自动执行。
- Plan 未被执行或回滚。

## 7. 全局迁移锁

- 同一 Vault 同时只允许一个 Apply。
- 锁文件位于 `.media-app/migration/apply.lock`。
- 锁包含 pid、planId、创建时间。
- 启动 Apply 时使用原子创建。
- 锁存在时拒绝启动第二实例。
- 崩溃遗留锁只允许人工检查后删除，不得自动忽略。

程序锁只约束 Migration Engine；Obsidian 不一定遵守。

## 7.1 操作组幂等

- Plan 的 checksum 在 Apply 开始前固定。
- 每个 OperationGroup 记录 `selected`、计划状态和运行状态。
- 已进入 `APPLIED` 的操作组不得再次执行。
- 一个文件可同时包含成功的基础迁移组和待审核的评分组。
- 不完整或损坏的 Plan 不得标记为可 Apply。
- Plan 已完整执行后再次 Apply 必须拒绝。
- 只有崩溃后状态为 `APPLYING` 的 Plan 可显式 resume，并依赖 journal 跳过已成功组。
- resume 前必须重新校验 Plan checksum、Vault 路径、文件状态和快照完整性。

## 8. 迁移前快照

步骤：

1. 收集本次 Plan 中所有可能修改的 Markdown。
2. 在 `.media-app/backups/snapshots/pre-migration-v2/<planId>/` 创建快照。
3. 按 Vault 相对路径复制文件。
4. 对复制文件计算 SHA-256。
5. 写 `manifest.json`。
6. Flush/fsync。
7. 重新读取每个快照文件并验证 SHA-256。
8. 验证 manifest 和 Plan 中文件集合一致。
9. 快照目录整体原子启用。

快照失败：禁止 Apply。

当前阶段 1 代码只读核对发现：

- `Library.create_snapshot_backup(label, include_attachments=False)` 存在。
- 底层 `create_snapshot_backup(vault, source_paths, label, snapshots_root=None)` 存在。
- 当前实现会写 manifest、fsync 并原子启用快照目录。

2.4 实现前必须重新核对其签名和行为，不得直接假定已满足迁移所需事务语义。

## 9. SHA-256 与 TOCTOU

### 9.1 Plan 时

```text
read original bytes
→ sha256(original)
→ write Plan.originalSha256
```

### 9.2 Apply 前

```text
read current bytes
→ sha256(current)
→ compare with Plan.originalSha256
```

不一致：

- 不写该文件。
- 状态 `CONFLICT`。
- 保留 Obsidian 当前内容。

### 9.3 最终替换前

1. 按 Plan 将原字节生成新字节。
2. 验证新字节哈希等于 `proposedSha256`。
3. 写同目录临时文件。
4. Flush/fsync。
5. 重新读取临时文件并校验哈希。
6. 解析和验证 YAML 与正文保护。
7. 再次读取目标文件并校验 SHA-256。
8. 仅在校验仍等于原始哈希时执行原子替换。

### 9.4 TOCTOU 边界

单次哈希检查和原子替换之间仍存在不可完全消除的窗口。Obsidian 若在同一时刻写入：

- 程序不能宣称已实现绝对互斥。
- 必须要求用户关闭 Obsidian 或暂停 Vault 编辑。
- Apply 前显示该要求。
- 文件在最终检查后仍变化时，可能由原子替换产生最后一次写入竞争。
- 后置哈希若与预期不同，立即标记 `CONFLICT` 并禁止自动回滚覆盖。

## 10. 原子写入

单篇流程：

```text
验证 Plan item
→ 检查当前 SHA-256
→ 基于原始字节生成 Patch
→ 校验 proposedSha256
→ 写同目录唯一临时文件
→ flush + fsync
→ 回读临时文件
→ 校验字节哈希和受限 YAML
→ 确认正文仅发生批准变化
→ 再次校验目标 SHA-256
→ os.replace(temp, target)
→ 回读目标
→ 校验最终 SHA-256
→ 记录 APPLIED
```

失败规则：

- 临时文件尚未替换：删除临时文件，原文件不动。
- 替换前检测变化：`CONFLICT`。
- 替换后校验失败且目标仍等于本次写入内容：可条件恢复原文。
- 替换后目标已被其他进程修改：`RESTORE_CONFLICT`，绝不覆盖。
- 恢复时也采用临时文件和原子替换。

## 11. Apply 循环

```python
with acquire_vault_migration_lock(vault, plan_id):
    plan = load_and_verify_plan()
    snapshot = create_and_verify_snapshot(plan)
    require_interactive_confirmation(plan)

    for item in plan.files:
        if item.migrationStatus not in {"READY", "READY_WITH_REVIEW", "UNCHANGED"}:
            report_skip(item)
            continue

        ready_ids = {
            operation_id
            for group in item.operationGroups
            if group.status == "READY"
            for operation_id in group.operationIds
        }
        executable_operations = [
            op for op in item.proposedChanges
            if op.operationId in ready_ids
        ]

        current = target.read_bytes()
        if sha256(current) != item.originalSha256:
            journal(item, "CONFLICT")
            continue

        new_bytes = apply_frozen_patch(current, executable_operations)
        if sha256(new_bytes) != item.proposedSha256:
            journal(item, "ERROR", "PATCH_HASH_MISMATCH")
            continue

        try:
            atomic_apply_with_guards(
                target,
                new_bytes=new_bytes,
                original_sha=item.originalSha256,
                proposed_sha=item.proposedSha256,
            )
            journal(item, "APPLIED")
        except ExternalModification:
            journal(item, "CONFLICT")
        except PostWriteVerificationError:
            handle_single_file_rollback(item)

run_post_audit(plan)
```

每完成一篇都必须 append journal 并 flush，避免进程崩溃后丢失完成状态。

## 12. 单篇回滚

条件：

```text
currentSha256 == postMigrationSha256
```

才允许自动恢复：

1. 读取该篇迁移前备份。
2. 验证备份 SHA-256 等于 `preMigrationSha256`。
3. 写同目录临时文件。
4. 回读并验证。
5. 再次确认当前文件仍等于 `postMigrationSha256`。
6. 原子恢复。
7. 回读并验证恢复后哈希。
8. 状态 `RESTORED`。

如果：

```text
currentSha256 != postMigrationSha256
```

则：

- 不恢复。
- `RESTORE_CONFLICT`。
- 保留用户当前文件。
- 报告当前文件哈希、Plan 后哈希、备份路径。

## 13. 全局回滚

全局回滚不是“一键覆盖”：

- 仅本地 CLI。
- 指定原 `planId`。
- 显示影响文件数。
- 要求 `ROLLBACK <planId>` 确认。
- 逐文件检查当前哈希。
- 能恢复的文件恢复。
- 冲突文件跳过并保留。
- 一篇失败不停止其余可安全恢复文件，但必须写完整报告。
- 最终汇总 `RESTORED`、`RESTORE_CONFLICT`、`ERROR`。

## 14. Post-audit

检查：

- Plan 文件数、实际处理数、跳过数一致。
- 成功文件最终哈希等于 `postMigrationSha256`。
- 新 `mediaId` 合法且全局唯一。
- 已存在媒体 ID 不变。
- `schemaVersion` 等于目标版本。
- 受限 YAML 可解析。
- 状态与旧 `status` 兼容。
- 评分专用字段只在 L1 写入。
- 旧 `score` 原量纲未改变。
- 未知字段、正文、图片引用和双链未被破坏。
- 文件数、类型分布和目录结构未改变。
- 已知历史异常与本次新增异常分开统计。

出现系统性异常时：

- 停止继续写入。
- 输出全局审计报告。
- 不自动回滚。
- 下一次命令必须由用户重新授权。

## 15. Web API 边界

允许：

- 只读扫描摘要。
- 内存中预览。
- 读取已存在报告。

禁止：

- 通过 Web API 生成持久化 Plan。
- Apply。
- 批量写入。
- 自动回滚。
- 修改文件。
- 绕过 CLI 人工确认。

## 16. 状态码

| 状态 | 含义 |
|---|---|
| `READY` | 计划可执行 |
| `READY_WITH_REVIEW` | 基础操作可执行，评分专用字段仍待人工确认 |
| `REVIEW_REQUIRED` | 整个文件没有可自动执行操作组 |
| `SKIPPED` | 明确跳过 |
| `UNCHANGED` | 无需修改 |
| `CONFLICT` | Apply 时文件变化 |
| `ERROR` | 计划、Patch、临时文件或写入失败 |
| `APPLIED` | 成功应用并通过后校验 |
| `RESTORED` | 单篇或全局回滚成功 |
| `RESTORE_CONFLICT` | 迁移后用户编辑，禁止覆盖 |

## 17. 失败场景与应对

| 失败场景 | 结果 |
|---|---|
| Plan checksum 错误 | 拒绝全部 Apply |
| Vault 路径不匹配 | 拒绝 |
| Plan 已执行 | 拒绝重复执行 |
| 快照创建失败 | 禁止 Apply |
| 快照文件哈希错误 | 禁止 Apply |
| 重复 mediaId | 该篇跳过或全局阻断，按严重度处理 |
| Plan 前后文件哈希变化 | 该篇 `CONFLICT` |
| 临时文件写入失败 | 原文件不动，`ERROR` |
| 临时文件哈希错误 | 删除临时文件，`ERROR` |
| 最终替换前文件变化 | 不替换，`CONFLICT` |
| 替换后最终哈希错误且目标仍为新内容 | 条件恢复，`RESTORED` 或 `ERROR` |
| 替换后目标被用户修改 | `RESTORE_CONFLICT` |
| Post-audit 发现新系统损坏 | 停止写入，输出报告 |
| Rollback 发现用户后编辑 | `RESTORE_CONFLICT` |
| 进程崩溃 | 根据 journal 和快照恢复状态，不自动覆盖 |
| 观察到隐藏路径/符号链接 | 跳过并报告 |
| 未来 schemaVersion | 只读跳过 |

## 18. Rollback 伪代码

```python
for item in records:
    current = safe_read(item.relativePath)
    current_hash = sha256(current)

    if current_hash != item.postMigrationSha256:
        journal(item, "RESTORE_CONFLICT")
        continue

    backup_bytes = read_verified_backup(item.backupPath, item.preMigrationSha256)
    temp = write_same_directory_temp(item.relativePath, backup_bytes)
    verify_temp(temp, item.preMigrationSha256)

    if sha256(safe_read(item.relativePath)) != item.postMigrationSha256:
        delete_temp(temp)
        journal(item, "RESTORE_CONFLICT")
        continue

    os.replace(temp, item.relativePath)
    verify_target(item.preMigrationSha256)
    journal(item, "RESTORED")
```

## 19. 验收测试

- Scan 和 Dry-run 不改内容、mtime。
- Dry-run 预分配 ID 固化。
- Plan checksum 篡改被拒绝。
- Plan 重复 Apply 被拒绝。
- Plan 路径不匹配被拒绝。
- 路径穿越被拒绝。
- Obsidian 在 Apply 前修改 -> conflict。
- 临时文件完成后、替换前修改 -> conflict。
- 原子替换后、验证前修改 -> 保留外部修改并 `RESTORE_CONFLICT`。
- 单篇错误不影响后续 ready 文件。
- 快照验证失败禁止 Apply。
- 全局回滚跳过已修改文件。
- Post-audit 区分历史异常与新增异常。
- 真实 Vault 副本完成 Obsidian、Dataview、旧 API 和新 API 检查。

## 20. 待确认

- 现有快照函数的事务语义是否可直接复用。
- Windows 文件锁能否覆盖 Obsidian 竞争窗口。
- Apply journal 的 fsync 原子策略。
- 是否需要记录文件系统 identity；如需，Windows/POSIX 兼容方案需单独设计。
- Post-audit 对已有重复 ID 和未知状态的严重度分级。
