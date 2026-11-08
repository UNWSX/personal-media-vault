# 阶段 1 修改清单

## 新增

- `app/storage_safety.py`
  - 严格 UTF-8 与 BOM/换行保留。
  - 同目录临时文件、刷新、回读验证和原子替换。
  - 修改前备份与失败恢复。
  - 单篇备份保留和完整快照。
- `app/safe_network.py`
  - 精确图片域名白名单。
  - 公网 IP 校验。
  - DNS 校验后固定 IP 连接，防止 DNS Rebinding。
  - 重定向逐跳复核和敏感头跨域清理。
  - 连接、读取、大小和 MIME 限制。
  - 图片白名单加入现有搜索结果实际使用的 `asset.doubanio.com`。
- `app/activity_log.py`
  - 每日 JSONL 日志和敏感字段脱敏。
- `docs/phase1-operations.md`
  - 备份、日志、恢复和回收目录说明。
- `docs/phase1-test-report.md`
  - 自动测试、真实库只读审计、临时副本回归和浏览器测试结果。
- `app/web/save_outcome.js`
  - 根据队列任务实际返回值判断关闭前是否全部保存成功。
- `tests/`
  - 安全写入、API、网络和真实库副本回归脚本。
  - `desktop_close_smoke.ps1` 验证窗口关闭前保存，以及保存失败时阻止关闭。
  - `test_save_outcome.js` 验证多队列全部成功、部分失败和异常结果。

## 修改

- `app/server.py`
  - 新增内容修订号和路径级锁。
  - 所有 Vault 相对路径先规范化，拒绝 `..`、绝对路径和盘符路径。
  - 不同分隔符、`.` 写法和 Windows 大小写别名共享基于目标绝对路径的同一锁键。
  - 更新、删除、封面修复限定媒体目录。
  - 新建使用原子写入和唯一文件名。
  - 写入使用备份、临时文件、回读验证和原子替换。
  - 备份完成后、原子替换前再次检查哈希。
  - 验证失败只在目标仍是本次写入内容时回滚，外部内容优先。
  - 增加 Host、Origin/客户端标记、Content-Type 和请求字段校验。
  - 增加 `/api/backup`。
  - `/media` 本地读取限制为 `attachments/`。
  - `/media` 远程请求使用安全网络模块。
  - 增加无效笔记跳过提示、操作日志和受控退出。
  - 临时安装目录与 Vault 同级时，备份、日志和回收目录跟随实际应用目录，不写入旧安装目录。
  - 批量游戏升级前创建完整媒体笔记快照，并为每篇写入修订号。
  - 前端保存兼容：旧字段和旧请求仍可读取/写入。
  - 元数据搜索改用豆瓣插件同款 `www.douban.com/j/search`，并只在空结果时回退 `subject_suggest`。
- `app/web/app.js`
  - 备注保存固定目标路径，使用草稿 Map、debounce、flush 和单笔记保存队列。
  - 切换、关闭、失焦时处理未保存备注。
  - 保存失败草稿保留并可重试。
  - 增加 `window.MediaVaultApp.flushAllPending()`，关闭窗口前处理全部草稿和保存队列。
  - 关闭检查读取队列任务的实际返回结果；`{ok:false}`、冲突、异常和未保存草稿都会阻止关闭。
  - 状态、评分和游戏字段保存失败时回滚或采用服务器最新状态。
  - 搜索使用 AbortController 和请求序号。
  - 回车与 500ms 输入定时器共用同一搜索任务，不会重复请求同一关键词。
  - 同一关键词的瞬时空响应不会清空已经显示的结果。
  - POST 请求增加客户端来源标记。
  - 新增手动备份按钮处理。
  - 自动刷新和解析失败提示。
- `app/web/index.html`
  - 新增“立即备份”按钮。
- `shell/Program.cs`
  - 只复认带 `app=media-vault` 标识的本地服务。
  - 同时校验服务版本、协议版本和 UUID 实例标识。
  - 从 Vault 外运行临时包时读取 `vault-path.txt`，并把 `--vault` 传给服务。
  - 复用服务前校验 Vault 根目录，避免连接其他数据目录。
  - 关闭时先发送受控退出请求，超时才结束自己启动的进程树。
- `shell/MainForm.cs`
  - 关闭流程改用受控退出。
  - 窗口关闭前通过 WebView 消息等待前端保存；失败或超时则保持窗口打开。
- `README.md`
  - 更新阶段 1 功能、API 和安全限制。
- `.gitignore`
  - 排除构建和临时测试目录。

## API 兼容性

现有 URL 和旧参数保持不变。新增内容均为可选字段或服务端行为：

- `/api/item`、`/api/delete`、`/api/fix-image` 接受可选 `revision`。
- 冲突响应使用 HTTP 409，并返回 `conflict: true` 和 `latest`。
- `/api/library` 增加 `app`、`version`、`warnings`，原有条目字段不变。
- `/api/create`、`/api/search`、`/api/import`、`/api/diag` 保持原有 URL 和主要响应结构。
- `/api/backup` 是新增接口。
- 旧版前端从同源页面写入时仍有合法 Origin，可以继续提交原有 JSON 参数。
