# 进度状态与正文 Callout 完整映射表

状态：2.1 冻结候选

目标阶段：2.3 后端与前端实现、2.4 迁移

## 1. 唯一状态枚举

### 电影 movie

| progressStatus | 英文 | 中文详情显示 | Callout 文本 | 四键筛选 |
|---|---|---|---|---|
| `planned` | Planned | 想看 | 未看 | 未开始 |
| `completed` | Completed | 已看 | 已看 | 已完成 |

电影不支持 `watching`、`paused`、`abandoned`。

### 剧集 teleplay

| progressStatus | 英文 | 中文详情显示 | Callout 文本 | 四键筛选 |
|---|---|---|---|---|
| `planned` | Planned | 想看 | 想看 | 未开始 |
| `watching` | Watching | 在看 | 在看 | 进行中 |
| `paused` | Paused | 搁置 | 搁置 | 进行中 |
| `completed` | Completed | 已看完全集 | 已看完全集 | 已完成 |

### 游戏 game

| progressStatus | 英文 | 中文详情显示 | Callout 文本 | 四键筛选 |
|---|---|---|---|---|
| `backlog` | Backlog | 待玩 | 待玩 | 未开始 |
| `playing` | Playing | 在玩 | 正在游玩 | 进行中 |
| `paused` | Paused | 搁置 | 搁置 | 进行中 |
| `completed` | Completed | 已通关 | 已通关 | 已完成 |
| `abandoned` | Abandoned | 已弃坑 | 已弃坑 | 进行中 |

`abandoned` 虽归入“进行中”聚合筛选，详情页和卡片角标必须显示“已弃坑”，不得显示“在玩”。

### 图书 book

| progressStatus | 英文 | 中文详情显示 | Callout 文本 | 四键筛选 |
|---|---|---|---|---|
| `planned` | Planned | 想读 | 想读 | 未开始 |
| `reading` | Reading | 在读 | 在读 | 进行中 |
| `paused` | Paused | 搁置 | 搁置 | 进行中 |
| `completed` | Completed | 已读完 | 已读完 | 已完成 |

## 2. 四键筛选

| 筛选按钮 | 匹配集合 |
|---|---|
| 全部 | 当前媒体类型所有条目 |
| 未开始 | `planned`、`backlog` |
| 进行中 | `watching`、`playing`、`reading`、`paused`、`abandoned` |
| 已完成 | `completed` |

“全部”不是状态值，不得写入 Frontmatter。

## 3. 旧 status 读取映射

严格请求：

```yaml
status: true
```

映射：

```yaml
progressStatus: completed
```

严格请求：

```yaml
status: false
```

| type | progressStatus |
|---|---|
| movie | planned |
| teleplay | planned |
| game | backlog |
| book | planned |

禁止把旧 `false` 推断为进行中状态。

## 4. 严格布尔校验

旧字段只接受未引用的小写布尔：

```yaml
status: true
status: false
```

以下不是合法兼容布尔：

```yaml
status: "true"
status: "false"
status: 1
status: 0
status: yes
status: no
status: on
status: off
```

非法值：

- 保留原文。
- 不推断 `progressStatus`。
- 不自动写 `status`。
- 报告 `INVALID_LEGACY_STATUS`。
- 详情页显示“状态需人工确认”。

## 5. 新旧字段读取优先级

1. `progressStatus` 存在且是当前媒体类型的合法枚举：以它为准。
2. 没有 `progressStatus`，`status` 是严格布尔：按类型兼容映射。
3. 两者都存在且语义一致：正常读取。
4. 两者都存在但语义冲突：以合法 `progressStatus` 为准，并报告 `STATUS_MISMATCH`。
5. `progressStatus` 存在但未知：保留未知值，显示“未知状态”，不得回退到 `status` 后覆盖。
6. `status` 非法：不影响合法 `progressStatus` 的读取，但不能自动同步覆盖非法旧值，需人工处理。
7. 两者都不合法或缺失：显示“状态未设置”，不得自动判定为 planned/backlog。

## 6. 新状态写入同步

`progressStatus` 的合法值必须同步旧字段：

```python
status = (progressStatus == "completed")
```

| progressStatus | 同步 status |
|---|---|
| planned | false |
| backlog | false |
| watching | false |
| playing | false |
| reading | false |
| paused | false |
| abandoned | false |
| completed | true |

规则：

- 新前端修改状态时必须同时提交 `progressStatus`，由兼容层计算 `status`。
- 不要求用户同时手动提交两个字段。
- 旧客户端只提交 `status` 时，兼容层按媒体类型生成 `progressStatus`。
- 同时提交且冲突时返回 400，不写任何状态字段。
- 未知 `progressStatus` 不得作为新值。
- 已有未知历史值在用户明确修改前保持原样。

## 7. API 入参矩阵

| `progressStatus` | `status` | 行为 |
|---|---|---|
| 合法 | 缺失 | 写入状态并按规则同步 `status` |
| 缺失 | 严格布尔 | 按类型映射并写入 `progressStatus` |
| 合法 | 严格布尔且一致 | 接受并写入两个字段 |
| 合法 | 严格布尔且冲突 | 拒绝，HTTP 400 |
| 非法 | 任意 | 拒绝 |
| 缺失 | 非法类型 | 拒绝 |
| 未知历史值（仅文件中） | 任意 | 只读提示，不覆盖 |

