from __future__ import annotations

"""受限 Frontmatter 解析与字节保留 Patch。

该模块不写文件，只接收原始 bytes 并返回候选 bytes。调用方必须先确认
PatchResult.ok，才可以把结果交给阶段 1 的原子写入层。
"""

import codecs
import json
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

REMOVE_FIELD = object()

__all__ = [
    "REMOVE_FIELD",
    "Diagnostic",
    "FieldRange",
    "ParseResult",
    "PatchResult",
    "parse_frontmatter",
    "patch_frontmatter",
    "read_field",
]

_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]+(.*))?$")
_LIST_ITEM_RE = re.compile(r"^[ \t]*-[ \t]*(.*)$")
_INTEGER_RE = re.compile(r"^[+-]?\d+$")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\d*\.\d+)$")
_MAP_ITEM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*:(?:[ \t]+.*)?$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_BLOCK_RE = re.compile(r"^(?:\||-)$")


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    line: int | None = None
    severity: str = "error"


@dataclass(frozen=True)
class FieldRange:
    key: str
    value: Any
    raw_value: str
    kind: str
    start_line: int
    end_line: int
    eol: str
    inline_comment: str = ""
    has_nested_comments: bool = False


@dataclass
class ParseResult:
    ok: bool
    fields: dict[str, FieldRange]
    diagnostics: list[Diagnostic]
    lines: list[str] = field(repr=False)
    close_index: int | None = None
    bom: bool = False
    newline: str = "\n"


@dataclass
class PatchResult:
    ok: bool
    data: bytes
    diagnostics: list[Diagnostic]
    changed: bool
    changed_fields: tuple[str, ...] = ()


class _Unsafe(ValueError):
    def __init__(self, code: str, message: str, line: int | None = None):
        super().__init__(message)
        self.diagnostic = Diagnostic(code, message, line)


