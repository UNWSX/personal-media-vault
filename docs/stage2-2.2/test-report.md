# 阶段 2.2 测试报告

测试日期：2026-11-07  
测试范围：受限 Frontmatter 解析与 Patch 引擎

## 1. 自动测试

命令：

```powershell
python -m unittest discover -s tests -v
```

结果：

```text
Ran 59 tests
OK
```

其中新增 2.2 测试 21 项：

- 支持语法综合解析。
- 重复 Key 拒绝。
- 锚点、别名、自定义标签、folded 块、嵌套映射、flow mapping 拒绝。
- 列表内映射和嵌套内联数组拒绝。
- BOM、CRLF、行内注释和正文保留。
- `None`、空字符串和 `REMOVE_FIELD` 区分。
- 新增字段插入位置和唯一性。
- 列表更新和字段删除。
- 行内列表注释保护。
- 更新无关字段时保留列表行内注释。
- 标量注释阻止转换为多行文本。
- 数字字符串类型保持。
- 块文本更新。
- 更新无关字段时保留块文本中的井号与伪字段文本。
- 非法 UTF-8 拒绝。
- 混合换行拒绝。
- 无 Frontmatter 拒绝。
- 多字段操作原子失败。
- 删除不存在字段为空操作。
- 未修改字段与正文字节级一致。
- PatchResult 与阶段 1 原子写入兼容。

测试源码：

[test_frontmatter_patch.py](../../tests/test_frontmatter_patch.py)

## 2. 真实 Vault 只读审计

命令：

```powershell
python tests/audit_frontmatter_patch.py "<OBSIDIAN_VAULT>"
```

结果：

```text
files=295 parsed=295 failed=0
fieldKinds={'block': 2, 'boolean': 295, 'empty': 293, 'integer': 407, 'list': 975, 'number': 217, 'quoted': 14, 'string': 1985}
```

结论：

- 当前真实媒体笔记全部落在受限子集内。
- 仅执行读取和内存解析。
- 未修改 Markdown、附件、mtime 或目录结构。
- 未执行 Scan、Dry-run、Apply 或 Rollback。

## 3. 静态与构建检查

- `py_compile`：通过。
- JavaScript `node --check`：通过。
- .NET Release build：成功，0 警告，0 错误。

## 4. 核心不变量

- 目标字段之外的行保持原字节。
- 正文保持原字节。
- BOM 保持。
- LF/CRLF 保持。
- 文件末尾是否有换行保持不变。
- 写后重新使用受限解析器验证。
- 任一步失败都返回原始 bytes。

## 5. 已知限制

- 当前模块只处理 Frontmatter，不处理正文 Callout。
- 混合换行、重复 Key、复杂 YAML 默认整篇拒绝。
- 列表字段含行内注释时，不支持通过 Patch 重建列表。
- 当前没有文件锁、备份、快照或原子替换；这些仍由阶段 1 和后续 Migration Engine 承担。
- 尚未接入生产 API 或 UI。
