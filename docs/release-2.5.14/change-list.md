# 2.5.14 修改清单

- `app/web/app.js`
  - 打开添加窗口时复用启动阶段获得的 `doubanLogin` 状态。
  - 已登录时直接隐藏豆瓣状态区域，不再发起重复请求。
  - 仅未登录时调用 `/api/douban/session`。
- `app/web/style.css`
  - 增加 `.db-session[hidden]` 强制隐藏规则，避免组件的
    `display:flex` 覆盖原生 `hidden` 行为。
