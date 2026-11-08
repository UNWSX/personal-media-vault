from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from migration import (
    ALL_GROUPS,
    MigrationError,
    apply_plan,
    create_dry_run,
    public_scan_result,
    rollback_plan,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="个人影音库 2.0 本地迁移工具；Apply/Rollback 只允许 CLI",
    )
    parser.add_argument("--migrate-vault", "--vault", dest="vault", required=True)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument(
        "--groups",
        default="BASE_SCHEMA,CALLOUT_STATUS",
        help="Dry-run 选择的操作组，逗号分隔",
    )
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--confirm", help="精确确认文本")
    parser.add_argument("--resume", action="store_true")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--scan", action="store_true")
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--apply", action="store_true")
    action.add_argument("--rollback", action="store_true")
    return parser


def _confirmation(value: str | None, expected: str) -> str:
    if value is not None:
        return value.strip()
    print("必须手动输入：%s" % expected)
    return input("> ").strip()


def _plan_file(args: argparse.Namespace, data_root: Path) -> Path:
    if args.plan is None:
        raise MigrationError("--apply/--rollback 需要 --plan")
    path = args.plan
    if not path.is_absolute():
        candidate = data_root / "migration" / "plans" / path
        if candidate.exists():
            path = candidate
    return path.resolve()


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    vault = Path(args.vault).resolve()
    data_root = (args.data_root or (vault / ".media-app")).resolve()
    try:
        if args.scan:
            result = public_scan_result(vault)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.dry_run:
            selected = {
                item.strip()
                for item in str(args.groups).split(",")
                if item.strip()
            }
            unknown = selected - ALL_GROUPS
            if unknown:
                raise MigrationError("未知操作组：%s" % ", ".join(sorted(unknown)))
            result = create_dry_run(
                vault,
                data_root=data_root,
                evidence_path=args.evidence.resolve() if args.evidence else None,
                selected_groups=selected,
            )
            print(json.dumps({
                "ok": True,
                "planId": result["plan"]["planId"],
                "planPath": result["planPath"],
                "previewJsonPath": result["previewJsonPath"],
                "previewMarkdownPath": result["previewMarkdownPath"],
                "counters": result["plan"]["counters"],
            }, ensure_ascii=False, indent=2))
            return 0
        if args.apply:
            plan_path = _plan_file(args, data_root)
            from migration import load_plan
            plan = load_plan(plan_path)
            confirmation = _confirmation(
                args.confirm,
                "APPLY %s" % plan.get("planId"),
            )
            result = apply_plan(
                vault,
                plan_path,
                data_root=data_root,
                confirmation=confirmation,
                resume=args.resume,
            )
            print(json.dumps({"ok": result["planState"] == "APPLIED", **result},
                             ensure_ascii=False, indent=2))
            return 0 if result["planState"] == "APPLIED" else 2
        if args.rollback:
            plan_path = _plan_file(args, data_root)
            from migration import load_plan
            plan = load_plan(plan_path)
            confirmation = _confirmation(
                args.confirm,
                "ROLLBACK %s" % plan.get("planId"),
            )
            result = rollback_plan(
                vault,
                plan_path,
                data_root=data_root,
                confirmation=confirmation,
            )
            print(json.dumps({
                "ok": result["planState"] == "ROLLED_BACK",
                **result,
            }, ensure_ascii=False, indent=2))
            return 0 if result["planState"] == "ROLLED_BACK" else 2
    except (MigrationError, OSError, ValueError) as exc:
        print(json.dumps({
            "ok": False,
            "error": str(exc),
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
