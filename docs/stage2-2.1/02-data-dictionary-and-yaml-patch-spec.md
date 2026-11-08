# 数据字典与 YAML 局部 Patch 规格说明

状态：2.1 冻结候选

适用范围：未来 2.2/2.3/2.4 的实现依据

依赖限制：仅 Python 标准库

## 1. 通用约定

- 正式存储：Markdown 文件。
- Frontmatter：文件开头 `---` 与下一个独占行 `---` 之间。
- 编码：严格 UTF-8。
- BOM：允许并原样保留。
- 换行：LF 或 CRLF，未修改区域不得转换。
- 路径：POSIX 相对路径只用于计划、日志和 API；实际文件访问必须解析为 Vault 内绝对路径。
- 未知字段：保留。
- 计算字段：不写入 Markdown，除非明确标为持久化。

## 2. 字段存在性语义

| 情形 | 含义 |
|---|---|
| 字段不存在 | 未提供，采用相关默认或兼容推导 |
| `key:` | 空值；不得擅自改写为 null 或空字符串 |
| `key: null` | 明确 YAML null |
| `key: ""` | 明确空字符串 |
| `key: []` | 明确空列表 |
| `REMOVE_FIELD` | Patch 内部删除指令，不产生任何字段行 |

不得将 Python `None` 同时解释为“删除字段”和“写入 YAML null”。

## 3. 身份和版本字段

| 字段 | 类型 | 范围/格式 | 持久化 | 必填 | 默认 |
|---|---|---|---|---|---|
| `schemaVersion` | Integer | `2` | 是 | 新笔记和迁移后笔记必填 | 新建时 2 |
| `mediaId` | String | UUIDv4，小写连字符 | 是 | 新笔记和迁移后笔记必填 | 新建时生成 |

规则：

- `mediaId` 通过 `str(uuid.uuid4())`。
- 修改标题、文件名、目录、类型不改变 ID。
- 已有合法 UUIDv4 保留。
- 非 v4 UUID 或格式非法：保留原文，人工处理，不自动替换。
- 重复 ID：所有关联文件标记 `DUPLICATE_MEDIA_ID`。
- `schemaVersion > 2`：未来版本，只读。
- 缺失 `schemaVersion` 或值为旧版本：可进入迁移计划。
- 类型错误、重复字段或未知未来版本：跳过自动写入。

## 4. 基础媒体字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | String | 是 | 标题；缺失时当前代码会回退文件名，但迁移不得自动补写 |
| `originalTitle` | String | 否 | 原文标题 |
| `aliases` | List[String] | 否 | 其他名称 |
| `type` | String | 是 | `movie`、`teleplay`、`game`、`book`；其他历史值保留但默认跳过自动迁移 |
| `year` | Integer | 否 | 四位年份 |
| `image` | String | 否 | 现有本地相对路径或允许的远程图片地址 |
| `tags` | List[String] | 否 | 标签；不得与 `genre` 自动互相删除 |
| `comment` | String | 否 | 个人评价；支持块标量 |

`music` 是当前源码可能保留的历史类型，但本冻结模型未定义其新版状态和评分规则：

- 保留旧读取、搜索、创建和旧 `status` 二元状态能力。
- 不自动添加 `progressStatus`、专用评分或 `normScore` 规则。
- 自动迁移默认跳过并报告，不删除、不改类型。

## 5. 标识字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `doubanId` | String | 豆瓣 ID；保留前导零 |
| `imdbId` | String | IMDb ID；按字符串保存 |
| `bangumiId` | String | Bangumi ID |
| `steamAppId` | String | Steam appid；按字符串保存 |

禁止仅因存在这些 ID 就断定旧 `score` 来源。

## 6. 日期字段

