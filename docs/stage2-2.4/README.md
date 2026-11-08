# 阶段 2.4：Migration Engine

状态：实现完成并进入 2.5 副本验收  
阶段范围：Scan、Dry-run、Plan、Apply、Post-audit、Rollback 和只读 Web 扫描

## 1. 工具入口

CLI 入口：

```powershell
python app\server.py --migrate-vault <vault> --scan
python app\server.py --migrate-vault <vault> --dry-run
python app\server.py --migrate-vault <vault> --apply --plan <plan.json>
python app\server.py --migrate-vault <vault> --rollback --plan <plan.json>
```

也可以直接运行：

```powershell
python app\migrate_vault.py --migrate-vault <vault> --scan
```

Apply 和 Rollback 的 `--confirm` 必须与 `APPLY <planId>`、`ROLLBACK <planId>` 完全一致。

## 2. 数据目录

```text
.media-app/
├─ migration/
│  ├─ plans/
│  ├─ reports/
│  ├─ states/
│  └─ apply-journals/
└─ backups/
   └─ migrations/
      └─ <planId>/
```

迁移专用快照没有自动清理策略。

## 3. Scan

只读统计：

- 文件与媒体类型。
- schemaVersion 和 mediaId 状态。
- 重复/非法身份。
- 旧 status 与 progressStatus。
- `score` 及 L1/L2/L3 证据。
- 不支持 YAML。
- 路径重解析点和未知目录。

Web API：

```text
GET /api/migration/scan
GET /api/migration/reports?name=<filename>
```

Web API 没有 Apply、Rollback 或持久化 Dry-run 路由。

## 4. 操作组

| 操作组 | 默认 | 行为 |
|---|---|---|
| `BASE_SCHEMA` | 选中 | 添加 schemaVersion、mediaId、可确定的状态兼容字段 |
| `CALLOUT_STATUS` | 选中 | 仅在唯一匹配且值可识别时更新正文状态行 |
| `SCORE_PROMOTION` | 未选 | 只对 L1 来源写入专用评分 |
| `SCORE_REVIEW` | 审核 | L2/L3 保留旧 score，不自动写入 |
| `CONFLICTING_LEGACY_STATUS` | 审核 | 状态语义冲突时不选边 |

可用逗号分隔选择操作组：

```powershell
--groups BASE_SCHEMA,CALLOUT_STATUS,SCORE_PROMOTION
```

## 5. Plan

Plan 记录：

- 固定 UUIDv4 `planId` 和预分配 `proposedMediaId`。
- Vault 绝对路径及 Windows 规范化路径。
- 原文件 SHA-256、大小、候选 SHA-256。
- 每个操作组的 selected、计划状态和操作引用。
- 每篇评分与状态证据。
- 不可变 `planChecksum`。

运行状态保存在独立 `states/state-<planId>.json`，避免 Apply 修改带 checksum 的 Plan。文件级与操作组级 runStatus、journal 和结果都单独记录。

## 6. Apply

顺序：

1. 验证 Plan checksum、路径、UUID、操作引用和候选哈希。
2. 创建迁移专用快照并回读 manifest。
3. 原文件哈希不一致时标记 `CONFLICT`。
4. 基于当前原始字节重新执行冻结操作。
5. 校验候选哈希、临时文件哈希和 YAML。
6. 最终替换前再次检查源哈希。
7. 原子替换后验证最终哈希。
8. 每篇完成后 fsync journal 和状态。
9. Post-audit。

进程中断后只可对 `APPLYING` Plan 使用 `--resume`。已等于候选哈希的文件会被核对为 APPLIED；缺少安全快照的文件不会写入。

## 7. Rollback

全局回滚只接受完整 `APPLIED` Plan。

- 当前哈希等于计划后哈希：恢复快照。
- 当前哈希已变化：`RESTORE_CONFLICT`，保留用户内容。
- 单个冲突不阻止其他安全文件恢复。
- 快照文件、manifest 和原 Plan 哈希必须先一致。

## 8. TOCTOU 边界

程序在 Plan、临时文件和替换前多次检查哈希，但文件哈希检查与 `os.replace()` 之间仍存在无法彻底消除的竞争窗口。Apply 期间必须暂停 Obsidian 编辑。

