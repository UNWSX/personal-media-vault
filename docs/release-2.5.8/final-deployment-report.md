# 2.5.8 自动评分与补全部署报告

执行日期：2026-11-08  
源码提交：`eee9df7`  
真实 Vault：`<OBSIDIAN_VAULT>`

## 自动评分

新建或导入条目时自动执行：

- 豆瓣导入保留豆瓣评分。
- 电影/剧集查询 IMDb、Rotten Tomatoes、Metacritic。
- 动画电影/剧集额外查询 Bangumi。
- 已有字段不覆盖。
- 自动抓取失败不阻断创建，也不写猜测值或 `0` 分占位。
- 优先使用 Wikidata 作品 ID；没有既有 ID 时以标题和年份解析，
  随后使用 IMDb `P345`、Rotten Tomatoes `P1258`、
  Metacritic `P1712` 读取评分。

实测《绝命毒师》第一季：

```json
{"imdbScore":8.7,"rottenTomatoesScore":86,"metacriticScore":73}
```

实测《绝命毒师》第二季：

```json
{"imdbScore":8.8,"rottenTomatoesScore":97,"metacriticScore":84}
```

实测《绝命毒师》第三季：

```json
{"imdbScore":8.7,"rottenTomatoesScore":100,"metacriticScore":89}
```

临时 Vault 调用 `/api/create` 创建 `Back to the Future`，立即返回：

```json
{"imdbScore":8.5,"rottenTomatoesScore":93,"metacriticScore":88}
```

## Metacritic 补全

- 第一批稳定编号补全：29 条，全部成功。
- 第二批使用豆瓣英文原名补全：37 条，全部成功。
- 新增《绝命毒师》第一至第三季外部评分：3 条。
- 本轮合计补入有效 Metacritic：69 条。
- 写入冲突：0。
- 写入失败：0。

电影/剧集 Metacritic 覆盖由 `60` 条提升到 `129` 条。

## 未补全记录

仍有 `72` 条电影/剧集未取得有效 Metacritic。详细逐条原因：

```text
<BACKUP_ROOT>\Metacritic补全-20261108-再次\Metacritic未补全清单.md
```

主要原因是：

- 未取得可靠 Wikidata 作品编号或严格标题/年份匹配。
- 少数新片/季页面尚无有效评论分。
- 评论分为空或只有 `0` 分占位时，系统拒绝写入。
- 没有用相似作品、其他季或不同年份的评分互相顶替。

## 验证

- Python 自动测试：139/139 通过。
- JavaScript 语法、兼容逻辑、保存结果测试：通过。
- PyInstaller 服务构建：通过。
- PyInstaller 评分增强工具构建：通过。
- 临时 Vault 新建接口自动评分：通过。
- 部署后 API：299 条，0 警告。

## 部署

已部署：

```text
<OBSIDIAN_VAULT>\.media-app
<OBSIDIAN_VAULT>\.media-app-stage1
```

立即回退目录：

```text
<OBSIDIAN_VAULT>\.media-app.pre-2.5.8-20261108-1530
<OBSIDIAN_VAULT>\.media-app-stage1.pre-2.5.8-20261108-1530
```

## 交付文件

- 正式安装包：
  `<APP_ROOT>\影音库-2.0-2.5.8-正式版-20261108.zip`
  - SHA-256：`f5aed7e62b9a96a58b2d3fc33e6ecfa9617b87cb185cc671892f3f723efc78cd`
- 完整源码：
  `<APP_ROOT>\影音库-2.5.8-完整源码-eee9df7.zip`
  - SHA-256：`48ae8a2ce3b0e30542949fd1ec5f36c563bbf7cf81b528c172bd802b8c180b76`

## 数据安全

- 本版没有改变 Markdown 目录、文件名、正文或 Obsidian 链接。
- 所有补全通过修订号校验和原子写入完成。
- 未执行阶段 2 数据迁移。

