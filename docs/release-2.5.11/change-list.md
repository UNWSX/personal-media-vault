# 2.5.11 修改清单

- `app/server.py`
  - 使用独占 HTTP 监听服务。
  - 移除端口探测与实际绑定之间的竞态。
- `shell/Program.cs`
  - 无 Obsidian Vault 时固定选择程序旁的 `数据` 目录。
  - 服务复用检查同时匹配数据目录。
  - 去除 `BaseDirectory` 末尾分隔符后再推断 Obsidian Vault，
    避免 `.media-app\` 被错误识别成自身父目录。
- `tests/test_phase1.py`
  - 增加两个服务实例必须获得不同端口的回归测试。

