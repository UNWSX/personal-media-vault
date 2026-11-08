# 阶段 2.4 修改清单

## 新增

### `app/migration.py`

- Vault 媒体文件发现和路径安全校验。
- Scan 与 L1/L2/L3 评分证据分类。
- Dry-run Plan 和预览报告。
- Plan checksum 与结构验证。
- 迁移专用快照。
- 全局 Apply 锁。
- 逐文件安全替换与条件回滚。
- Apply journal、运行状态和 resume。
- Post-audit。
- 单篇/全局回滚。

### `app/migrate_vault.py`

- `--scan`
- `--dry-run`
- `--apply`
- `--rollback`
- `--resume`
- 精确确认文本
- 操作组选择
- 外部证据文件入口

### `app/callout_sync.py`

- 抽离只更新唯一状态 Callout 的兼容函数，供 API 和迁移共用。

### 测试与审计脚本

- `tests/test_migration.py`
- `tests/audit_migration_copy.py`
- `tests/audit_dataview_compat.py`

## 修改

### `app/server.py`

- 增加 migration CLI 分流。
- 增加只读 `/api/migration/scan`。
- 增加安全文件名限定的 `/api/migration/reports`。
- 不增加 Web Apply/Rollback 接口。

### `tests/test_api_phase1.py`

- 验证迁移 Web 接口只读。
- 验证 Scan 不改 Markdown 或 mtime。

## 兼容性

- 旧 API URL 和返回字段不变。
- 迁移是独立 CLI，不自动在启动时运行。
- 真实 Vault 仍保持只读，直到人工接受 2.5。

