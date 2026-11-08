# 2.5.3 修改清单

- `app/server.py`
  - 新增 IMDb 官方建议接口查询。
  - 新增 IMDb GraphQL 标题、年份、类型和评分复核。
  - 新导入电影、剧集自动尝试 IMDb 评分。
- `app/batch_imdb.py`
  - 新增 IMDb 预览、缓存、报告和安全批量写入工具。
- `app/web/app.js`
  - 详情页单独显示 IMDb 评分。
  - 缺失的烂番茄或 IMDb 评分不生成评分模块。
- `tests/test_imdb_import.py`
  - 增加 IMDb 电影、剧集、年份拒绝、写入与正文保护测试。
- `README.md`
  - 增加 IMDb 批量工具说明。
