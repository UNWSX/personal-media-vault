# 阶段 2.4 测试报告

日期：2026-11-07

## 自动化覆盖

最终全量 Python 回归：

```text
Ran 102 tests
OK
```

失败 0，跳过 0。

- Scan 不改内容与 mtime。
- Dry-run 预分配固定 UUID。
- Plan checksum 篡改拒绝。
- 路径穿越拒绝。
- 精确 Apply/Rollback 确认。
- 全局迁移锁。
- Plan 重复 Apply 拒绝。
- 临时文件验证失败时原文件不变。
- 快照失败禁止 Apply。
- Apply 前外部修改 -> CONFLICT。
- 临时文件完成后、替换前外部修改 -> 不替换。
- 替换后、验证前外部修改 -> RESTORE_CONFLICT。
- 单篇失败不阻止后续文件。
- `APPLYING` 状态显式 resume。
- L1 评分提升与 L2/L3 保留旧评分。
- 状态冲突审核。
- 未来 schema、重复/非法 ID 跳过。
- 全局回滚恢复原始字节。
- 迁移后用户编辑导致回滚冲突。
- 操作组级 runStatus 写入运行状态。
- Web API 只读边界。
- Obsidian 临时副本插件信任由人工确认，Dataview `媒体筛选库6.0` 界面正常渲染。

JavaScript 检查、.NET Release 构建和隔离 PyInstaller 构建也通过。

## 工具检查

- `py_compile` 通过。
- JavaScript 检查通过。
- .NET Release 构建通过。
- PyInstaller 隔离构建通过。

