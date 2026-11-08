# 阶段 1 测试报告

测试日期：2026-11-07

## 自动测试

命令：

```powershell
python -m unittest discover -s tests -v
```

结果：38 项通过，0 项失败。

覆盖内容：

- 备注保存队列、修订号和并发冲突。
- Obsidian 外部修改检测，以及写入过程中再次发生外部修改。
- 同一修订号只能写入一次。
- `..`、绝对路径和目录逃逸被拒绝。
- 斜杠、反斜杠和 `.` 等不同路径写法规格化到同一把路径锁。
- Windows 大小写不同的目录和文件名共享基于目标绝对路径的同一把锁，并发写入只有一个成功。
- 无效 UTF-8、重复字段、未闭合引号不覆盖原文件。
- CRLF、无末尾换行、BOM 和数据目录边界。
- 注释、引号、冒号、列表和未知字段保留。
- 备份失败、校验失败和写入失败恢复。
- 备份完成后、原子替换前再次发生外部修改时停止替换。
- 原子替换后的验证失败只回滚仍属于本次写入的内容。
- 单篇备份与完整快照。
- 与 Vault 同级的临时安装使用自己的备份、日志和回收目录，不写入旧 `.media-app`。
- `/api/item`、`/api/create`、`/api/delete`、`/api/backup` 和 `/api/quit`。
- Host、Origin、Content-Type、路径和请求类型校验。
- 本地附件只能读取 `attachments/` 中的真实图片。
- 远程图片域名精确匹配、DNS 公网校验、IP 固定连接、重定向复核和体积限制。
- 搜索封面实际使用的 `asset.doubanio.com` 已加入精确白名单。
- 添加媒体搜索优先使用豆瓣插件同款 `j/search`，空结果时才回退轻量接口。
- 系统 DNS 返回代理假地址时使用固定公网 DoH，重新校验并固定公网 IP。

## 真实 Vault 只读审计

命令：

```powershell
python tests/audit_library.py "<OBSIDIAN_VAULT>"
```

结果：

```text
notes=292 parsed=292 unparsed=0
missing_images=0
```

该命令只读取真实 Vault，没有修改任何真实媒体笔记。

## 真实 Vault 临时副本回归

命令：

```powershell
python tests/regress_real_copy.py "<OBSIDIAN_VAULT>"
```

脚本将 292 篇 Markdown 复制到系统临时目录，在副本中通过安全写入函数逐篇更新原状态，并检查：

- 笔记数量和分类不变。
- BOM 和换行风格不变。
- 末尾换行状态不变。
- 标题、图片引用、未知字段和正文保护内容不变。
- 每次修改都有原文备份。

结果：

```text
notes=292 updated=292 backups=292 failures=0
```

真实 Vault 没有执行故障注入或写入。

## 前端浏览器回归

测试实例使用 `<LOCAL_REPO>\.test-artifacts\ui-vault` 临时 Vault。

- A 条目输入备注后立即切到 B：内容只写入 A。
- 连续切换 B、C、A：三条备注分别写入正确文件。
- 修改备注后立即关闭详情：离开详情时保存完成。
- 关闭服务后修改备注：页面显示“保存失败，点此重试”，草稿在切换后仍保留。
- 服务恢复后点击重试：草稿成功写入原条目。
- 页面打开后从外部修改 A 的 YAML，再保存备注：服务返回冲突，A 文件保持外部修改，页面显示冲突提示和可重试草稿。
- 关闭服务后切换完成状态：前端回滚，仍显示“未看完”，并显示保存失败。
- 快速输入两个不同外部搜索词：最终页面结果对应最后一个搜索词，旧响应没有覆盖。
- 输入定时器与回车不会对同一关键词发出两次搜索；瞬时空结果不会覆盖已有结果。
- 媒体库本地筛选正常。
- 封面、详情、个人评分控件、状态和日期控件正常渲染。
- 在临时打包的 Windows 桌面程序中输入备注后立即关闭整个窗口，窗口先完成保存再退出。
- 不填写备注、只触发状态保存并停止临时服务时，关闭窗口仍会等待失败任务并保持窗口打开。
- JavaScript 结果归并测试覆盖全部成功、单个失败、队列异常、部分成功部分失败、残留草稿和已记录失败。

