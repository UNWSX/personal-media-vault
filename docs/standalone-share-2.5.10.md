# 影音库 2.5.10 独立分享版

独立分享版不需要安装 Obsidian，启动后数据位于程序旁：

```text
数据/
├─ 媒体库/
│  ├─ movie/
│  ├─ teleplay/
│  ├─ game/
│  └─ book/
└─ attachments/
```

## 验证

- 无 `.obsidian` 目录。
- 无 `vault-path.txt`。
- API 返回 `obsidian: false`。
- 初始媒体数为 0。
- 窗口主动启动分享包内的 `影音库服务.exe`。
- 服务参数中的 Vault 为分享包内的 `数据` 目录。
- 不会复用同一端口上正在运行的 Obsidian 版服务。

分享包不包含真实媒体、封面、日志、备份、窗口位置或评分任务。

