from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, filedialog, messagebox
from tkinter import ttk

from data_migration import (
    DataMigrationError,
    MigrationOptions,
    apply_migration,
    normalize_data_root,
    scan_migration,
)


POLICY_LABELS = {
    "仅补充缺失文件": "skip",
    "源文件较新时覆盖（推荐）": "newer",
    "始终覆盖同名文件": "force",
}
TOOL_VERSION = "1.0.0"
ACTION_LABELS = {
    "copy": "新增",
    "overwrite": "覆盖",
    "skip": "跳过",
    "conflict": "冲突",
}


def _resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return "%.1f %s" % (size, unit) if unit != "B" else "%d B" % size
        size /= 1024
    return "%d B" % value


class DataMigrationApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("影音库数据迁移助手 %s" % TOOL_VERSION)
        self.root.geometry("1020x700")
        self.root.minsize(820, 560)
        icon = _resource_path("app.ico")
        if icon.is_file():
            try:
                self.root.iconbitmap(default=str(icon))
            except Exception:
                pass

        self.source_var = StringVar()
        self.destination_var = StringVar()
        self.policy_var = StringVar(value="源文件较新时覆盖（推荐）")
        self.include_all_var = BooleanVar(value=False)
        self.status_var = StringVar(value="请选择旧数据目录和新版本数据目录")
        self.plan = None
        self.worker = None
        self.busy = False

        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self):
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(
            outer,
            text="影音库数据迁移助手 %s" % TOOL_VERSION,
            font=("Microsoft YaHei UI", 18, "bold"),
        )
        title.pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "用于版本升级时把旧版“数据”合并到新版。源目录不会被删除；"
                "执行前会完整备份目标目录。迁移时请关闭影音库和 Obsidian。"
            ),
            foreground="#555555",
            wraplength=940,
        ).pack(anchor="w", pady=(4, 14))

        paths = ttk.LabelFrame(outer, text="目录", padding=12)
        paths.pack(fill="x")
        paths.columnconfigure(1, weight=1)
        ttk.Label(paths, text="旧版数据").grid(row=0, column=0, sticky="w", padx=(0, 8))
        source_entry = ttk.Entry(paths, textvariable=self.source_var)
        source_entry.grid(
            row=0, column=1, sticky="ew", pady=4
        )
        source_entry.bind("<KeyRelease>", lambda _event: self._invalidate())
        ttk.Button(paths, text="选择目录", command=self._choose_source).grid(
            row=0, column=2, padx=(8, 0)
        )
        ttk.Label(paths, text="新版数据").grid(row=1, column=0, sticky="w", padx=(0, 8))
        destination_entry = ttk.Entry(paths, textvariable=self.destination_var)
        destination_entry.grid(
            row=1, column=1, sticky="ew", pady=4
        )
        destination_entry.bind(
            "<KeyRelease>", lambda _event: self._invalidate()
        )
        ttk.Button(paths, text="选择目录", command=self._choose_destination).grid(
            row=1, column=2, padx=(8, 0)
        )

        options = ttk.LabelFrame(outer, text="合并规则", padding=12)
        options.pack(fill="x", pady=(12, 0))
        ttk.Label(options, text="同名文件").pack(side="left")
        policy = ttk.Combobox(
            options,
            textvariable=self.policy_var,
            values=list(POLICY_LABELS),
            state="readonly",
            width=28,
        )
        policy.pack(side="left", padx=(8, 20))
        policy.bind("<<ComboboxSelected>>", lambda _event: self._invalidate())
        ttk.Checkbutton(
            options,
            text="迁移整个所选文件夹（高级）",
            variable=self.include_all_var,
            command=self._invalidate,
        ).pack(side="left")
        ttk.Label(
            options,
            text="默认只迁移“媒体库/02-电影阅读、attachments、.media-app”",
            foreground="#666666",
        ).pack(side="right")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=12)
        self.scan_button = ttk.Button(
            actions, text="扫描并预览", command=self._scan
        )
        self.scan_button.pack(side="left")
        self.apply_button = ttk.Button(
            actions,
            text="执行迁移",
            command=self._apply,
            state="disabled",
        )
        self.apply_button.pack(side="left", padx=8)
        ttk.Button(actions, text="打开报告目录", command=self._open_reports).pack(
            side="left"
        )

        self.summary_label = ttk.Label(actions, text="尚未扫描", foreground="#666666")
        self.summary_label.pack(side="right")

        table = ttk.LabelFrame(outer, text="迁移预览", padding=8)
        table.pack(fill="both", expand=True)
        columns = ("action", "path", "size", "reason")
        self.tree = ttk.Treeview(table, columns=columns, show="headings")
        headings = {
            "action": ("操作", 80),
            "path": ("相对路径", 540),
            "size": ("大小", 100),
            "reason": ("原因", 190),
        }
        for key, (label, width) in headings.items():
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(
            table, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(10, 0))
        self.progress = ttk.Progressbar(footer, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(footer, textvariable=self.status_var).pack(anchor="w", pady=(4, 0))

    def _choose_source(self):
        selected = filedialog.askdirectory(title="选择旧版数据目录或旧版程序目录")
        if selected:
            self.source_var.set(selected)
            self._invalidate()

    def _choose_destination(self):
        selected = filedialog.askdirectory(title="选择新版数据目录或新版程序目录")
        if selected:
            self.destination_var.set(selected)
            self._invalidate()

    def _invalidate(self):
        self.plan = None
        self.apply_button.configure(state="disabled")
        self.summary_label.configure(text="目录或规则已变化，请重新扫描")
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _options(self) -> MigrationOptions:
        return MigrationOptions(
            policy=POLICY_LABELS[self.policy_var.get()],
            include_all=self.include_all_var.get(),
        )

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.scan_button.configure(state=state)
        self.apply_button.configure(
            state=state if busy or self.plan is None else "normal"
        )
        if not busy:
            self.apply_button.configure(
                state="normal" if self.plan is not None else "disabled"
            )

    def _scan(self):
        source = self.source_var.get().strip()
        destination = self.destination_var.get().strip()
        if not source or not destination:
            messagebox.showwarning("缺少目录", "请先选择旧版和新版数据目录。")
            return
        self._set_busy(True)
        self.status_var.set("正在扫描并比较文件…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)

        def work():
            try:
                plan = scan_migration(source, destination, self._options())
            except Exception as exc:
                self.root.after(
                    0, lambda error=exc: self._scan_failed(error)
                )
                return
            self.root.after(0, lambda: self._scan_done(plan))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _scan_failed(self, exc: Exception):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self._set_busy(False)
        self.status_var.set("扫描失败")
        messagebox.showerror("无法扫描", str(exc))

    def _scan_done(self, plan: dict):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.plan = plan
        self._set_busy(False)
        self.apply_button.configure(state="normal")
        for item in plan["items"][:2000]:
            self.tree.insert(
                "",
                "end",
                values=(
                    ACTION_LABELS.get(item["action"], item["action"]),
                    item["relativePath"],
                    _format_bytes(item["sourceSize"]),
                    item["reason"],
                ),
            )
        counts = plan["summary"]["actions"]
        self.summary_label.configure(
            text="文件 %d · 新增 %d · 覆盖 %d · 跳过 %d · 冲突 %d" % (
                plan["summary"]["files"],
                counts.get("copy", 0),
                counts.get("overwrite", 0),
                counts.get("skip", 0),
                counts.get("conflict", 0),
            )
        )
        warnings = len(plan.get("warnings", []))
        self.status_var.set(
            "扫描完成；执行前将自动备份目标目录"
            + ("；%d 个临时/链接项被跳过" % warnings if warnings else "")
        )

    def _apply(self):
        if not self.plan:
            return
        counts = self.plan["summary"]["actions"]
        message = (
            "即将从：\n%s\n\n迁移到：\n%s\n\n"
            "新增 %d，覆盖 %d，跳过 %d，冲突 %d。\n\n"
            "程序会先完整备份目标目录，源目录不会删除。\n"
            "请确认影音库与 Obsidian 已关闭。继续吗？"
        ) % (
            self.plan["sourceRoot"],
            self.plan["destinationRoot"],
            counts.get("copy", 0),
            counts.get("overwrite", 0),
            counts.get("skip", 0),
            counts.get("conflict", 0),
        )
        if not messagebox.askyesno("确认执行迁移", message):
            return
        self._set_busy(True)
        self.progress.configure(mode="determinate", maximum=100, value=0)
        self.status_var.set("正在备份并迁移…")

        def progress(index, total, item):
            percent = int(index * 100 / max(total, 1))
            self.root.after(
                0,
                lambda current=index, maximum=total, current_item=item, value=percent: (
                    self.progress.configure(value=value),
                    self.status_var.set(
                        "正在迁移 %d/%d：%s" % (
                            current,
                            maximum,
                            current_item["relativePath"],
                        )
                    ),
                ),
            )

        def work():
            try:
                report = apply_migration(self.plan, progress=progress)
            except Exception as exc:
                self.root.after(
                    0, lambda error=exc: self._apply_failed(error)
                )
                return
            self.root.after(0, lambda: self._apply_done(report))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _apply_failed(self, exc: Exception):
        self._set_busy(False)
        self.status_var.set("迁移中止")
        report = getattr(exc, "report", None)
        extra = ""
        if report:
            extra = "\n\n备份：%s\n报告：%s" % (
                report.get("backupPath") or "无",
                report.get("reportMarkdownPath") or "未生成",
            )
        messagebox.showerror("迁移中止", str(exc) + extra)

    def _apply_done(self, report: dict):
        self._set_busy(False)
        self.progress.configure(value=100)
        conflicts = report["counts"]["conflicts"]
        self.status_var.set(
            "迁移完成，但存在冲突" if conflicts else "迁移完成"
        )
        dialog = messagebox.showwarning if conflicts else messagebox.showinfo
        dialog(
            "迁移完成" if not conflicts else "迁移完成，存在冲突",
            "新增 %d，覆盖 %d，跳过 %d，冲突 %d。\n\n备份：%s\n报告：%s" % (
                report["counts"]["copied"],
                report["counts"]["overwritten"],
                report["counts"]["skipped"],
                conflicts,
                report.get("backupPath") or "无需备份",
                report.get("reportMarkdownPath") or "未生成",
            ),
        )
        self.plan = None
        self.apply_button.configure(state="disabled")

    def _open_reports(self):
        destination = self.destination_var.get().strip()
        if not destination:
            return
        root = normalize_data_root(destination)
        reports = root / ".media-app" / "migration" / "data-migrations"
        if not reports.exists():
            messagebox.showinfo("暂无报告", "完成一次迁移后会在这里生成报告。")
            return
        os.startfile(str(reports))

    def _on_close(self):
        if self.busy:
            messagebox.showwarning(
                "迁移正在进行",
                "请等待当前扫描或迁移完成后再关闭程序。",
            )
            return
        self.root.destroy()


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="影音库数据迁移助手 CLI")
    parser.add_argument("--version", action="version", version=TOOL_VERSION)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--policy", choices=("skip", "newer", "force"), default="newer")
    parser.add_argument("--include-all", action="store_true")
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args(argv)
    options = MigrationOptions(args.policy, args.include_all)
    try:
        if args.apply:
            if not args.plan:
                raise DataMigrationError("--apply 需要 --plan")
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            result = apply_migration(plan)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        plan = scan_migration(args.source, args.destination, options)
        if args.preview:
            args.preview.parent.mkdir(parents=True, exist_ok=True)
            args.preview.write_text(
                json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(json.dumps({
            "sourceRoot": plan["sourceRoot"],
            "destinationRoot": plan["destinationRoot"],
            "summary": plan["summary"],
            "warnings": plan["warnings"],
        }, ensure_ascii=False, indent=2))
        return 0
    except (DataMigrationError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def main() -> int:
    if "--cli" in sys.argv:
        index = sys.argv.index("--cli")
        return _cli(sys.argv[index + 1:])
    root = Tk()
    DataMigrationApp(root)
    if "--smoke" in sys.argv:
        root.update_idletasks()
        root.destroy()
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
