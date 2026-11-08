# 2.5.10 修改清单

- `app/server.py`
  - `auto_lookup_external_ratings()` 在季条目查询前提取系列基名。
  - IMDb、Rotten Tomatoes、Metacritic 的季级查询统一使用基名和季号。
- `shell/Program.cs`
  - 独立版固定使用程序旁 `数据` 目录作为 Vault。
  - 服务复用检查现在会比较独立版数据目录，避免误连正在运行的 Obsidian 版服务。
- `tests/test_enhanced_ratings.py`
  - 增加带 `Season N` 原始标题的自动评分回归测试。

本次不改 Markdown 结构、API 或界面布局。

