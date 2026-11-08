# 2.5.6 修改清单

## 评分身份与抓取

- `app/server.py`
  - 增加电影/剧集 Metacritic 搜索与季页读取。
  - 增加 Wikidata `P1712`，并复用 `P345`、`P1258`。
  - 增加按既有 Wikidata QID 刷新作品编号。
  - 按季度从 IMDb 作品编号读取季均分。
  - 严格校验 Metacritic 季名，拒绝续作前缀误配。
  - 拒绝 Metacritic 未收录时返回的 `0` 分占位。
- `app/batch_enhanced_ratings.py`
  - 增加 Metacritic-only、分段扫描、别名文件与分段缓存。
  - 编号优先、标题别名回退，并记录每条匹配尝试。
  - 支持分部剧集的 IMDb 季均分。
- `app/rating_aliases.py`
  - 保存人工核对的作品编号与 Metacritic 标题回退。
- `app/media_seasons.py`
  - 增强中文、罗马数字、Season 和 Part 解析。

## 前端

- `app/web/app.js`
  - 增加 Metacritic 评分来源。
  - 5 个评分使用上 3、下 2 的独立居中行。
- `app/web/style.css`
  - 1－4 个评分保持正式部署版的单行布局。
  - 5 个评分采用 `48px` 行内间距。
- `app/web/compat_logic.js`
  - 改进系列标题和分部排序 key。

## 测试

- `tests/test_enhanced_ratings.py`
  - 增加 Wikidata ID 复用、季页、严格季名、零分拒绝、编号优先计划测试。
- `tests/test_compat_logic.js`
  - 增加罗马数字与 Part 系列标题测试。

## 未改变

- 未更换后端或前端技术栈。
- 未修改媒体目录、文件名、正文和 Obsidian 双链。
- 未引入数据库或阶段 2 数据迁移。