| 字段 | 类型 | 格式 | 说明 |
|---|---|---|---|
| `datePublished` | String | `YYYY-MM-DD`，可保留旧格式 | 发行/出版日期 |
| `startDate` | String | `YYYY-MM-DD` | 开始日期 |
| `finishTime` | String | `YYYY-MM-DD` | 完成日期 |
| `purchaseDate` | String | `YYYY-MM-DD` | 游戏购买日期 |
| `currentDate` | String | 现有格式原样保留 | 历史创建/导入辅助字段 |
| `dateModified` | String | 现有格式原样保留 | 历史辅助字段 |

空日期不自动删除。新日期写入必须是合法 ISO 日期；历史非标准值只告警，不强制重写。

## 7. 评分字段

| 字段 | 类型 | 合法范围 | 持久化 | 步长/精度 |
|---|---|---|---|---|
| `score` | Number | 原量纲 | 是 | 不改变原值 |
| `myScore` | Decimal | 0–10 | 是 | 0.5 |
| `doubanScore` | Decimal | 0–10 | 是 | 最多 1 位小数 |
| `metacriticScore` | Integer | 0–100 | 是 | 1 |
| `imdbScore` | Decimal | 0–10 | 是 | 不超过 1 位小数 |
| `bangumiScore` | Decimal | 0–10 | 是 | 不超过 1 位小数 |
| `normScore` | Decimal | 0–10 或 null | 否 | 计算字段 |

禁止：

- NaN、Infinity、负数、超范围。
- 通过截断、取整或四舍五入修复非法值。
- 将非法值默认为 0。
- 将 `normScore` 写入 Markdown。

## 8. 进度状态字段

| 字段 | 类型 | 持久化 | 说明 |
|---|---|---|---|
| `status` | Boolean | 是 | 严格 `true`/`false` |
| `progressStatus` | String enum | 是 | 按媒体类型限制 |

`progressStatus` 枚举：

- movie：`planned`、`completed`
- teleplay：`planned`、`watching`、`paused`、`completed`
- game：`backlog`、`playing`、`paused`、`completed`、`abandoned`
- book：`planned`、`reading`、`paused`、`completed`

未知枚举保留原文，不自动回退或覆盖。

## 9. 旧字段兼容表

| 旧字段 | 类型/形式 | 新模型处理 |
|---|---|---|
| `id` | String | 保留；不能自动视为 `doubanId` |
| `score` | Number | 保留原量纲 |
| `scoreStar` | String | 保留历史显示字段，不作为权威评分 |
| `status` | Boolean | 保留并与 `progressStatus` 双向兼容 |
| `genre` | List[String] | 读取时与新 `tags` 合并去重；持久化位置保留 |
| `country` | List[String] | 保留 |
| `director` | String/List | 保留；运行时映射 creator |
| `author` | String/List | 保留；运行时映射 creator |
| `publisher` | String | 保留；图书和游戏含义保持原结构 |
| `actor` | String/List | 保留 |
| `desc` | String | 保留 |
| `platform` | List[String] | 保留 |
| `price` | Number | 保留 |
| `pages` | Integer/String | 保留；非法值告警 |
| `isbn` | String | 保留前导零 |
| `series` | String | 保留 |
| `translator` | String/List | 保留 |
| `aliases` | List | 保留 |
| `imageDataurl` | String | 保留历史字段，不自动删除 |
| `currentDate` | String | 保留 |
| `dateModified` | String | 保留 |
| 任意未知字段 | 任意可安全保留的文本范围 | 原样保留 |

## 10. 字段关系

- `mediaId` 是唯一稳定身份；路径和标题只是定位属性。
- `schemaVersion` 描述整篇文档结构，不描述单条缓存。
- `myScore` 与外部评分相互独立。
- `normScore` 只由 `metacriticScore`、`doubanScore` 或兼容旧 `score` 运行时计算。
- `progressStatus` 是语义状态；`status` 只是完成兼容位。
- `tags` 与 `genre` 在读取层合并，但不得自动迁移删除任一字段。
- 当前数据中 `genre` 主要用于游戏题材，`tags` 主要用于其它媒体，不能确认两者语义完全相同。
- 2.3 只提供归一化读取、筛选和展示；标签编辑不纳入本阶段必做范围。
- 新建游戏继续写 `genre`，其它类型继续写 `tags`；未来统一标签编辑必须另行冻结差量写回规则。
- 日期字段不影响评分来源或状态推断。

