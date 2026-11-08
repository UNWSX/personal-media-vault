# 2.5.11 测试报告

日期：2026-11-08
源码提交：`ec4fa5c`

## 根因

旧服务先探测可用端口，再关闭探测 Socket，随后才创建真正的 HTTP
监听器；原监听器还允许地址复用。两个服务实例因此可能同时认为自己
取得同一个 `127.0.0.1:8765`。独立版窗口又可能在找不到本地数据目录时
接受另一个正在运行的服务，表现为两个 EXE 明明放在一起却无法启动。

## 修复验证

- `MediaVaultHTTPServer.allow_reuse_address = False`。
- 端口探测与实际监听合并为一次独占绑定；端口被占用时直接尝试下一个端口。
- 独立版固定使用程序旁的 `数据` 目录。
- 窗口复用服务时同时核对 app、API 版本、协议版本、实例 UUID 和 Vault 根目录。
- 修正 `BaseDirectory` 末尾分隔符导致 Obsidian 安装父目录识别错误的问题。

## 打包程序实测

独立分享版：

- 双击 `影音库.exe` 后成功启动包内服务。
- 服务监听 `127.0.0.1:8765`，服务进程路径位于修复包内。
- `/api/ping` 返回的 `vaultRoot` 为修复包下的 `数据` 目录。
- `/api/library` 返回 `obsidian: false`、`count: 0`。
- 同时启动第二个隔离服务时，两个实例分别监听 `8765`、`8766`，
  且各自返回不同的 Vault 根目录与实例 UUID。

Obsidian 修复包（只在临时 Vault 中验证）：

- 服务成功识别临时 Vault 的 `.obsidian`。
- `/api/ping` 返回的 Vault 根目录与临时 Vault 完全相同。
- `/api/library` 返回 `obsidian: true`、`count: 1`、无警告。
- 未读取、写入或迁移真实 Vault。

## 自动测试

- Python：`146/146` 通过，包含服务独占端口回归测试。
- JavaScript 语法：`app.js`、`compat_logic.js`、`save_outcome.js` 全部通过。
- JavaScript 逻辑：兼容逻辑、保存结果测试全部通过。
- .NET Release：构建成功，0 警告、0 错误。
- PyInstaller：服务单文件 EXE 构建成功。

## 分享包清洁检查

- `数据/` 只包含空的媒体目录和附件目录。
- 不包含媒体笔记、封面、日志、评分队列、备份、窗口状态或 WebView2 缓存。
- 不包含真实 Vault 路径。

## 已知限制

- 分享包仍依赖 Windows 的 Microsoft Edge WebView2 Runtime；Windows
  10/11 通常已经安装。
- 独立分享包自带 .NET 7 运行时；Obsidian 修复包使用系统已有的
  .NET 7 Desktop Runtime。
- 本次没有部署、覆盖当前安装，也没有执行阶段 2 数据迁移。

