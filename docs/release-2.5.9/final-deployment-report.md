# 2.5.9 异步评分与部署报告

执行日期：2026-11-08  
源码提交：`95ad8f0`  
真实 Vault：`<OBSIDIAN_VAULT>`

## 创建流程

新条目现在按以下顺序处理：

1. 先在临时文件中生成 Markdown。
2. 校验内容后原子替换到正式目录。
3. 写入成功后立即向页面返回成功。
4. 将缺失的外部评分加入持久化队列。
5. 后台工作线程逐个抓取并安全写回。

评分队列位置：

```text
<OBSIDIAN_VAULT>\.media-app\ratings\queue.json
```

队列支持：

- 页面关闭后继续保留。
- 服务重启后恢复。
- 中断的 `running` 任务自动转回 `retry`。
- 同一路径任务合并，不会因重复添加产生多份并发写入。
- 5 秒、15 秒、1 分钟、5 分钟、15 分钟、1 小时后递增退避。
- Markdown 更新仍使用修订号校验和原子替换。

## 实测

关闭系统代理后在临时 Vault 创建 `The Dark Knight`：

- 创建接口返回耗时：`74ms`
- 初始终止状态：已创建，评分任务 `queued`
- 后台完成：IMDb `9.1`、Rotten Tomatoes `94%`、Metacritic `85`

另一次关闭代理创建 `The Matrix`：

- 创建接口返回耗时：`84ms`
- Markdown 即时成功
- 队列任务持久化并成功恢复

## 外部评分覆盖率

- 当前媒体总数：300。
- 电影/剧集：201。
- 电影/剧集 Metacritic：129。
- 仍无有效 Metacritic：72。

未补全逐条清单：

```text
<BACKUP_ROOT>\Metacritic补全-20261108-再次\Metacritic未补全清单.md
```

原因主要是没有取得可靠作品编号或严格标题匹配，以及少数新片/新季暂无有效评论分。系统没有使用相似作品或其他季度代替。

## 测试

- Python 自动测试：144/144 通过。
- JavaScript 语法检查：通过。
- JavaScript 兼容逻辑测试：通过。
- JavaScript 保存结果测试：通过。
- PyInstaller 服务与评分增强工具构建：通过。
- 部署后 API：300 条，0 警告。
- `/api/ratings`：可读取持久化队列。

## 部署

已部署：

```text
<OBSIDIAN_VAULT>\.media-app
<OBSIDIAN_VAULT>\.media-app-stage1
```

立即回退目录：

```text
<OBSIDIAN_VAULT>\.media-app.pre-2.5.9-20261108-1545
<OBSIDIAN_VAULT>\.media-app-stage1.pre-2.5.9-20261108-1545
```

## 交付文件

- 正式安装包：
  `<APP_ROOT>\影音库-2.0-2.5.9-正式版-20261108.zip`
  - SHA-256：`851b0a7f48d633c8eea3ee2204d52db9b58d11b7f29a4aea5e260c5b535cc65b`
- 完整源码：
  `<APP_ROOT>\影音库-2.5.9-完整源码-95ad8f0.zip`
  - SHA-256：`beef7931f85339187f446cbd72f25ce2f286e11734149177d133e4e1ba3f95c2`

## 数据安全

- 本版没有执行阶段 2 数据迁移。
- 没有改变媒体目录、文件名、正文或 Obsidian 链接。
- 所有评分更新均通过 `Library.update()` 的修订号校验和原子写入。

