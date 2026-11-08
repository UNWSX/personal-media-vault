# 2.5.9 修改清单

- `app/rating_queue.py`
  - 新增持久化评分任务队列。
  - 支持去重、重试、完成状态和中断恢复。
- `app/server.py`
  - 条目创建接口只完成本地安全写入后立即返回。
  - 后台单线程处理评分任务，避免同一文件并发写入。
  - 通过 `Library.update()`、修订号和原子写入更新评分。
  - 增加 `/api/ratings` 和 `/api/retry-ratings`。
  - 豆瓣导入不再同步等待 IMDb、Rotten Tomatoes、Metacritic。
  - 重复导入时为已有条目重新排队缺失评分。
- `app/web/app.js`
  - 增加评分任务提示和完成后的自动刷新。
- `tests/test_rating_queue.py`
  - 增加队列持久化、去重、重试、中断恢复和完成状态测试。
- `tests/test_enhanced_ratings.py`
  - 增加立即创建、后台评分写入、失败重试和重复导入测试。

