# 影音库 2.0 · 2.5.10

发布日期：2026-11-08

## 修复内容

- 修复带季剧集自动评分失败的问题。
- 当条目标题或原始标题包含 `Season N`、`第N季` 时，先提取系列基名，
  再调用 IMDb 季均分和 Rotten Tomatoes 季评分查询。
- 修复前会把 `"Breaking Bad Season 4"` 当作系列名，导致
  `NO_SEASON_MATCH`。
- 修复后正确使用 `"Breaking Bad"` + 季号查询。

## 实际结果

- 《绝命毒师》第四季：
  IMDb 9.0、Rotten Tomatoes 100%、Metacritic 96。
- 《怪奇物语》第一季：
  IMDb 8.8、Rotten Tomatoes 97%、Metacritic 76。

本版继续使用持久化评分队列、后台抓取和自动重试。

