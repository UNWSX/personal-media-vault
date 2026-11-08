# 2.5.7 修改清单

- `shell/MainForm.cs`
  - `RestoreGeometry()`：恢复最大化状态，并钳制普通窗口到当前屏幕工作区。
  - `OnClosing()`：改用普通窗口的 `Bounds` 或最大化前的 `RestoreBounds`，并保存 `maximized`。
- `app/web/style.css`
  - 保持上一版排序按钮的自动宽度与图标结构。
  - 通过调整左右内边距，将图标和文字整体向左微移 3px。
- `app/web/index.html`
  - 保持原 `↕` 字符图标，不改变按钮结构。
- `docs/release-2.5.7/`
  - 增加修复说明。

不涉及 Python、Markdown、API、评分数据或真实 Vault 结构。