## 10.1 默认值与缺失值

| 字段类别 | 缺失时的处理 |
|---|---|
| 新笔记 `schemaVersion` | 新建时必须写 `2` |
| 新笔记 `mediaId` | 新建时必须生成 UUIDv4 |
| 旧笔记身份字段 | 保持缺失；只能由 Dry-run/Plan 建议添加 |
| `title` | 读取层可回退文件名用于显示；不自动写回 |
| 可选字符串 | 不创建默认空字段 |
| 可选数字/评分 | 读取层返回 null；不默认写 0 |
| `myScore` | 缺失表示未评分 |
| `status` | 缺失且无 `progressStatus` 表示状态未设置，不自动猜 planned/backlog |
| `progressStatus` | 只有合法旧 `status` 存在时才在兼容层映射 |
| `tags`/`genre` | 读取层可为空列表；不自动写 `[]` |
| `platform`/`country` | 读取层可为空列表；不自动写 `[]` |
| `comment` | 缺失等同于无评价；不得自动写成块标量 |
| `aliases` | 缺失表示未提供，不自动创建 |
| `finishTime`/`startDate` | 缺失表示未记录，不根据 status 推断 |
| `normScore` | 每次运行时重新计算，不缓存为正式数据 |

`scoreStar` 不是权威评分。只读代码确认：当前新建流程对非游戏使用 `clamp(round(score / 2), 1, 5)`，对大于 10 的游戏分数使用 `clamp(round(score / 20), 1, 5)`；但当前 `Library.update()` 修改已有 `score` 时不会重算该字段。迁移不得重算历史值。2.3 是否在旧 `score` 写入后同步 `scoreStar`，必须在模板和 Dataview 回归后另行决定。

## 11. REMOVE_FIELD、None 和空字符串

```python
REMOVE_FIELD = object()
```

| Patch 输入 | 结果 |
|---|---|
| `REMOVE_FIELD` | 删除字段整体及其从属行 |
| `None` | 写 `key: null` |
| `""` | 写 `key: ""` |
| `[]` | 写 `key: []` |
| 未包含 Key | 不操作 |

删除 `score`、`status` 或 `progressStatus` 必须经过字段级明确操作，不得由空输入隐式删除。

## 12. 受限 YAML 子集

### 12.1 支持

- 顶层 `key: value`。
- 双引号与单引号字符串。
- 严格布尔 `true`、`false`。
- 整数、十进制浮点数、`null`。
- `|`、`|-` 纯文本块标量。
- 简单缩进字符串列表。
- 简单字符串内联数组。
- 整行注释。
- 引号外的行内注释，前提是解析边界唯一。

### 12.2 不支持

- 锚点 `&`、别名 `*`。
- 自定义标签 `!tag`。
- 多级嵌套映射。
- 复杂跨行对象。
- Folded `>` 块标量。
- 复杂 flow mapping。
- 不明确缩进的列表/块。
- 非字符串列表元素。
- 无法唯一识别边界的结构。

不支持语法默认整篇跳过。若需局部修改无关字段，必须证明目标字段范围独立、其余字节保持不变，并经单独测试批准。

## 13. Frontmatter 边界

- BOM 可位于文件开头，BOM 后立即出现起始 `---`。
- 起始分隔符必须独占一行。
- 结束分隔符必须独占一行。
- 缺少任一分隔符：`NO_FRONTMATTER`，跳过。
- 结束标记后正文原样保留。
- 空 Frontmatter：允许读取，但迁移操作需明确。
- Frontmatter 内多个顶层重复 Key：`DUPLICATE_KEY`，整篇跳过写入。
- 顶层 Key 识别以行首无缩进为准。

## 14. 字段行范围

基础匹配：

