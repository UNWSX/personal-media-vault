# 影音库 2.0 · 2.5.4 正式版

发布日期：2026-11-07

## 本版内容

- 分季剧集按季号查询 IMDb 与 Rotten Tomatoes，不再把整部剧评分写入第一季。
- IMDb 季评分使用该季全部有评分剧集的平均值。
- 动画通过 Bangumi 与 AniList 的日文名、罗马字名和英文名进行别名转换。
- 新增 `bangumiScore` 的批量补全与详情页显示。
- 标题排序改为系列分组、组内按发行日期从旧到新。
- 标题搜索有命中时，不再混入仅在剧情简介中提及角色的结果。
- 网络评分排序优先 IMDb，其次豆瓣，最后按媒体类型回退 Metacritic/旧评分。
- 缺失的豆瓣、烂番茄、IMDb 或 Bangumi 评分模块直接隐藏。

## 真实 Vault 批量结果

197 条电影/剧集预演结果：

- 需要更新：76
- IMDb 更新：41
- Rotten Tomatoes 更新：25
- Bangumi 更新：59
- 分季条目：26
- 写入冲突：0
- 写入失败：0
- 正文/Callout 字节级变化：0

瑞克和莫蒂第 1–9 季已分别写入独立评分；第 5 季 Rotten Tomatoes 在补充解析后写入
86%。

## 兼容性

- API 版本仍为 `2.0.0`，协议版本仍为 `1`。
- 目录、文件名、正文、Callout 和 Obsidian 链接不变。
- 继续复用 `imdbScore`、`rottenTomatoesScore`、`bangumiScore` 字段。
