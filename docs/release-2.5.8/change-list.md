# 2.5.8 修改清单

- `app/server.py`
  - 增加 `auto_lookup_external_ratings()`。
  - 新建/导入时自动补齐 IMDb、Rotten Tomatoes、Metacritic；
    动画额外查询 Bangumi。
  - 优先使用作品 ID，失败后回退到严格标题/年份查询。
  - 新建条目支持保存 `originalTitle`。
  - Wikidata 支持系统代理、限速重试和明确请求错误。
  - 豆瓣导入自动启用评分补全。
- `app/batch_enhanced_ratings.py`
  - 缓存只复用 `matched`，不再永久复用旧的失败结果。
- `app/web/app.js`
  - 手动创建请求启用 `autoRatings`。
- `tests/test_enhanced_ratings.py`
  - 增加编号优先自动评分、创建写入、豆瓣导入和失败缓存重试测试。

不涉及阶段 2 数据迁移。

