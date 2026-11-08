# 阶段 1：备份、日志与恢复

## 目录

所有辅助数据都位于 Vault 的应用专用目录，不进入正式媒体目录：

```text
.media-app/
├─ backups/
│  ├─ notes/        每次修改前的单篇笔记原文
│  └─ snapshots/    手动或批量操作前的完整快照
├─ logs/            JSONL 操作日志
├─ trash/           删除功能的回收目录
└─ migration/       预留给阶段 2
```

## 单篇备份

修改已有笔记前，程序按原始 Vault 相对路径保存原文：

```text
.media-app/backups/notes/02-电影阅读/movie/示例/
└─ 20261107-123456-a1b2c3d4.md
```

每篇笔记默认保留最近 20 个版本。备份失败时停止写入，原文件保持不变。

## 完整快照

顶栏“立即备份”按钮会保存媒体 Markdown 和 `attachments/` 图片。批量游戏升级开始前也会自动保存媒体 Markdown 快照。

```text
.media-app/backups/snapshots/20261107-123456-manual-a1b2c3d4/
├─ 02-电影阅读/
├─ attachments/
└─ manifest.json
```

`manifest.json` 记录快照时间、文件相对路径、字节数和 SHA-256。默认保留最近 10 个完整快照。

## 恢复单篇笔记

1. 先关闭影音库窗口，并在 Obsidian 中停止编辑对应笔记。
2. 在 `.media-app/backups/notes/` 找到该笔记目录。
3. 选择正确的备份版本。
4. 将备份内容复制回原相对路径，保留原文件名。
5. 重新启动影音库，确认内容和封面正常。

单篇备份只包含 Markdown，不复制或删除附件。

## 恢复完整快照

1. 先关闭影音库窗口，并在 Obsidian 中停止编辑。
2. 打开需要恢复的快照目录。
3. 按 `manifest.json` 中的相对路径复制 `02-电影阅读/` 和需要的 `attachments/` 文件。
4. 不要删除快照目录，以便恢复失败时再次尝试。
5. 重新启动影音库并在 Obsidian 中抽查。

恢复操作会覆盖目标文件，因此程序没有提供一键恢复接口。

## 日志

日志文件为每日一个：

```text
.media-app/logs/media-vault-YYYYMMDD.jsonl
```

记录启动、停止、新建、修改、删除、外部修改冲突、备份结果、图片失败、批量升级和未预期 API 异常。日志不记录 Cookie、授权头、密码、密钥或完整系统路径。

## 回收目录

删除功能把 Markdown 移入 `.media-app/trash/`。如果 `image` 主封面位于
`attachments/` 且没有被其它媒体笔记引用，封面也会随笔记移入回收站；
共享封面保持原处。需要恢复时，把笔记和封面分别移回原目录。
