# 2.5.10 测试报告

日期：2026-11-08

## 根因

后台任务确实执行了搜索，但旧代码把：

```text
Breaking Bad Season 4
Stranger Things Season 1
```

作为系列名传入季查询。查询函数期望：

```text
Breaking Bad
Stranger Things
```

因此连续返回 `NO_SEASON_MATCH`，队列持续重试但拿不到评分。

## 修复验证

| 条目 | IMDb | Rotten Tomatoes | Metacritic |
|---|---:|---:|---:|
| 绝命毒师 第四季 | 9.0 | 100% | 96 |
| 怪奇物语 第一季 | 8.8 | 97% | 76 |

两条记录已通过修订号校验和原子写入完成更新。

## 完整回归

- Python：145/145 通过。
- JavaScript 语法检查：通过。
- JavaScript 兼容逻辑：通过。
- JavaScript 保存结果：通过。
- PyInstaller 服务构建：通过。
- 部署后 API：通过。
- 两条目标记录的评分均已实际读取确认。


