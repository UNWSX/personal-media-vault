# 阶段 2.2：受限 Frontmatter Patch 引擎

状态：实现完成，等待阶段验收  
阶段范围：独立字节级 Patch 模块与测试  
未包含：迁移 CLI、API/UI 接入、批量扫描、真实 Vault 写入

## 1. 模块

新增：

```text
app/frontmatter_patch.py
```

公共接口：

```python
parse_frontmatter(raw: bytes) -> ParseResult
patch_frontmatter(raw: bytes, updates: Mapping[str, Any]) -> PatchResult
read_field(raw: bytes, key: str) -> Any
REMOVE_FIELD
```

该模块只处理内存中的原始字节，不读写文件。调用方必须先检查：

```python
result.ok
```

只有 `ok=True` 时，`result.data` 才能交给阶段 1 的原子写入层。

## 2. 安全边界

- 严格 UTF-8 解码。
- 支持并保留 UTF-8 BOM。
- 支持纯 LF、纯 CRLF；混合换行和单独 CR 默认拒绝。
- 只接受文件开头的 `---` Frontmatter。
- 不修改正文。
- 不修改非目标字段。
- 失败时返回原始 bytes。
- 一次 Patch 中任一字段不安全，全部更新取消。
- 不进行文件写入、备份或回滚；这些由阶段 1 原子写入层负责。

## 3. 支持的 Frontmatter 子集

- 顶层 `key: value`。
- 单引号和双引号字符串。
- 严格布尔 `true`、`false`。
- 整数、十进制数、`null`。
- 空值。
- `|`、`|-` 块文本。
- 简单缩进字符串列表。
- 简单字符串内联数组。
- 整行注释。
- 引号外的行内注释。
- 未知顶层字段原样保留。

## 4. 不支持并拒绝写入的结构

- YAML 锚点和别名。
- 自定义标签。
- 多级嵌套映射。
- Folded `>` 块标量。
- Flow mapping。
- 列表内的映射元素。
- 嵌套内联数组。
- 无法证明边界的缩进。
- 重复顶层 Key。
- 混合换行。
- 非法 UTF-8。

默认采用整篇跳过。只有目标字段的范围完全可确定且不涉及这些结构时，未来调用方才可以选择局部写入；当前模块对含错误诊断的文件一律返回不可 Patch。

## 5. 字段操作

### 更新

- 只替换目标字段的完整行范围。
- 简单标量的行内注释在能够安全保留时保留。
- 列表含行内注释时不重建。
- 标量注释阻止转换为块文本或列表。

### 新增

- 新字段插入 Frontmatter 结束标记前。
- 使用原文件换行风格。
- 字段按调用方提供的顺序插入。

### 删除

```python
REMOVE_FIELD
```

只有显式哨兵才删除字段。

| 输入 | 结果 |
|---|---|
| `REMOVE_FIELD` | 删除字段及其行范围 |
| `None` | `key: null` |
| `""` | `key: ""` |
| `[]` | `key: []` |
| 未包含 Key | 不操作 |

## 6. 诊断码

| 诊断码 | 含义 |
|---|---|
| `INVALID_UTF8` | 不是严格 UTF-8 |
| `NO_FRONTMATTER` | 缺少起始 Frontmatter |
| `NO_CLOSING_FRONTMATTER` | 缺少结束标记 |
| `UNSUPPORTED_NEWLINE` | 混合换行或单独 CR |
| `DUPLICATE_KEY` | 顶层字段重复 |
| `ORPHAN_INDENT` | 顶层字段之外出现缩进内容 |
| `UNSUPPORTED_TOP_LEVEL_SYNTAX` | 顶层语法越界 |
| `UNSUPPORTED_YAML` | 锚点、别名、标签、嵌套或复杂结构 |
| `INVALID_QUOTED_SCALAR` | 引号不完整或转义无效 |
| `INVALID_NUMBER` | 数字无效 |
| `UNSUPPORTED_LIST_ITEM` | 列表项不是字符串 |
| `UNSUPPORTED_NESTED_VALUE` | 缩进块不是简单列表 |
| `INVALID_BLOCK_SCALAR` | 块文本缩进无效 |
| `UNSAFE_TARGET_FIELD` | 目标字段本身语法越界 |
| `COMMENT_PRESERVATION_UNSAFE` | 更新会丢失目标注释 |
| `UNSUPPORTED_VALUE_TYPE` | 写入值类型不在子集内 |
| `BODY_CHANGED` | 内部校验发现正文发生变化 |

## 7. 当前真实数据只读结果

命令：

```powershell
python tests/audit_frontmatter_patch.py "<OBSIDIAN_VAULT>"
```

结果：

```text
files=295 parsed=295 failed=0
fieldKinds={'block': 2, 'boolean': 295, 'empty': 293, 'integer': 407, 'list': 975, 'number': 217, 'quoted': 14, 'string': 1985}
```

该命令只读取文件并解析内存，不写 Markdown，不修改 mtime。

## 8. 未完成事项

- 尚未把新 Patch 引擎接入 `server.py`。
- 尚未实现 status/Callout 联动。
- 尚未实现 2.3 兼容模型。
- 尚未实现 Migration CLI。
- 尚未对真实 Vault 执行 Dry-run 或 Apply。

进入 2.3 前须人工验收本阶段。
