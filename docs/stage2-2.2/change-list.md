# 阶段 2.2 修改清单

## 新增文件

### `app/frontmatter_patch.py`

新增函数和类型：

- `parse_frontmatter(raw: bytes) -> ParseResult`
- `patch_frontmatter(raw: bytes, updates: Mapping[str, Any]) -> PatchResult`
- `read_field(raw: bytes, key: str) -> Any`
- `REMOVE_FIELD`
- `Diagnostic`
- `FieldRange`
- `ParseResult`
- `PatchResult`

行为：

- 使用 Python 标准库解析受限 Frontmatter。
- 返回字段类型、原始值、行范围和诊断。
- Patch 只修改目标字段行范围。
- Patch 失败时返回原始 bytes。
- 不直接读写文件。

### `tests/test_frontmatter_patch.py`

新增 21 项自动测试，覆盖：

- 基础类型、引号、注释、列表、块文本。
- 字段更新、新增、删除和空值。
- BOM、LF、CRLF、正文和未修改字段保留。
- 复杂/非法 YAML 拒绝。
- 多字段操作原子失败。
- 与阶段 1 原子写入函数的兼容性。

### `tests/audit_frontmatter_patch.py`

新增只读全库兼容审计脚本：

- 解析当前媒体的 Frontmatter。
- 汇总字段类型。
- 列出不安全文件和诊断。
- 不写入文件、不执行迁移。

### 文档

- `docs/stage2-2.2/README.md`
- `docs/stage2-2.2/test-report.md`
- `docs/stage2-2.2/change-list.md`

## 原有逻辑变化

- 生产服务、API、前端和桌面程序未改动。
- 现有 `parse_fm()` 未替换。
- 新引擎尚未接入任何写入路径。
- 阶段 1 的 revision、原子写入、备份和日志行为保持不变。

## API 兼容性

无 API 变化。2.2 模块是独立底层组件，目前不影响旧版或新版客户端。

## 测试

```powershell
python -m unittest discover -s tests -v
python tests/audit_frontmatter_patch.py "<OBSIDIAN_VAULT>"
python -m py_compile app\frontmatter_patch.py
node --check app\web\app.js
dotnet build shell\AppHost.csproj -c Release --nologo --no-restore
```

当前结果：

- Python 自动测试：59 项通过。
- 真实 Vault 只读解析：295/295 成功。
- .NET Release：构建成功，0 警告，0 错误。
- 真实 Markdown 没有写入。
