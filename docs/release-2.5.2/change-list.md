# 2.5.2 修改清单

## 源码

- `app/server.py`
  - 将失效的 Rotten Tomatoes 私有搜索接口替换为当前公开 Algolia 搜索索引。
  - 新增精确身份匹配、候选歧义判断和网络错误分类。
  - 豆瓣详情结果增加 `originalTitle` 读取。
  - 豆瓣插件 `loginCookiesContent` 可用于本地请求。
- `app/batch_rotten_tomatoes.py`
  - 新增批量预览、缓存、报告和安全执行工具。
  - 写入复用 `Library.update()`、修订号检查、原子替换和写后校验。
- `README.md`
  - 增加批量工具使用说明。

## 测试

- `tests/test_api_phase1.py`
  - 更新 Rotten Tomatoes 查询测试。
  - 增加年份拒绝、候选歧义和豆瓣 Cookie 回归测试。
- `tests/test_rotten_tomatoes_import.py`
  - 增加预览、执行、正文保护、已有值跳过、外部修改冲突和标题提取测试。

## 数据

- 只修改真实 Vault 的 `02-电影阅读/` 下 93 篇电影/剧集 Markdown。
- 每篇只新增 `rottenTomatoesScore`。
- 不修改附件、目录结构、文件名、正文和 Obsidian 链接。
