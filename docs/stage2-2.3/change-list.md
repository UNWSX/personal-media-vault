# 阶段 2.3 修改清单

基线提交：`96fc8cd`  
阶段范围：数据模型兼容层、普通编辑与前端字段冲突

## 新增文件

### `app/media_compat.py`

新增：

- `strict_bool()`
- `normalize_identity()`
- `validated_rating()`
- `default_progress_status()`
- `status_group()`
- `normalize_status()`
- `normalize_ratings()`
- `normalize_tags()`
- `build_item_updates()`

负责集中处理新旧评分、状态、身份字段、标签和普通写入校验。

### `app/web/compat_logic.js`

新增纯函数：

- `sameValue()`
- `conflictGroup()`
- `dirtyKeysBetween()`
- `mergeLatestWithLocal()`
- `threeWayDecision()`
- `threeWayGroupDecision()`

用于未捆绑框架的三方草稿合并和兼容组冲突判断。

### 自动测试

- `tests/test_media_compat.py`
- `tests/test_compat_logic.js`

## 修改文件

### `app/server.py`

`read_note()`：

- 改用阶段 2.2 受限 Frontmatter 解析。
- 接入状态、评分、身份、标签兼容映射。
- 返回 `normScore`、`scoreSource`、`statusGroup`、新旧评分字段、身份告警、`scoreStar` 等只读展示信息。
- 无效评分不自动变成 0。

`Library.load()`：

- 检测大小写不敏感的重复 `mediaId`。
- 标记所有关联条目只读。

`Library.update()`：

- 普通编辑改为受限 Frontmatter Patch。
- 写入前阻止未来 schema、非法身份和重复 `mediaId`。
- 通过兼容层校验和同步评分、状态、日期、平台、价格等字段。
- 保留 revision、备份、原子替换和外部修改检测。

`sync_callout_result()`：

- 只更新代码块之外、类型匹配、值可识别且唯一匹配的状态 Callout。
- 不修改带额外说明的状态行。
- 返回明确 `calloutWarning`。

`create_note()`：

- 新笔记写入 `schemaVersion: 2` 和 UUIDv4 `mediaId`。
- 写入可确定的 `progressStatus`、来源评分和 `startDate`。

豆瓣导入与游戏批量升级：

- Metacritic 写 `metacriticScore`。
- 豆瓣来源写 `doubanScore`。
- 同步旧 `score` 保持旧客户端兼容。

`fix_image()`：

- 使用受限 Patch 和相同身份只读规则。

### `app/web/app.js`

- 四键状态筛选。
- 分来源评分展示与当前主渠道评分编辑。
- 状态胶囊和旧版 `music` 开关。
- `baseItems` 与 `fieldConflicts` 草稿状态。
- 409 三方合并与冲突栏。
- 未解决字段冲突时阻止窗口关闭。
- 保存失败回滚可回滚的 UI 状态。
- 保存成功但 Callout 未同步时显示明确提示。
- 详情页显示非法身份、未知状态、评分告警和 Callout 告警。

### `app/web/index.html`

- 更新状态筛选按钮。
- 增加冲突栏。
- 加载 `compat_logic.js`。

### `app/web/style.css`

- 增加冲突栏、评分编辑和兼容警告样式。

### `app/web/save_outcome.js`

- 窗口关闭结果统计纳入未解决字段冲突。

### `tests/test_phase1.py`

- 增加 Callout 代码块、额外说明、零/多匹配、类型不匹配和 `music` 回归测试。
- 增加未来 schema、非法身份和重复 `mediaId` 只读测试。

### `tests/test_api_phase1.py`

- 增加状态与评分兼容写入测试。
- 增加非法 schema 的 API 序列化和写入阻止测试。
- 增加 `scoreStar` 旧字段读取验证。

### `tests/regress_real_copy.py`

- 适配 2.3 预期的 `progressStatus` 写入。
- 比较器只忽略兼容状态字段和状态 Callout 行，其余 Frontmatter、正文、BOM、行尾和末尾换行继续逐字节校验。
- 修复 Windows 控制台非 GBK 字符输出问题。

### `tests/test_save_outcome.js`

- 增加状态或评分队列部分成功、部分失败时关闭被阻止的断言。

### `README.md`

- 更新数据格式说明。
- 增加阶段 2.3 使用和兼容说明。

## 原有行为对照

| 场景 | 原有行为 | 2.3 行为 |
|---|---|---|
| 旧 `status` | 只返回布尔 | 继续返回布尔，并映射类型相关进度 |
| 外部评分 | 只共用 `score` | 保留旧字段，同时读取专用来源评分 |
| 409 | 只刷新最新条目 | 三方比较；同字段冲突要求用户确认 |
| Callout | 正则可能替换多个 | 唯一、代码块外、值可识别才更新 |
| 新笔记 | 无身份字段 | 写 `schemaVersion` 与 UUIDv4 `mediaId` |
| 身份异常 | 普通编辑仍可能写入 | 整篇只读并显示原因 |
| 标签 | 读取时简单合并 | 兼容层合并去重；本阶段不双写 |

## API 兼容性

- 所有原有 API URL 未改。
- `/api/item` 继续接受旧 `score`、`status`。
- `/api/library` 继续返回旧字段，并增加兼容字段。
- 新笔记继续提供旧客户端可读取的 `score` 和 `status`。
- 原有参数不会因本阶段被移除。
- 不兼容输入返回 HTTP 400 和明确错误。

## 未做事项

- 没有迁移工具。
- 没有批量写真实 Vault。
- 没有自动重算 `scoreStar`。
- 没有将 `tags` 和 `genre` 自动双写。
- 没有扩展 `music` 到新版进度模型。

