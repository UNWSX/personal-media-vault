# 个人影音库 2.0 正式迁移与部署报告

执行日期：2026-11-07  
源码提交：`786aba8`  
真实 Vault：`<OBSIDIAN_VAULT>`

## 1. 执行前备份

安装归档：

```text
<BACKUP_ROOT>\影音库-现有安装-正式迁移部署前-20261107-185322.zip
SHA-256: 172edbfcd951389c7103278d794979d97d7a77468da171e7f8cf58864a8f1410
```

归档包含 `.media-app` 和 `.media-app-stage1` 的应用文件、日志、备份、回收站与配置，排除仅用于运行时的 WebView2 缓存和 `__pycache__`。

Markdown 归档：

```text
<BACKUP_ROOT>\影音库-Markdown-正式迁移前-20261107-185322.zip
SHA-256: 3c4d8f1e4c2897712ec8f0dfed95ceb1e89c9a5483db19c8f7e6799f99601ec9
```

包含迁移前 295 篇 Markdown 和逐文件 SHA-256 manifest。

## 2. 正式迁移

```text
Plan ID: 188189d2-bbd0-41e9-b86a-1bf073903469
Plan state: APPLIED
Applied: 295
Conflict: 0
Error: 0
Post-audit: OK
```

迁移专用快照：

```text
<OBSIDIAN_VAULT>\.media-app-stage1\backups\migrations\188189d2-bbd0-41e9-b86a-1bf073903469
```

结果：

- 295 篇均包含 `schemaVersion: 2`。
- 295 个 `mediaId` 均为唯一 UUIDv4。
- 295 篇 `progressStatus` 与旧 `status` 一致。
- 51 个安全状态 Callout 同步。
- 244 个不满足唯一/可识别条件的 Callout 保持原文。
- 293 个旧 `score` 和全部 `scoreStar` 保持不变。
- 没有 L1 来源证据，因此没有自动写入专用外部评分。
- 正文、标题、封面、标签、未知字段、双链和目录结构审计 0 异常。

## 3. 部署

已覆盖两个现有安装入口：

```text
<OBSIDIAN_VAULT>\.media-app\影音库.exe
<OBSIDIAN_VAULT>\.media-app-stage1\影音库.exe
```

`<APP_ROOT>\影音库.lnk` 和 `<APP_ROOT>\影音库-2.0-阶段1临时版.lnk` 保持原目标路径，现在都启动新版。

部署后启动验证：

```text
.media-app:       count=295, schemaVersion2=295, uniqueMediaIds=295
.media-app-stage1: count=295, schemaVersion2=295, uniqueMediaIds=295
```

## 4. 回退资源

部署时原目录已原子改名为：

```text
.media-app.pre-deploy-20261107-190000
.media-app-stage1.pre-deploy-20261107-190000
```

以上路径仅作为立即回退副本保留。正式安装归档仍是独立 ZIP。

如新版无法启动：

1. 停止影音库进程。
2. 将当前新版目录改名保留。
3. 将对应 `pre-deploy` 目录改回原名称。

如需要回退数据迁移，只能使用原 Plan 的 Rollback 命令；迁移后已经产生的用户编辑会导致对应文件标记为 `RESTORE_CONFLICT`，不会被覆盖。

## 5. 验证

- Python 自动测试：102/102。
- JavaScript 检查：通过。
- .NET Release：0 警告、0 错误。
- 真实 Vault 只读/YAML 审计：295/295。
- 正式迁移后独立正文审计：295/295，0 异常。
- Dataview 数据统计：0 异常。
- Obsidian 插件界面：用户已在临时完整副本中确认正常。

