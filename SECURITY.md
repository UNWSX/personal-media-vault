# Security

本项目的媒体数据是用户自己的 Markdown 文件，不随源码提交。

以下内容不会进入仓库或分享包：

- Obsidian Vault 中的媒体笔记和附件
- 豆瓣登录 Cookie
- WebView2 缓存和窗口状态
- 备份、日志、评分队列
- 本机绝对路径

独立豆瓣登录态使用 Windows DPAPI 加密，并只保存在当前用户的本机环境中。