```python
^([A-Za-z0-9_-]+):(.*)$
```

字段范围算法：

1. 识别目标 Key 所在行及其缩进级别 0。
2. 后续空行暂视为候选从属行。
3. 后续缩进大于该 Key 的行属于从属范围。
4. 遇到下一个不缩进的顶层行或 Frontmatter 结束标记即停止。
5. 块标量只接受其声明的缩进。
6. 任何边界歧义都拒绝修改。

不得使用全局字符串替换、正则全局替换或按值搜索定位字段。

## 15. Patch 操作

### 更新

- 只替换目标 Key 的完整行范围。
- 对同一字段的标量更新，如安全可识别，保留行尾注释。
- 保持原有缩进风格和换行符。

### 新增

- 仅在 Frontmatter 结束分隔符之前插入。
- 新建字段顺序稳定：`schemaVersion`、`mediaId`，其他字段按操作列表。
- 不以覆盖未知同名缩进行的方式新增。

### 删除

- 只用于显式 `REMOVE_FIELD`。
- 删除整个字段范围。
- 不删除相邻未知注释或空行，除非它们明确属于该字段范围。

## 16. 注释和特殊内容保护

必须保留：

- 未修改字段及行内注释。
- 整行注释和空行。
- 缩进。
- Obsidian 双链 `[[...]]`。
- 正文 Markdown、Callout、代码块和图片。
- 未知字段。
- 文件名、目录和附件路径。

目标字段引号内 `#` 或 `:` 不得当作注释/Key 分隔符。

## 17. 重复 Key 与语法越界

- 任意顶层重复 Key：整篇不写入。
- 语法越界：默认整篇跳过。
- 无法唯一确定目标 Key 范围：跳过。
- 旧文件可继续只读展示能解析的部分，但不得保存。
- 所有跳过原因写入迁移/运行报告。

## 18. 编码与换行

- 严格 UTF-8 解码；失败即跳过。
- 不自动猜测 GBK、UTF-16 等编码。
- 保留 UTF-8 BOM。
- 保留 LF 或 CRLF。
- 不统一缩进。
- 不删除末尾换行。
- Patch 以原始字节基于同一换行风格重建。
- 写临时文件后按字节回读校验。

## 19. 字段序列化

- 纯安全标量可裸写。
- 含首尾空格、`#`、`: `、引号、换行或 YAML 特殊前缀时必须安全引用。
- 字符串日期按字符串处理，不自动变成 YAML 日期对象。
- `None` -> `null`。
- `True`/`False` -> 小写 `true`/`false`。
- 空字符串 -> `""`。
- 列表默认缩进两空格，每项 `- `。
- 块文本使用 `|-`，只用于 `comment` 等已批准字段。

## 20. Patch 验收

必须通过：

1. 目标字段值符合规格。
2. 未修改字节完全一致。
3. 正文完全一致。
4. BOM/换行/末尾换行一致。
5. 重复 Key 不覆盖。
6. 不支持结构不强行处理。
7. 目标字段行内注释按批准规则保留。
8. 写后重新读取并通过受限解析。
9. 失败时原文件保持原样。
10. 临时文件与目标文件同目录，原子替换前再次校验哈希。

## 21. 测试矩阵

| 类别 | 必测内容 |
|---|---|
| 标量 | 普通、中文、英文、引号、冒号、井号、Unicode |
| 数字 | 整数、0、负数、十进制、NaN 文本、Infinity 文本 |
| 布尔 | true、false、字符串布尔、整数布尔 |
| 列表 | 缩进列表、内联数组、空数组、重复项 |
| 块 | `|`、`|-`、空行、缩进、Obsidian 双链 |
| 文件 | BOM、LF、CRLF、无末尾换行 |
| 异常 | 重复 Key、非法 UTF-8、缺少分隔符、未知 tag、锚点 |
| Patch | 更新、插入、显式删除、空字符串、null |
| 正文 | 图片、代码块、Callout、未知字段 |