## 8. 状态保存和失败处理

- 点击状态胶囊后只提交状态字段。
- 保存期间禁止重复提交同一状态任务。
- 保存成功：采用服务器返回条目和 revision。
- 保存失败：保留用户选择或明确标记“未保存”，不得伪装成功。
- 409：保留本地状态草稿，刷新 `latest`，显示字段冲突。
- 同字段冲突需要用户选择“采用本地值/采用 Obsidian 值”。
- 用户未解决冲突前，不允许关闭整个桌面窗口。

## 9. Callout 状态同步

### 9.1 当前已观察格式

阶段 1 源码中的 `sync_callout()` 可识别：

```markdown
> **📝 状态**：未看完
```

和：

```markdown
> **🕹️ 状态**：未通关
```

2.3 只能在此类明确标记基础上扩展，不得假设所有用户 Callout 使用同一模板。

### 9.2 唯一匹配规则

允许的初始匹配模式必须同时满足：

1. 位于 Markdown 正文。
2. 行首为 Callout 引用标记 `>`。
3. 行内存在完整的加粗状态标签。
4. 标签为 `📝 状态` 或 `🕹️ 状态`。
5. 使用全角冒号 `：`。
6. 同一正文恰好一个匹配。
7. 冒号后允许零个或多个普通空格，空格保留。
8. 冒号后只接受映射表中的完整已知显示值。
9. 状态文本后面存在额外说明、标签、括号脚注或 “#” 内容时不自动修改。
10. CRLF 和 LF 都支持，更新后保持原换行。

概念模式：

```regex
(?m)^> \*\*(?:📝|🕹️) 状态\*\*：(.*)$
```

该模式仅作为规范候选，2.2 必须用真实样本和单元测试确认边界，不得直接声称覆盖所有历史格式。

### 9.3 安全更新

只替换冒号后的状态文本，行前缀、Emoji、加粗标签、冒号、换行和引用符保持不变。

示例：

```text
> **🕹️ 状态**：待玩
```

更新为：

```text
> **🕹️ 状态**：正在游玩
```

### 9.4 不得更新

- 0 个匹配。
- 2 个或更多匹配。
- 匹配位于代码块中。
- 匹配位于嵌套引用或未知模板。
- 状态文本包含用户额外说明而无法判断可替换范围。
- 目标标签与媒体类型明显不一致。
- 文件语法越界或正文解析不安全。
- 状态值后带额外用户说明。
- 同一 Callout 存在多个不同状态字段，无法确定目标。

此时保留正文原样并记录 `CALLOUT_NOT_UPDATED`。

### 9.5 Callout 映射

| type | progressStatus | 正文值 |
|---|---|---|
| movie | planned | 未看 |
| movie | completed | 已看 |
| teleplay | planned | 想看 |
| teleplay | watching | 在看 |
| teleplay | paused | 搁置 |
| teleplay | completed | 已看完全集 |
| game | backlog | 待玩 |
| game | playing | 正在游玩 |
| game | paused | 搁置 |
| game | completed | 已通关 |
| game | abandoned | 已弃坑 |
| book | planned | 想读 |
| book | reading | 在读 |
| book | paused | 搁置 |
| book | completed | 已读完 |

## 10. 自定义 Callout 与正文保护

- 不重写整个 Callout。
- 不标准化用户 Callout 标题。
- 不改变 `[!info]+/-` 折叠状态。
- 不删除自定义行。
- 不修改其他加粗字段。
- 不修改双链、图片、表格、代码块和正文段落。
- Callout 无法唯一更新时，Frontmatter 状态仍可保存，但必须警告“正文状态未同步”。

## 11. 列表卡片和详情显示

卡片状态角标：

| 状态组 | 角标含义 |
|---|---|
| 未开始 | 显示计划状态 |
| 进行中 | 显示具体进行中状态 |
| 已完成 | 显示完成 |
| 未知 | 显示需确认 |

详情页按媒体类型只显示合法胶囊按钮，不跨类型混用。

## 12. 状态测试用例

| 用例 | 预期 |
|---|---|
| movie planned | 未看/未开始/status false |
| movie completed | 已看/已完成/status true |
| teleplay watching | 在看/进行中/status false |
| teleplay paused | 搁置/进行中/status false |
| game backlog | 待玩/未开始/status false |
| game playing | Callout 正在游玩/status false |
| game abandoned | 已弃坑/进行中角标/status false |
| book reading | 在读/status false |
| status true 无 progress | completed |
| game status false 无 progress | backlog |
| movie status false 无 progress | planned |
| 两个字段冲突 | 400，拒绝 |
| progress 未知 | 保留并警告 |
| status "true" | 非法并警告 |
| Callout 0 匹配 | 正文不变并警告 |
| Callout 2 匹配 | 正文不变并警告 |
| 自定义 Callout | 不修改 |
