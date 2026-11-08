# 2.5.13 修改清单

- `app/web/index.html`
  - 登录状态区域、登录按钮和导入按钮默认隐藏，避免状态查询前闪现。
- `app/web/app.js`
  - 根据 `/api/douban/session` 返回状态切换按钮可见性。
  - 已登录时隐藏整个豆瓣登录状态条。
- `app/web/style.css`
  - 搜索输入框与类型切换框统一为 36px 高，使两者水平中心线一致。
- `tests/test_api_phase1.py`
  - 增加登录控件初始隐藏回归测试。
