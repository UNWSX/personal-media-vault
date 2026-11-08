# 阶段 2.3 测试报告

测试日期：2026-11-07  
测试范围：后端兼容模型、普通写入、前端字段冲突、Callout 和桌面关闭  
测试边界：破坏性操作只使用系统临时 Vault 或真实 Vault 的临时副本

## 1. Python 自动测试

命令：

```powershell
python -m py_compile app\server.py app\media_compat.py app\frontmatter_patch.py
python -m unittest discover -s tests -v
```

结果：

```text
Ran 83 tests
OK
```

失败 0，跳过 0。

新增或扩展覆盖：

- 旧 `status` 到类型相关 `progressStatus`。
- 显式状态优先与字段不一致告警。
- 未知历史状态保留。
- 各来源评分合法范围和安全解析。
- `myScore` 0.5 步长。
- 游戏和非游戏 `normScore`。
- 专用主评分与旧 `score` 同步。
- `tags`/`genre` 只读合并，不双写。
- `music` 旧模型兼容。
- UUIDv4、未来 schema、非法身份和重复 ID。
- 新笔记写入 `schemaVersion`、`mediaId` 和 `progressStatus`。
- 409 状态与主评分兼容组。
- 代码块中的 Callout 不修改。
- 多个 Callout、零匹配和类型不匹配不修改。
- Callout 额外说明不覆盖。
- CRLF、BOM、正文和末尾换行保留。

## 2. JavaScript 检查

命令：

```powershell
node --check app\web\app.js
node --check app\web\compat_logic.js
node tests\test_compat_logic.js
node tests\test_save_outcome.js
```

结果：

```text
compat-logic-tests: PASS
save-outcome-tests: PASS
```

覆盖：

- 状态组冲突。
- 主评分组冲突。
- 三方比较。
- 最新远端与本地 dirty fields 合并。
- 多个保存队列部分成功、部分失败时不返回成功。
- 未解决字段冲突时阻止窗口关闭。

## 3. .NET Release 构建

命令：

```powershell
dotnet build shell\AppHost.csproj -c Release --nologo --no-restore
```

结果：

```text
0 警告
0 错误
```

另外在 `.test-artifacts` 使用 `dotnet publish` 和 PyInstaller 6.16.0 生成隔离测试包。该构建依赖只用于临时验收，不加入应用运行时依赖。

## 4. 真实 Vault 只读审计

命令：

```powershell
python tests\audit_frontmatter_patch.py "<OBSIDIAN_VAULT>"
python tests\audit_library.py "<OBSIDIAN_VAULT>"
```

结果：

```text
files=295 parsed=295 failed=0
notes=295 parsed=295 unparsed=0
missing_images=0
```

只读归一结果：

```text
types: book=6, game=92, movie=133, teleplay=64
statusGroups: completed=239, not_started=56
progressSources: legacy=295
scoreSources: legacy=293, none=2
writeAllowed=true: 295
```

真实 Vault 没有写入 Markdown、附件或 mtime；没有运行迁移。

## 5. 真实媒体目录临时副本回归

命令：

```powershell
python tests\regress_real_copy.py "<OBSIDIAN_VAULT>"
```

结果：

```text
notes=295 updated=295 backups=295 failures=0
```

比较规则：

- 忽略本次预期写入的 `status` 和 `progressStatus`。
- 忽略一条允许同步的状态 Callout 行。
- 其余 Frontmatter、正文、BOM、LF/CRLF 和末尾换行必须一致。

测试只写系统临时目录中的 Vault 副本。

## 6. 临时 Vault 前端联调

使用独立临时 Vault 和本地服务验证：

- 电影、游戏卡片正常显示。
- 四键状态筛选渲染。
- 评分来源和量纲明确显示。
- 状态胶囊保存后 Markdown 同步。
- Obsidian 外部修改主评分后，本地编辑不同评分触发字段冲突。
- “保留本地并重试”保存本地状态，同时保留无关的远端评分修改。
- Callout 含额外说明时，Frontmatter 保存成功，正文保持原样，UI 显示明确未同步提示。

## 7. Windows 桌面关闭

隔离测试包同时包含当前 C# 外壳、当前 Python 服务和当前前端资源。

成功路径：

- 立即输入备注后关闭窗口。
- 前端 flush 完成，备注写入 Markdown。
- 删除确认完成后窗口退出。

失败路径：

- 备注为空，状态保存请求开始后终止测试服务。
- UI 显示保存失败并回滚状态。
- 关闭窗口时弹出“有 1 项数据没有保存成功，窗口将保持打开”。
- 窗口没有关闭。

## 8. 完整回归结论

- Python：83/83 通过。
- JavaScript：全部检查通过。
- .NET Release：0 警告、0 错误。
- 真实 Vault 只读解析：295/295。
- 真实目录临时副本写入回归：295/295，失败 0。
- Windows 桌面成功与失败关闭路径均符合预期。

## 9. 已知限制

- `scoreStar` 继续只读展示，普通评分写入时不重算；符号算法尚未通过模板和 Dataview 全量回归。
- `tags` 与 `genre` 只在读取层合并，暂不自动双写。
- `music` 继续使用旧二元状态。
- 混合换行、重复 YAML Key 和不受支持结构继续拒绝写入。
- 阶段 2.3 不提供迁移预览或执行工具。
- 阶段 1 的修订号和外层锁不能为不遵守锁协议的外部编辑器提供绝对无竞争的跨进程事务保证；冲突检测仍以保存前和替换后回读为准。

