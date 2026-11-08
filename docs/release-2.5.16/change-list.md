# 2.5.16 修改清单

- `app/web/style.css`
  - `.db-results:not(:empty)` 增加 6px 上间距。
- `app/server.py`
  - 删除条目时检测 `image` 主封面。
  - 仅回收未被其他媒体笔记引用的本地附件。
  - `/api/delete` 增加 `attachmentMoved`、`attachmentShared` 和
    `attachment` 字段，原有 `ok`、`deleted` 字段保持兼容。
- `app/web/app.js`
  - 删除确认和成功提示说明封面的回收或保留结果。