def _line_parts(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def _decode(raw: bytes) -> tuple[str, bool]:
    bom = raw.startswith(codecs.BOM_UTF8)
    body = raw[len(codecs.BOM_UTF8):] if bom else raw
    try:
        return body.decode("utf-8", errors="strict"), bom
    except UnicodeDecodeError as exc:
        raise _Unsafe("INVALID_UTF8", "文件不是严格 UTF-8") from exc


def _split_inline_comment(value: str) -> tuple[str, str]:
    quote: str | None = None
    escaped = False
    for index, ch in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote == '"' and ch == "\\":
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == "#" and (index == 0 or value[index - 1].isspace()):
            prefix = value[:index].rstrip()
            suffix = value[len(prefix):]
            return prefix, suffix
    return value.rstrip(), ""


def _parse_quoted(value: str) -> str:
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise _Unsafe("INVALID_QUOTED_SCALAR", "双引号字符串无法安全解析") from exc
        if not isinstance(parsed, str):
            raise _Unsafe("UNSUPPORTED_SCALAR", "只支持字符串双引号值")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise _Unsafe("INVALID_QUOTED_SCALAR", "单引号字符串没有闭合")
        inner = value[1:-1]
        if re.search(r"(?<!')'(?!')", inner):
            raise _Unsafe("INVALID_QUOTED_SCALAR", "单引号字符串中存在未转义引号")
        return inner.replace("''", "'")
    raise _Unsafe("INVALID_QUOTED_SCALAR", "引号字符串格式无效")


def _parse_scalar(value: str, *, list_item: bool = False) -> tuple[Any, str]:
    text = value.strip()
    if not text:
        return ("" if list_item else None), "empty"
    if text[0] in ("'", '"'):
        if len(text) < 2 or text[-1] != text[0]:
            raise _Unsafe("INVALID_QUOTED_SCALAR", "引号字符串没有闭合")
        return _parse_quoted(text), "quoted"
    if list_item and _MAP_ITEM_RE.fullmatch(text):
        raise _Unsafe("UNSUPPORTED_YAML", "列表中的映射元素不受支持")
    if text == "null":
        return None, "null"
    if text == "true":
        return True, "boolean"
    if text == "false":
        return False, "boolean"
    if _INTEGER_RE.fullmatch(text):
        try:
            return int(text), "integer"
        except ValueError as exc:
            raise _Unsafe("INVALID_NUMBER", "整数值无效") from exc
    if _NUMBER_RE.fullmatch(text):
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise _Unsafe("INVALID_NUMBER", "十进制数无效") from exc
        if not number.is_finite():
            raise _Unsafe("INVALID_NUMBER", "数值必须是有限值")
        return number, "number"
    if text.startswith(("&", "*", "!", "{", "[")):
        raise _Unsafe("UNSUPPORTED_YAML", "不支持锚点、别名、自定义标签或映射值")
    return text, "string"


def _split_flow_list(value: str) -> list[str]:
    inner = value[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    depth = 0
    for index, ch in enumerate(inner):
        if escaped:
            escaped = False
            continue
        if quote == '"' and ch == "\\":
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(inner[start:index].strip())
            start = index + 1
    if quote or depth != 0:
        raise _Unsafe("UNSUPPORTED_YAML", "内联数组引号或括号不完整")
    items.append(inner[start:].strip())
    return [item for item in items if item]


def _parse_inline_list(value: str) -> list[str]:
    values = _split_flow_list(value)
    result: list[str] = []
    for item in values:
        parsed, kind = _parse_scalar(item, list_item=True)
        if kind != "quoted" and kind != "string" and kind != "empty":
            raise _Unsafe("UNSUPPORTED_LIST_ITEM", "内联数组只支持字符串元素")
        result.append(str(parsed))
    return result


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _scan_field_block(lines: list[str], start: int, close_index: int) -> int:
    last_nonblank = start + 1
    index = start + 1
    while index < close_index:
        content, _ = _line_parts(lines[index])
        if content.strip() == "":
            index += 1
            continue
        if _indent(content) == 0:
            break
        last_nonblank = index + 1
        index += 1
    return last_nonblank


def _parse_block_scalar(lines: list[str], start: int, end: int, marker: str) -> str:
    content_lines: list[str] = []
    indents: list[int] = []
    for raw in lines[start + 1:end]:
        content, _ = _line_parts(raw)
        if not content.strip():
            content_lines.append("")
            continue
        indent = _indent(content)
        if indent == 0:
            raise _Unsafe("INVALID_BLOCK_SCALAR", "块标量缩进缺失")
        indents.append(indent)
        content_lines.append(content)
    if not indents:
        return ""
    base = min(indents)
    out = []
    for content in content_lines:
        if not content:
            out.append("")
        elif _indent(content) < base:
            raise _Unsafe("INVALID_BLOCK_SCALAR", "块标量缩进不一致")
        else:
            out.append(content[base:])
    text = "\n".join(out)
    if marker == "|" and out:
        text += "\n"
    return text


def _parse_list_block(lines: list[str], start: int, end: int) -> tuple[list[str], bool]:
    values: list[str] = []
    has_comments = False
    for raw in lines[start + 1:end]:
        content, _ = _line_parts(raw)
        if not content.strip():
            continue
        match = _LIST_ITEM_RE.match(content)
        if not match:
            raise _Unsafe("UNSUPPORTED_NESTED_VALUE", "缩进列表中存在非列表行")
        item, comment = _split_inline_comment(match.group(1))
        if comment:
            has_comments = True
        parsed, kind = _parse_scalar(item, list_item=True)
        if kind not in ("quoted", "string", "empty"):
            raise _Unsafe("UNSUPPORTED_LIST_ITEM", "列表只支持字符串元素")
        values.append(str(parsed))
    return values, has_comments


def parse_frontmatter(raw: bytes) -> ParseResult:
    diagnostics: list[Diagnostic] = []
    try:
        text, bom = _decode(raw)
    except _Unsafe as exc:
        return ParseResult(False, {}, [exc.diagnostic], [])

    lines = text.splitlines(keepends=True)
    has_crlf = "\r\n" in text
    has_bare_lf = re.search(r"(?<!\r)\n", text) is not None
    has_bare_cr = re.search(r"\r(?!\n)", text) is not None
    if has_bare_cr or (has_crlf and has_bare_lf):
        return ParseResult(
            False,
            {},
            [Diagnostic(
                "UNSUPPORTED_NEWLINE",
                "混合换行或单独 CR 不在受限 YAML 子集内",
            )],
            lines,
            bom=bom,
        )
    if not lines:
        return ParseResult(
            False,
            {},
            [Diagnostic("NO_FRONTMATTER", "文件为空")],
            lines,
            bom=bom,
        )
    first, _ = _line_parts(lines[0])
    if first.rstrip(" \t") != "---":
        return ParseResult(
            False,
            {},
            [Diagnostic("NO_FRONTMATTER", "文件开头没有 Frontmatter 起始标记", 1)],
            lines,
            bom=bom,
        )

    close_index = None
    for index in range(1, len(lines)):
        content, _ = _line_parts(lines[index])
        if content.rstrip(" \t") == "---":
            close_index = index
            break
    if close_index is None:
        return ParseResult(
            False,
            {},
            [Diagnostic("NO_CLOSING_FRONTMATTER", "Frontmatter 缺少结束标记")],
            lines,
            bom=bom,
        )

    _, first_eol = _line_parts(lines[0])
    _, close_eol = _line_parts(lines[close_index])
    newline = first_eol or close_eol or "\n"
    fields: dict[str, FieldRange] = {}
    seen: set[str] = set()
    index = 1
    while index < close_index:
        content, eol = _line_parts(lines[index])
        stripped = content.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        if content[:1].isspace():
            diagnostics.append(Diagnostic(
                "ORPHAN_INDENT",
                "顶层字段之外出现缩进内容",
                index + 1,
            ))
            index += 1
            continue
        match = _KEY_RE.match(content)
        if not match:
            diagnostics.append(Diagnostic(
                "UNSUPPORTED_TOP_LEVEL_SYNTAX",
                "顶层字段语法不在受限 YAML 子集内",
                index + 1,
            ))
            index += 1
            continue
        key = match.group(1)
        raw_value = (match.group(2) or "").rstrip()
        if key in seen:
            diagnostics.append(Diagnostic(
                "DUPLICATE_KEY",
                "顶层字段 %s 重复" % key,
                index + 1,
            ))
            index += 1
            continue
        seen.add(key)
        end = _scan_field_block(lines, index, close_index)
        inline_comment = ""
        has_nested_comments = False
        scalar_without_comment, scalar_comment = _split_inline_comment(raw_value)
        try:
            if raw_value in ("|", "|-"):
                value = _parse_block_scalar(lines, index, end, raw_value)
                kind = "block"
            elif raw_value.startswith(">"):
                raise _Unsafe("UNSUPPORTED_YAML", "不支持 folded 块标量")
            elif scalar_without_comment.startswith("[") and scalar_without_comment.endswith("]"):
                value = _parse_inline_list(scalar_without_comment)
                kind = "list"
                inline_comment = scalar_comment
                has_nested_comments = bool(scalar_comment)
            elif end > index + 1:
                value, has_nested_comments = _parse_list_block(lines, index, end)
                kind = "list"
            else:
                inline_comment = scalar_comment
                value, kind = _parse_scalar(scalar_without_comment)
        except _Unsafe as exc:
            diag = exc.diagnostic
            diagnostics.append(Diagnostic(
                diag.code,
                "%s（字段 %s）" % (diag.message, key),
                index + 1,
                diag.severity,
            ))
            value = raw_value
            kind = "unsupported"
        fields[key] = FieldRange(
            key=key,
            value=value,
            raw_value=raw_value,
            kind=kind,
            start_line=index,
            end_line=end,
            eol=eol,
            inline_comment=inline_comment,
            has_nested_comments=has_nested_comments,
        )
        index = end

    return ParseResult(
        ok=not any(item.severity == "error" for item in diagnostics),
        fields=fields,
        diagnostics=diagnostics,
        lines=lines,
        close_index=close_index,
        bom=bom,
        newline=newline,
    )


def _safe_plain_scalar(text: str) -> bool:
    if not text or text != text.strip():
        return False
    if text.lower() in {
        "true", "false", "null", "yes", "no", "on", "off", "~",
    }:
        return False
    if _INTEGER_RE.fullmatch(text) or _NUMBER_RE.fullmatch(text):
        return False
    if _UUID_RE.fullmatch(text):
        return False
    if text[0] in "-?:,[]{}#&*!|>%@`\"'":
        return False
    if ": " in text or " #" in text or "\n" in text or "\r" in text:
        return False
    if text.startswith(("!", "{", "[")):
        return False
    return True


def _serialize_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise _Unsafe("INVALID_NUMBER", "数值必须是有限值")
        return format(value, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _Unsafe("INVALID_NUMBER", "数值必须是有限值")
        return repr(value)
    if isinstance(value, str):
        if _safe_plain_scalar(value):
            return value
        return json.dumps(value, ensure_ascii=False)
    raise _Unsafe(
        "UNSUPPORTED_VALUE_TYPE",
        "不支持写入 %s" % type(value).__name__,
    )


def _serialize_field(key: str, value: Any, inline_comment: str = "") -> list[str]:
    if not _KEY_RE.match(key + ": "):
        raise _Unsafe("INVALID_KEY", "字段名不在受限 YAML 子集内")
    if isinstance(value, str) and "\n" in value.replace("\r\n", "\n"):
        lines = [key + ": |-"]
        for line in value.replace("\r\n", "\n").split("\n"):
            lines.append(("  " + line) if line else "")
        return lines
    if isinstance(value, (list, tuple)):
        items = list(value)
        if not items:
            return [key + ": []"]
        if any(not isinstance(item, str) for item in items):
            raise _Unsafe("UNSUPPORTED_LIST_ITEM", "列表只支持字符串元素")
        lines = [key + ":"]
        for item in items:
            lines.append("  - " + _serialize_scalar(item))
        return lines
    rendered = _serialize_scalar(value)
    return [key + ": " + rendered + inline_comment]


def patch_frontmatter(raw: bytes, updates: Mapping[str, Any]) -> PatchResult:
    parsed = parse_frontmatter(raw)
    if not parsed.ok:
        return PatchResult(False, raw, parsed.diagnostics, False)
    if parsed.close_index is None:
        return PatchResult(
            False,
            raw,
            [Diagnostic("NO_CLOSING_FRONTMATTER", "缺少 Frontmatter 结束标记")],
            False,
        )
    if not isinstance(updates, Mapping) or not updates:
        return PatchResult(True, raw, [], False)

    try:
        replacements: list[tuple[int, int, list[str]]] = []
        insert_lines: list[str] = []
        changed_fields: list[str] = []
        for key, value in updates.items():
            if not isinstance(key, str) or not _KEY_RE.match(key + ": "):
                raise _Unsafe("INVALID_KEY", "字段名不在受限 YAML 子集内")
            existing = parsed.fields.get(key)
            if existing and existing.kind == "unsupported":
                raise _Unsafe(
                    "UNSAFE_TARGET_FIELD",
                    "目标字段语法越界，不能修改",
                )
            if existing and isinstance(value, (list, tuple)) and existing.has_nested_comments:
                raise _Unsafe(
                    "COMMENT_PRESERVATION_UNSAFE",
                    "列表字段含行内注释，无法安全重建",
                )
            if (existing and existing.inline_comment
                    and (isinstance(value, (list, tuple))
                         or (isinstance(value, str) and "\n" in value.replace("\r\n", "\n")))):
                raise _Unsafe(
                    "COMMENT_PRESERVATION_UNSAFE",
                    "目标字段的行内注释无法随复合值安全保留",
                )
            if value is REMOVE_FIELD:
                if existing:
                    replacements.append((existing.start_line, existing.end_line, []))
                    changed_fields.append(key)
                continue
            eol = existing.eol if existing else parsed.newline
            rendered = _serialize_field(
                key,
                value,
                existing.inline_comment if existing and existing.kind not in ("list", "block") else "",
            )
            rendered = [line + eol for line in rendered]
            if existing:
                replacements.append((existing.start_line, existing.end_line, rendered))
            else:
                insert_lines.extend(rendered)
            changed_fields.append(key)

        new_lines = list(parsed.lines)
        original_close_index = parsed.close_index
        for start, end, replacement in sorted(replacements, reverse=True):
            new_lines[start:end] = replacement
        close_before_insert = original_close_index + sum(
            len(replacement) - (end - start)
            for start, end, replacement in replacements
            if start < original_close_index
        )
        if insert_lines:
            new_lines[close_before_insert:close_before_insert] = insert_lines
            close_index = close_before_insert + len(insert_lines)
        else:
            close_index = close_before_insert

        text = "".join(new_lines)
        new_data = text.encode("utf-8")
        if parsed.bom:
            new_data = codecs.BOM_UTF8 + new_data

        reparsed = parse_frontmatter(new_data)
        if not reparsed.ok:
            return PatchResult(False, raw, reparsed.diagnostics, False)

        original_body_text = "".join(parsed.lines[original_close_index:])
        patched_body_text = "".join(new_lines[close_index:])
        if patched_body_text != original_body_text:
            return PatchResult(
                False,
                raw,
                [Diagnostic("BODY_CHANGED", "Patch 意外改变了正文")],
                False,
            )
        return PatchResult(
            True,
            new_data,
            [],
            new_data != raw,
            tuple(dict.fromkeys(changed_fields)),
        )
    except _Unsafe as exc:
        return PatchResult(False, raw, [exc.diagnostic], False)


def read_field(raw: bytes, key: str) -> Any:
    parsed = parse_frontmatter(raw)
    field = parsed.fields.get(key)
    return field.value if field else None
