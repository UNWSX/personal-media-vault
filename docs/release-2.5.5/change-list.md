# 2.5.5 修改清单

- `app/server.py`
  - 新增 Wikidata 搜索与实体校验。
  - 新增 IMDb ID、Rotten Tomatoes ID 提取。
  - 新增按稳定外部 ID 获取评分。
  - Wikidata 搜索语言根据中文、日文或英文自动选择。
- `app/batch_enhanced_ratings.py`
  - 优先使用 Wikidata 外部 ID 补全缺失评分。
  - 增加 `--missing-only`。
  - Bangumi 主标题失败时使用豆瓣原始标题重试。
  - 增加特摄/系列剧场版的父系列英文标题派生。
- `tests/test_enhanced_ratings.py`
  - 增加 Wikidata 实体解析和稳定 ID 评分测试。