桌面关闭回归脚本：

```powershell
pwsh -File tests/desktop_close_smoke.ps1 `
  -Executable <临时包>\影音库.exe `
  -NotePath <临时 Vault>\02-电影阅读\movie\<测试笔记>.md `
  -Marker "desktop-close-smoke"
```

保存失败分支增加 `-ExpectSaveFailure`。两个分支都只在临时 Vault 中执行。

JavaScript 保存结果检查：

```powershell
node tests/test_save_outcome.js
```

结果：`save-outcome-tests: PASS`

## 图片与 API 冒烟测试

当前真实 Vault 唯一远程封面：

```text
https://img3.doubanio.com/view/photo/s_ratio_poster/public/p2936502228.jpg
```

安全网络冒烟测试结果：

```text
bytes=20880 header=image/jpeg detected=image/jpeg
```

在临时 Vault 中通过 `/api/create` 下载并保存同一封面后，`/media` 返回：

```text
Status: 200
Content-Type: image/jpeg
Bytes: 20880
```

没有访问真实 Vault 的图片或修改真实封面。

## 构建检查

命令：

```powershell
node --check app\web\app.js
dotnet build shell\AppHost.csproj -c Release --nologo --no-restore
```

结果：

- JavaScript 语法检查通过。
- .NET 构建成功，0 个警告，0 个错误。
- 所有 Python 源码和测试通过 `py_compile`。

## 已知问题

1. 阶段 2 尚未执行。`schemaVersion`、`mediaId`、分来源评分、`progressStatus`、PyYAML 和迁移工具均未修改真实数据。
2. 当前阶段仍使用保守 YAML 结构检查。重复字段、未闭合引号和明显损坏结构会拒绝写入；完整标准 YAML、锚点和复杂嵌套将由阶段 2 的成熟解析器处理。
3. 现有 `score` 的来源仍无法统一判断，阶段 1 保持原字段和现有显示逻辑，避免猜测来源。
4. 图片白名单只包含现有数据/实际搜索返回的豆瓣 `img1`–`img9`、`asset.doubanio.com`、Steam 两个 CDN 主机，以及现有 Bangumi 下载流程使用的单一主机 `lain.bgm.tv`。没有开放厂商通配域名。
5. 当系统 DNS 返回非公网地址时，远程图片会使用固定 IP `223.5.5.5` 的 HTTPS DoH 重新解析。DoH 或公网解析不可用时，请求失败，不会退回私网或未经验证的连接。
6. 手动恢复采用复制文件的方式，没有一键恢复按钮，以降低误覆盖风险。
7. 完整快照目前保留最近 10 个；单篇备份保留最近 20 个版本。本阶段没有定时备份或云端同步。
8. 程序没有打包或覆盖当前正在运行的 `.media-app` 安装。源码和构建产物仅在开发工作区。

## 初审后修复复核

- 不同路径写法现在共享基于规范化目标绝对路径的锁键；Windows 大小写别名也会命中同一把锁，原始路径中的 `..` 直接拒绝。
- 备份完成后和原子替换前再次比较内容哈希。
- 验证失败时先检查目标是否仍等于本次写入内容，只在这种情况下恢复原文。
- 若替换后、验证前发生外部修改，程序保留外部内容并返回冲突。
- 前端提供 `window.MediaVaultApp.flushAllPending()`，桌面壳关闭前等待保存队列和全部草稿，并检查每个任务返回的 `ok` 值。
- 桌面壳在保存失败或前端响应超时时阻止关闭并显示提示。
- 服务 ping 同时返回 `version`、`protocolVersion` 和 UUID `instanceId`；窗口壳只复用版本、协议和实例信息均兼容的服务。
- 从 Vault 外解压并直接运行临时包时，`vault-path.txt` 会把正式 Vault 传给服务；界面显示 292 条媒体，服务 ping 和媒体库根目录均为正式 Vault。
