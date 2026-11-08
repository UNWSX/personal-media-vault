# 2.5.12 测试报告

日期：2026-11-08

## 自动测试

- Python：`152/152` 通过。
- JavaScript 语法检查：通过。
- JavaScript 兼容逻辑：通过。
- JavaScript 保存结果：通过。
- JavaScript 自绘滚动条几何与边界：通过。
- .NET Release 构建：成功，0 警告、0 错误。

## 安全验证

- 登录 Cookie 必须包含 `dbcl2`。
- 换行、NUL、格式错误和超长 Cookie 被拒绝。
- Cookie 使用当前 Windows 用户范围的 DPAPI 加密。
- 加密文件不包含 Cookie 名称或值的明文。
- API 不返回 Cookie。
- 日志只记录保存或清除事件，不记录 Cookie。

## 兼容验证

- 有独立版登录态时优先使用该会话。
- 没有独立版登录态时，继续兼容 Obsidian 豆瓣插件登录态。
- 两者都没有时，电影和图书继续使用公开搜索回退。
