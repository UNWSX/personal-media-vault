# 2.5.9 测试报告

日期：2026-11-08

## 自动测试

| 项目 | 结果 |
|---|---|
| Python 单元与回归测试 | 144/144 通过 |
| JavaScript 语法检查 | 通过 |
| JavaScript 兼容逻辑测试 | 通过 |
| JavaScript 保存结果测试 | 通过 |
| Python `py_compile` | 通过 |

## 异步创建验证

在关闭代理的临时 Vault 中创建 `The Matrix`：

- `/api/create` 返回耗时：84 ms。
- Markdown 立即创建成功。
- IMDb、烂番茄、Metacritic 初始为空。
- 评分任务写入持久化队列，状态为 `running`。
- 服务停止并重启后，队列任务自动恢复。

## 队列测试

- 新建任务。
- 相同路径任务合并字段。
- 服务重启后恢复 `running` 任务。
- 网络失败后记录 `attempts` 和 `nextAttemptAtTs`。
- 成功任务写入完成状态。

## 评分结果

- 电影/剧集 Metacritic：129/201。
- 未取得有效评分：72。
- 未补全原因清单：
  `<BACKUP_ROOT>\Metacritic补全-20261108-再次\Metacritic未补全清单.md`

