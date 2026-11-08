# 阶段 2.5：Vault 副本验收

状态：副本全量验收完成；真实 Vault 保持只读  
测试日期：2026-11-07

## 1. 测试边界

- 真实 Vault：`<OBSIDIAN_VAULT>`
- 副本根目录：系统隔离的 `.test-artifacts`
- 真实 Vault 只执行 Scan、Dry-run 和只读审计。
- Apply、Rollback、外部修改和故障注入只在完整副本执行。

## 2. 真实 Vault 只读 Scan

```text
files=295
movie=133
teleplay=64
game=92
book=6
schemaVersion missing=295
mediaId missing=295
YAML failures=0
duplicate mediaId files=0
score present=293
L1=0
L2=90
L3=205
status action=set_progressStatus 295
```

结论：当前历史评分缺少逐条来源证据，没有任何专用评分被自动迁移。

## 3. Dry-run

计划包含 295 篇：

```text
BASE_SCHEMA READY: 295
CALLOUT_STATUS READY: 51
CALLOUT_STATUS REVIEW_REQUIRED: 244
SCORE_REVIEW REVIEW_REQUIRED: 293 / 295
```

计划状态：

```text
READY_WITH_REVIEW=295
```

Dry-run 前后 295 个 Markdown 的内容哈希、大小和 mtime 完全一致。

## 4. 副本 Apply

```text
APPLIED=295
CONFLICT=0
ERROR=0
Post-audit ok=true
checkedFiles=295
```

验证结果：

- 295 个 schemaVersion=2。
- 295 个 mediaId 均为 UUIDv4 且全局唯一。
- 原有 295 个 status 完成位保持不变并与 progressStatus 一致。
- 51 个安全状态 Callout 同步；244 个带额外说明或找不到匹配的 Callout 保持原样。
- 293 个旧 score 全部保持原值；没有写入来源不明的评分字段。
- `scoreStar`、标题、封面、标签、未知字段、正文、双链和图片引用保持。
- 文件集合、文件名和目录结构不变。

独立审计：

```text
files=295 ids=295 failures=0
```

## 5. API 与看板

迁移后副本 API：

```text
/api/library count=295
schemaVersion=2 count=295
unique mediaIds=295
/api/migration/scan files=295
```

旧客户端兼容：

- 新建条目获得 schemaVersion 2。
- 只提交旧 `status` 时可正常保存并映射到 progressStatus。
- 可恢复删除继续工作。

Dataview 静态兼容统计：

```text
files=295
types: movie=133, teleplay=64, game=92, book=6
completed=239
incomplete=56
averageScore=31.3
scored=293
failures=0
```

## 6. Obsidian

Obsidian 已能从迁移后副本启动并读取文件树、笔记和 YAML 属性。

副本首次作为新 Vault 打开时出现“是否信任仓库作者并启用插件”的安全提示。该安全提示由用户本人在临时副本中确认；用户随后确认 `媒体筛选库6.0` Dataview 界面正常渲染。独立只读脚本也验证了相同的数据统计。

## 7. 全局回滚

```text
planState=ROLLED_BACK
RESTORED=295
all_original_bytes_restored=true
```

295 个 Markdown 与迁移前原始字节 SHA-256 完全一致。

## 8. 外部修改冲突

在另一份完整副本中，Dry-run 后修改第一篇笔记，再 Apply：

```text
APPLIED=294
CONFLICT=1
planState=APPLYING
```

被 Obsidian 修改的文件保持外部内容，旧 Plan 没有覆盖。

## 9. 真实 Vault 完整性

验收结束时再次比较：

```text
files=295
content_unchanged=true
mtime_unchanged=true
```

真实 Vault 没有被迁移、覆盖或部署。

## 10. 已知限制

- Obsidian 新副本的插件信任提示由人工确认，自动化没有代替该安全决定。
- 当前 295 篇历史条目没有 L1 逐条评分来源证据，因此没有专用评分的自动提升。
- 6 本旧图书没有 tags/genre；这是原有数据状态，不是迁移失败。
- 哈希比对和原子替换不能消除其他编辑器完全不遵守锁协议时的极短 TOCTOU 窗口。

