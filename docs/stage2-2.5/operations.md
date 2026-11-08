# 迁移、备份与恢复说明

## 1. 使用顺序

```powershell
python app\server.py --migrate-vault "D:\your\vault" --scan
python app\server.py --migrate-vault "D:\your\vault" --dry-run
```

检查 `.media-app/migration/reports/preview-<planId>.md`。

暂停 Obsidian 编辑后：

```powershell
python app\server.py --migrate-vault "D:\your\vault" --apply --plan ".media-app\migration\plans\plan-<planId>.json"
```

输入：

```text
APPLY <planId>
```

## 2. 回滚

```powershell
python app\server.py --migrate-vault "D:\your\vault" --rollback --plan ".media-app\migration\plans\plan-<planId>.json"
```

输入：

```text
ROLLBACK <planId>
```

如果迁移后再次编辑过某篇，回滚不会覆盖该篇。

## 3. 备份位置

- 迁移前完整快照：`.media-app/backups/migrations/<planId>/`
- 快照 manifest：该目录下 `manifest.json`
- Journal：`.media-app/migration/apply-journals/<planId>.jsonl`
- 运行状态：`.media-app/migration/states/state-<planId>.json`

## 4. 中断恢复

如果 Plan 状态为 `APPLYING`：

```powershell
python app\server.py --migrate-vault "D:\your\vault" --apply --plan "<plan>" --resume
```

仍需输入原 Plan 的 `APPLY <planId>`。没有快照覆盖或当前状态无法确认的文件会继续标记冲突。

## 5. 锁文件

`.media-app/migration/apply.lock` 表示有 Apply 或 Rollback 正在进行，或上一次进程异常退出。程序不会自动删除遗留锁；必须人工确认没有迁移进程后再处理。

