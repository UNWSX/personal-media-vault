# 2.5.8 测试报告

日期：2026-11-08

## 自动测试

| 项目 | 结果 |
|---|---|
| Python 单元与回归测试 | 139/139 通过 |
| JavaScript 语法检查 | 通过 |
| JavaScript 兼容逻辑测试 | 通过 |
| JavaScript 保存结果测试 | 通过 |
| Python `py_compile` | 通过 |
| .NET Release build | 沿用 2.5.7，0 警告、0 错误 |

## 实际抓取

《Dune: Part Two》自动查询结果：

```json
{"imdbScore":8.4,"rottenTomatoesScore":92,"metacriticScore":79}
```

《绝命毒师》第一季实际查询结果：

```json
{"imdbScore":8.7,"rottenTomatoesScore":86,"metacriticScore":73}
```

《绝命毒师》第二季实际查询结果：

```json
{"imdbScore":8.8,"rottenTomatoesScore":97,"metacriticScore":84}
```

《绝命毒师》第三季实际查询结果：

```json
{"imdbScore":8.7,"rottenTomatoesScore":100,"metacriticScore":89}
```

## 数据验证

- 当前总条目：299。
- 电影/剧集：201。
- 电影/剧集 Metacritic 已填写：129。
- 尚未取得有效 Metacritic：72。
- 未使用 `0` 分占位或相似作品代替缺失评分。
- 未补全清单：`<BACKUP_ROOT>\Metacritic补全-20261108-再次\Metacritic未补全清单.md`

## 创建接口实测

在临时 Vault 调用 `/api/create`，请求
`Dune: Part Two` 且启用 `autoRatings`，返回：

```json
{"imdbScore":8.4,"rottenTomatoesScore":92,"metacriticScore":79}
```

条目创建成功后立即包含三项外部评分，无需再运行批量补全。

