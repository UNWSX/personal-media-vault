from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any


MEDIA_TYPES = frozenset({"movie", "teleplay", "game", "book"})

PROGRESS_BY_TYPE = {
    "movie": frozenset({"planned", "completed"}),
    "teleplay": frozenset({"planned", "watching", "paused", "completed"}),
    "game": frozenset({"backlog", "playing", "paused", "completed", "abandoned"}),
    "book": frozenset({"planned", "reading", "paused", "completed"}),
}

UNSTARTED = frozenset({"planned", "backlog"})
IN_PROGRESS = frozenset({"watching", "playing", "reading", "paused", "abandoned"})

RATING_FIELDS = (
    "score",
    "myScore",
    "doubanScore",
    "metacriticScore",
    "imdbScore",
    "bangumiScore",
    "rottenTomatoesScore",
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def strict_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value == "true":
            return True
        if value == "false":
            return False
    return None


def normalize_identity(schema_raw: Any, media_id_raw: Any) -> dict:
    warnings = []
    schema = None
    schema_present = schema_raw not in (None, "")
    schema_invalid = False
    if schema_present:
        if isinstance(schema_raw, int) and not isinstance(schema_raw, bool):
            schema = schema_raw
            if schema < 1:
                schema_invalid = True
                warnings.append("INVALID_SCHEMA_VERSION")
        else:
            schema_invalid = True
            warnings.append("INVALID_SCHEMA_VERSION")

    media_id = str(media_id_raw).strip() if media_id_raw is not None else ""
    media_id_valid = bool(_UUID_RE.fullmatch(media_id))
    media_id_invalid = bool(media_id) and not media_id_valid
    if media_id_invalid:
        warnings.append("INVALID_MEDIA_ID")

    future = schema is not None and schema > 2
    if future:
        warnings.append("FUTURE_SCHEMA_VERSION")
    write_allowed = not future and not schema_invalid and not media_id_invalid
    return {
        "schemaVersion": schema,
        "schemaVersionValid": schema == 2,
        "schemaFuture": future,
        "mediaId": media_id or None,
        "mediaIdValid": media_id_valid,
        "identityWarnings": warnings,
        "writeAllowed": write_allowed,
        "identityWriteAllowed": write_allowed,
    }


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("%s必须是数字" % label)
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("%s必须是数字" % label) from exc
    if not number.is_finite():
        raise ValueError("%s必须是有限数字" % label)
    return number


def validated_rating(
    value: Any,
    *,
    label: str,
    minimum: Decimal,
    maximum: Decimal,
    integer: bool = False,
    decimal_places: int | None = None,
) -> Decimal:
    number = _decimal(value, label)
    if number < minimum or number > maximum:
        raise ValueError("%s必须在 %s-%s 之间" % (label, minimum, maximum))
    if integer and number != number.to_integral_value():
        raise ValueError("%s必须是整数" % label)
    if decimal_places is not None:
        quantum = Decimal(1).scaleb(-decimal_places)
        if number != number.quantize(quantum):
            raise ValueError("%s最多保留 %d 位小数" % (label, decimal_places))
    return number


def _read_rating(raw: Any, label: str, **kwargs) -> tuple[Decimal | None, str | None]:
    if raw is None or raw == "":
        return None, None
    try:
        return validated_rating(raw, label=label, **kwargs), None
    except ValueError as exc:
        return None, str(exc)


def _float_or_none(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def default_progress_status(media_type: str, done: bool) -> str | None:
    if media_type not in MEDIA_TYPES:
        return None
    if done:
        return "completed"
    return "backlog" if media_type == "game" else "planned"


def status_group(progress: str | None) -> str:
    if progress in UNSTARTED:
        return "not_started"
    if progress in IN_PROGRESS:
        return "in_progress"
    if progress == "completed":
        return "completed"
    return "unknown"


def normalize_status(media_type: str, status_raw: Any, progress_raw: Any) -> dict:
    status_value = strict_bool(status_raw)
    progress_text = str(progress_raw).strip() if progress_raw is not None else ""
    allowed = PROGRESS_BY_TYPE.get(media_type, frozenset())
    progress_valid = bool(progress_text) and progress_text in allowed
    warnings = []

    if progress_text and not progress_valid:
        resolved_progress = progress_text
        resolved_status = None
        progress_source = "unknown"
        warnings.append("UNKNOWN_PROGRESS_STATUS")
    elif progress_valid:
        resolved_progress = progress_text
        resolved_status = progress_text == "completed"
        progress_source = "explicit"
        if status_value is not None and status_value != resolved_status:
            warnings.append("STATUS_FIELD_MISMATCH")
    elif status_value is not None:
        resolved_progress = default_progress_status(media_type, status_value)
        resolved_status = status_value
        progress_source = "legacy"
    else:
        resolved_progress = None
        resolved_status = None
        progress_source = None
        if status_raw not in (None, ""):
            warnings.append("INVALID_LEGACY_STATUS")

    if progress_text and not progress_valid:
        resolved_group = "unknown"
    elif resolved_progress:
        resolved_group = status_group(resolved_progress)
    elif resolved_status is True:
        resolved_group = "completed"
    elif resolved_status is False:
        resolved_group = "not_started"
    else:
        resolved_group = "unknown"

    return {
        "status": resolved_status,
        "progressStatus": resolved_progress,
        "statusGroup": resolved_group,
        "statusValid": resolved_status is not None,
        "progressStatusValid": resolved_progress in allowed,
        "progressStatusSource": progress_source,
        "warnings": warnings,
    }


def normalize_ratings(media_type: str, fm: dict) -> dict:
    limits = {
        "myScore": (Decimal("0"), Decimal("10"), False, 1),
        "doubanScore": (Decimal("0"), Decimal("10"), False, 1),
        "metacriticScore": (Decimal("0"), Decimal("100"), True, None),
        "imdbScore": (Decimal("0"), Decimal("10"), False, 1),
        "bangumiScore": (Decimal("0"), Decimal("10"), False, 1),
        "rottenTomatoesScore": (Decimal("0"), Decimal("100"), True, None),
    }
    values = {}
    warnings = []
    for key, (minimum, maximum, integer, places) in limits.items():
        value, warning = _read_rating(
            fm.get(key),
            key,
            minimum=minimum,
            maximum=maximum,
            integer=integer,
            decimal_places=places,
        )
        if key == "myScore" and value is not None:
            doubled = value * Decimal("2")
            if doubled != doubled.to_integral_value():
                warnings.append("myScore 必须使用 0.5 步长")
                value = None
        values[key] = value
        if warning:
            warnings.append(warning)

    legacy_score, score_warning = _read_rating(
        fm.get("score"),
        "score",
        minimum=Decimal("-1000000000"),
        maximum=Decimal("1000000000"),
    )
    if score_warning:
        warnings.append(score_warning)

    norm = None
    source = None
    if media_type == "game":
        if values["metacriticScore"] is not None:
            norm = values["metacriticScore"] / Decimal("10")
            source = "metacritic"
        elif legacy_score is not None:
            if Decimal("10") < legacy_score <= Decimal("100"):
                norm = legacy_score / Decimal("10")
                source = "metacritic"
            elif Decimal("0") <= legacy_score <= Decimal("10"):
                norm = legacy_score
                source = "metacritic"
    else:
        if values["doubanScore"] is not None:
            norm = values["doubanScore"]
            source = "douban"
        elif legacy_score is not None and Decimal("0") <= legacy_score <= Decimal("10"):
            norm = legacy_score
            source = "douban"

    return {
        "score": _float_or_none(legacy_score),
        **{key: _float_or_none(value) for key, value in values.items()},
        "normScore": _float_or_none(norm),
        "scoreSource": source,
        "ratingWarnings": warnings,
    }


def normalize_tags(fm: dict) -> list[str]:
    result = []
    seen = set()
    for raw in _as_sequence(fm.get("tags")) + _as_sequence(fm.get("genre")):
        value = str(raw).strip()
        if value.startswith("#"):
            value = value[1:]
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _as_sequence(value: Any) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            return [item.strip() for item in text[1:-1].split(",") if item.strip()]
        return [text]
    return [value]


def _number_for_yaml(number: Decimal) -> int | float:
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def build_item_updates(media_type: str, fm: dict, payload: dict) -> dict:
    updates: dict = {}
    legacy_rating = None

    if "score" in payload:
        value = payload["score"]
        if value is None or value == "":
            updates["score"] = None
        else:
            limit = Decimal("100") if media_type == "game" else Decimal("10")
            rating = validated_rating(
                value, label="评分", minimum=Decimal("0"), maximum=limit
            )
            updates["score"] = _number_for_yaml(rating)
            legacy_rating = rating

    if "myScore" in payload:
        value = payload["myScore"]
        if value is None or value == "":
            updates["myScore"] = None
        else:
            rating = validated_rating(
                value,
                label="个人评分",
                minimum=Decimal("0"),
                maximum=Decimal("10"),
                decimal_places=1,
            )
            if rating * Decimal("2") != (rating * Decimal("2")).to_integral_value():
                raise ValueError("个人评分必须使用 0.5 步长")
            updates["myScore"] = _number_for_yaml(rating)

    dedicated_primary = "metacriticScore" if media_type == "game" else "doubanScore"
    dedicated_specs = {
        "doubanScore": ("豆瓣评分", Decimal("0"), Decimal("10"), False, 1),
        "metacriticScore": ("Metacritic 评分", Decimal("0"), Decimal("100"), True, None),
        "imdbScore": ("IMDb 评分", Decimal("0"), Decimal("10"), False, 1),
        "bangumiScore": ("Bangumi 评分", Decimal("0"), Decimal("10"), False, 1),
        "rottenTomatoesScore": (
            "烂番茄评分",
            Decimal("0"),
            Decimal("100"),
            True,
            None,
        ),
    }
    primary_value = None
    for key, (label, minimum, maximum, integer, places) in dedicated_specs.items():
        if key not in payload:
            continue
        value = payload[key]
        if value is None or value == "":
            updates[key] = None
            if key == dedicated_primary:
                primary_value = None
            continue
        rating = validated_rating(
            value,
            label=label,
            minimum=minimum,
            maximum=maximum,
            integer=integer,
            decimal_places=places,
        )
        updates[key] = _number_for_yaml(rating)
        if key == dedicated_primary:
            primary_value = rating

    if primary_value is not None:
        updates["score"] = _number_for_yaml(primary_value)
    elif dedicated_primary not in payload and "score" in payload:
        updates[dedicated_primary] = (
            None if legacy_rating is None else _number_for_yaml(legacy_rating)
        )

    if "progressStatus" in payload or "status" in payload:
        progress_payload = payload.get("progressStatus")
        status_payload = payload.get("status")
        if media_type not in MEDIA_TYPES:
            if progress_payload is not None:
                raise ValueError("该媒体类型暂不支持 progressStatus")
            if not isinstance(status_payload, bool):
                raise ValueError("状态必须是布尔值")
            updates["status"] = status_payload
        elif progress_payload is not None:
            progress = str(progress_payload).strip()
            allowed = PROGRESS_BY_TYPE[media_type]
            if progress not in allowed:
                raise ValueError("该媒体类型不支持状态 %s" % progress)
            computed_status = progress == "completed"
            if status_payload is not None:
                if not isinstance(status_payload, bool):
                    raise ValueError("状态必须是布尔值")
                if status_payload != computed_status:
                    raise ValueError("status 与 progressStatus 语义冲突")
            updates["progressStatus"] = progress
            updates["status"] = computed_status
        else:
            if not isinstance(status_payload, bool):
                raise ValueError("状态必须是布尔值")
            updates["progressStatus"] = default_progress_status(media_type, status_payload)
            updates["status"] = status_payload

    for key in ("finishTime", "purchaseDate", "startDate"):
        if key in payload:
            value = payload[key]
            if value is not None and not isinstance(value, str):
                raise ValueError("%s必须使用 YYYY-MM-DD 字符串" % key)
            updates[key] = str(value or "").strip() or None

    if "price" in payload:
        value = payload["price"]
        if value is None or value == "":
            updates["price"] = None
        else:
            price = validated_rating(
                value,
                label="价格",
                minimum=Decimal("0"),
                maximum=Decimal("1000000000"),
                decimal_places=2,
            )
            updates["price"] = _number_for_yaml(price)

    if "platform" in payload:
        updates["platform"] = _string_list(payload["platform"], "平台")
    if "title" in payload:
        value = payload["title"]
        if not isinstance(value, str):
            raise ValueError("标题必须是字符串")
        if value.strip():
            updates["title"] = value.strip()
    if "image" in payload:
        value = payload["image"]
        if value is not None and not isinstance(value, str):
            raise ValueError("封面路径必须是字符串")
        updates["image"] = str(value or "").strip() or None
    if "comment" in payload:
        value = payload["comment"]
        if value is not None and not isinstance(value, str):
            raise ValueError("个人评价必须是字符串")
        updates["comment"] = str(value or "")
    return updates


def _string_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.split("/")
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise ValueError("%s必须是字符串或列表" % label)
    if any(not isinstance(item, str) for item in values):
        raise ValueError("%s中的每一项都必须是字符串" % label)
    return [item.strip() for item in values if item.strip()]
