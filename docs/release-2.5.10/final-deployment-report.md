# 2.5.10 剧集评分查询修复与部署报告

执行日期：2026-11-08  
源码提交：`203afd5`  
真实 Vault：`<OBSIDIAN_VAULT>`

## 问题确认

后台评分任务确实执行过。队列记录显示：

- 《绝命毒师 第四季》重试 4 次，缺少 IMDb、Rotten Tomatoes。
- 《怪奇物语 第一季》重试 4 次，缺少 Rotten Tomatoes。

失败原因是季查询收到：

```text
Breaking Bad Season 4
Stranger Things Season 1
```

而季查询函数需要系列基名：

```text
Breaking Bad
Stranger Things
```

旧实现把带季号的原始标题直接当作系列名，因此返回
`NO_SEASON_MATCH`。任务有搜索，但搜索身份参数错误。

## 修复

- 在调用 IMDb、Rotten Tomatoes、Metacritic 季查询前提取系列基名。
- 使用“系列基名 + 独立季号 + 发布年份”查询。
- 新增带 `Season N` 原标题的自动评分回归测试。

## 实际修复结果

| 条目 | IMDb | Rotten Tomatoes | Metacritic |
|---|---:|---:|---:|
| 绝命毒师 第四季 | 9.0 | 100% | 96 |
| 怪奇物语 第一季 | 8.8 | 97% | 76 |

两条记录均通过修订号校验和原子写入写回 Markdown。

## 验证

- Python 自动测试：145/145 通过。
- JavaScript 语法、兼容逻辑和保存结果测试：通过。
- PyInstaller 服务构建：通过。
- 部署后 API：正常。
- 两条记录重新读取后评分完整。

## 部署

已部署：

```text
<OBSIDIAN_VAULT>\.media-app
<OBSIDIAN_VAULT>\.media-app-stage1
```

立即回退目录：

```text
<OBSIDIAN_VAULT>\.media-app.pre-2.5.10-20261108-1640
<OBSIDIAN_VAULT>\.media-app-stage1.pre-2.5.10-20261108-1640
```

## 交付文件

- 正式安装包：
  `<APP_ROOT>\影音库-2.0-2.5.10-正式版-20261108.zip`
  - SHA-256：`a2f81aea4fbd4b90671355a05c783347b264129d5954236a5f2c4abfffd659b7`
- 完整源码：
  `<APP_ROOT>\影音库-2.5.10-完整源码-203afd5.zip`
  - SHA-256：`6977c8beb41815114417c775d8d9c9102fd3829badb714d30eae6e4dbc1db7b0`

本次未执行阶段 2 数据迁移，未改变正文、文件名、目录或 Obsidian 链接。

