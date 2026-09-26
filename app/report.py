"""Deterministic report presentation helpers.

The execution engine owns the report schema.  This module intentionally does not
import that schema so it can be used while the engine and its models are still
being assembled.  The two public entry points are:

``render_markdown(report)``
    Render a concise, evidence-first PR comment.

``to_web_data(report)``
    Convert a mapping or model into JSON-compatible, redacted data for the
    evidence viewer.  The input shape is retained where possible; known
    sections get safe defaults so missing optional fields do not crash a page.

The renderer reports outcomes and human dispositions that are present in the
report.  It never derives a verdict from an outcome.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
from collections.abc import Mapping
from enum import Enum
from typing import Any, Dict, List, Tuple
from urllib.parse import unquote_plus, urlsplit


_REDACTED = "[redacted]"
_LOCAL_PATH_REDACTED = "[local path redacted]"
_MISSING = object()

# Keep this deliberately conservative: a URL path such as /api/v1 is not a
# local path, while the usual workstation and temporary-directory roots are.
_POSIX_LOCAL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:/])"
    r"/(?:Users|home|root|tmp|var|private|workspace|workspaces|mnt|opt|etc|usr|run|srv|build|app|Volumes|System)"
    r"(?:/[^\s<>\"'`|,;)]*)?",
    re.IGNORECASE,
)
_PATH_FILE_EXTENSION = r"(?:py|json|ya?ml|toml|md|txt|log|csv|db|sqlite|js|jsx|ts|tsx|java|go|rs|rb|c|cc|cpp|h|hpp|exe|dll)"
_WINDOWS_SPACED_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\)"
    rf"[^<>\"'`|,;)]*?\.{_PATH_FILE_EXTENSION}(?=$|[\s,;)\]])",
    re.IGNORECASE,
)
_POSIX_SPACED_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:/])/(?!/)"
    rf"[^<>\"'`|,;)]*?\.{_PATH_FILE_EXTENSION}(?=$|[\s,;)\]])",
    re.IGNORECASE,
)
_WINDOWS_LOCAL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\)"
    r"[^\s<>\"'`|,;)]*"
)
_GENERIC_POSIX_LOCAL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:/])/(?!/)"
    r"(?:[^/\s<>\"'`|,;)]+/)+[^/\s<>\"'`|,;)]*"
)
_FILE_URI_RE = re.compile(
    r"(?i)\bfile:(?://)?(?:/[A-Za-z]:)?[^\s<>\"'`|,;)]*"
)
_PATH_KEY_RE = re.compile(
    r"(?:^|[_\-.])(?:checkout|cwd|directory|file|filename|filepath|location|"
    r"path|root|traceback|workspace|worktree)(?:$|[_\-.])",
    re.IGNORECASE,
)

_SECRET_KEY_RE = re.compile(
    r"(?:^|[_\-.])(?:api[_\-.]?key|access[_\-.]?key|access[_\-.]?token|"
    r"auth(?:orization)?|client[_\-.]?secret|credential(?:s)?|cookie|"
    r"id[_\-.]?token|password|passwd|private[_\-.]?key|refresh[_\-.]?token|"
    r"secret(?:[_\-.]?key)?|session[_\-.]?token|token)(?:$|[_\-.])",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_PREFIX = (
    r"\b(?:api[_-]?key|access[_-]?(?:key|token)|auth(?:orization|[_-]?token)?|"
    r"client[_-]?secret|credential(?:s)?|password|passwd|private[_-]?key|"
    r"refresh[_-]?token|secret(?:[_-]?key)?|session[_-]?token|token)\b\s*[:=]\s*"
)
_SECRET_QUOTED_ASSIGNMENT_RE = re.compile(
    rf"(?P<prefix>{_SECRET_ASSIGNMENT_PREFIX})(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(?P<prefix>{_SECRET_ASSIGNMENT_PREFIX})"
    r"(?P<quote>[\"']?)(?P<value>[^\"'\s,;&}]+)(?P=quote)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[^\s,;)}]+")
_AUTH_HEADER_RE = re.compile(
    r"(?i)(\b(?:Authorization|Proxy-Authorization)\s*:\s*)"
    r"(?:Basic|Bearer|Digest|Token)\s+[^\s,;)}]+"
)
_COOKIE_HEADER_RE = re.compile(r"(?i)(\b(?:Cookie|Set-Cookie)\s*:\s*)[^\r\n]+")
_QUERY_PARAMETER_RE = re.compile(
    r"(?P<prefix>[?&])(?P<key>[^=&#\s]+)(?P<separator>=)(?P<value>[^&#\s]*)"
)
_TOKEN_PREFIX_RE = re.compile(
    r"(?i)\b(?:gh[porsu]_[A-Za-z0-9_\-]+|github_pat_[A-Za-z0-9_\-]+|"
    r"glpat-[A-Za-z0-9_\-]+|xox[baprs]-[A-Za-z0-9_\-]+|"
    r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9_\-]+|sk-[A-Za-z0-9_\-]{12,}|"
    r"(?:AKIA|ASIA)[0-9A-Z]{16})"
)
_PEM_RE = re.compile(
    r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.IGNORECASE | re.DOTALL
)
_HTTP_URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>\"'`|]+")


def _is_sensitive_key(key: Any) -> bool:
    """Return whether a field name should have its value redacted."""

    if key is None:
        return False
    text = str(key).strip()
    if not text:
        return False
    if _SECRET_KEY_RE.search(text):
        return True
    # The boundary regex above intentionally handles snake/kebab case.  The
    # compact check covers common camelCase fields such as ``secretKey`` and
    # ``accessToken`` without treating arbitrary words containing ``auth`` as
    # credentials.
    compact = re.sub(r"[^a-z0-9]", "", text.lower())
    sensitive_prefixes = (
        "apikey",
        "accesskey",
        "accesstoken",
        "authorization",
        "clientsecret",
        "credential",
        "cookie",
        "idtoken",
        "password",
        "passwd",
        "privatekey",
        "refreshtoken",
        "secret",
        "sessiontoken",
        "token",
    )
    return any(compact == prefix or compact.startswith(prefix) for prefix in sensitive_prefixes)


def _is_sensitive_query_key(key: str) -> bool:
    """Return whether a URL query key commonly carries a credential.

    Query keys are more varied than mapping field names.  In particular,
    signed Action/artifact URLs use names such as ``X-Amz-Signature`` and
    ``X-Goog-Signature``.  Keep the check conservative so ordinary URL
    parameters remain intact.
    """

    decoded = unquote_plus(key).strip()
    if not decoded:
        return False
    if _is_sensitive_key(decoded):
        return True
    compact = re.sub(r"[^a-z0-9]", "", decoded.lower())
    return compact in {
        "sig",
        "signature",
        "xamzsignature",
        "xgoogsignature",
        "awsaccesskeyid",
        "xapikey",
    } or compact.endswith(("signature", "token", "secret"))


def _redact_query_parameter(match: re.Match[str]) -> str:
    key = match.group("key")
    if not _is_sensitive_query_key(key):
        return match.group(0)
    return f"{match.group('prefix')}{key}{match.group('separator')}{_REDACTED}"


def _redact_http_url(url: str) -> str:
    """Redact URL credentials without treating a URL path as a local path."""

    redacted = _QUERY_PARAMETER_RE.sub(_redact_query_parameter, url)
    return _TOKEN_PREFIX_RE.sub(_REDACTED, redacted)


def _is_valid_http_url(value: str) -> bool:
    """Return whether *value* has an active, absolute HTTP(S) destination."""

    if not isinstance(value, str) or any(character.isspace() for character in value):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc) and bool(hostname)


def _plain(value: Any, seen: set[int] | None = None) -> Any:
    """Best-effort conversion of common model values to plain Python values."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        # JSON consumers should not have to deal with non-standard NaN values.
        return str(value)
    if isinstance(value, Enum):
        return _plain(value.value, seen)
    if isinstance(value, bytes):
        # Do not decode arbitrary bytes: they may contain credentials or binary
        # process output.  The presence of binary evidence is still explicit.
        return "[binary data omitted]"
    if isinstance(value, os.PathLike):
        return os.fspath(value)

    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return "[circular reference omitted]"
    seen.add(identity)
    try:
        if isinstance(value, Mapping):
            result: Dict[str, Any] = {}
            for key, item in value.items():
                plain_key = _plain(key, seen)
                result[str(plain_key)] = _plain(item, seen)
            return result

        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            result = {}
            for field in dataclasses.fields(value):
                result[field.name] = _plain(getattr(value, field.name), seen)
            return result

        # Pydantic v2 and v1, respectively.  Calling these methods is safer
        # than serialising a model's repr, which can contain local paths.
        for method_name in ("model_dump", "dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                try:
                    dumped = method()
                except TypeError:
                    # A few model implementations require keyword arguments.
                    try:
                        dumped = method(exclude_none=False)
                    except Exception:
                        dumped = _MISSING
                except Exception:
                    dumped = _MISSING
                if dumped is not _MISSING and dumped is not value:
                    return _plain(dumped, seen)

        if isinstance(value, (list, tuple)):
            return [_plain(item, seen) for item in value]
        if isinstance(value, (set, frozenset)):
            values = [_plain(item, seen) for item in value]
            return sorted(values, key=lambda item: repr(item))

        attributes = getattr(value, "__dict__", None)
        if isinstance(attributes, Mapping):
            return {
                str(key): _plain(item, seen)
                for key, item in attributes.items()
                if not str(key).startswith("_") and not callable(item)
            }

        return str(value)
    finally:
        seen.discard(identity)


def _is_path_key(key: Any) -> bool:
    if key is None:
        return False
    text = str(key).strip()
    if not text:
        return False
    if _PATH_KEY_RE.search(text):
        return True
    compact = re.sub(r"[^a-z0-9]", "", text.lower())
    return compact in {
        "checkout",
        "cwd",
        "directory",
        "file",
        "filename",
        "filepath",
        "location",
        "path",
        "root",
        "traceback",
        "workspace",
        "worktree",
    }


def _looks_absolute_path(value: str) -> bool:
    stripped = value.strip()
    return bool(
        re.match(r"(?i)^(?:[A-Za-z]:[\\/]|\\\\|/|file:(?://|/))", stripped)
    )


def _redact_text(value: str) -> str:
    """Redact local paths and common credential forms from text evidence."""

    # Protect HTTP(S) URLs while the local-path patterns run.  A query such as
    # ``?path=/home/runner`` is still a URL value, not a filesystem path; only
    # its credential-bearing parameters should be changed.
    url_replacements: Dict[str, str] = {}

    def protect_url(match: re.Match[str]) -> str:
        marker = f"\x00HTTP_URL_{len(url_replacements)}\x00"
        url_replacements[marker] = _redact_http_url(match.group(0))
        return marker

    text = _HTTP_URL_RE.sub(protect_url, value)
    text = _PEM_RE.sub(_REDACTED, text)
    text = _AUTH_HEADER_RE.sub(r"\1" + _REDACTED, text)
    text = _COOKIE_HEADER_RE.sub(r"\1" + _REDACTED, text)
    text = _BEARER_RE.sub(r"\1" + _REDACTED, text)
    text = _SECRET_QUOTED_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('prefix')}{match.group('quote')}{_REDACTED}{match.group('quote')}",
        text,
    )
    text = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('prefix')}{match.group('quote')}{_REDACTED}{match.group('quote')}",
        text,
    )
    text = _QUERY_PARAMETER_RE.sub(_redact_query_parameter, text)
    text = _TOKEN_PREFIX_RE.sub(_REDACTED, text)
    text = _FILE_URI_RE.sub(_LOCAL_PATH_REDACTED, text)
    text = _WINDOWS_SPACED_PATH_RE.sub(_LOCAL_PATH_REDACTED, text)
    text = _POSIX_SPACED_PATH_RE.sub(_LOCAL_PATH_REDACTED, text)
    text = _WINDOWS_LOCAL_PATH_RE.sub(_LOCAL_PATH_REDACTED, text)
    text = _POSIX_LOCAL_PATH_RE.sub(_LOCAL_PATH_REDACTED, text)
    text = _GENERIC_POSIX_LOCAL_PATH_RE.sub(_LOCAL_PATH_REDACTED, text)
    for marker, url in url_replacements.items():
        text = text.replace(marker, url)
    return text


def _sanitize(value: Any, key: Any = None) -> Any:
    """Recursively redact a plain value while retaining its report shape."""

    if _is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            (
                _LOCAL_PATH_REDACTED
                if _looks_absolute_path(str(item_key))
                else _redact_text(str(item_key))
            ): _sanitize(item, item_key)
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        if _is_path_key(key) and _looks_absolute_path(value):
            return _LOCAL_PATH_REDACTED
        return _redact_text(value)
    return value


def _as_report_mapping(report: Any) -> Dict[str, Any]:
    """Convert a supported report input into a mapping, or an empty mapping."""

    plain = _plain(report)
    if isinstance(plain, str):
        # Accepting a JSON string is convenient for CLI adapters, while an
        # arbitrary string is treated as an empty/malformed report.
        try:
            decoded = json.loads(plain)
        except (TypeError, ValueError):
            decoded = {}
        plain = decoded
    if not isinstance(plain, Mapping):
        return {}
    sanitized = _sanitize(plain)
    return dict(sanitized) if isinstance(sanitized, Mapping) else {}


def _set_default_section(data: Dict[str, Any], key: str, default: Any) -> None:
    if key not in data or data[key] is None:
        data[key] = default


def _collection(value: Any) -> List[Any]:
    """Normalize a legacy singular section without changing list ordering."""

    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=lambda item: repr(item))
    return [value]


def to_web_data(report: Any) -> Dict[str, Any]:
    """Return redacted, JSON-compatible data for the evidence viewer.

    The report's fields are retained rather than reduced to a second schema.
    The canonical sections used by the viewer (``run``, ``impact``,
    ``observations``, ``decisions``, ``limits``, and ``links``) are present with
    empty defaults when omitted.  A few historical/planned aliases are copied
    to their canonical name only when the canonical field is missing.  No
    outcome or human disposition is inferred.
    """

    data = _as_report_mapping(report)

    aliases = (
        ("changed_symbols", ("changed", "symbols")),
        ("observations", ("comparisons", "behavior_observations", "results")),
        ("decisions", ("decision", "dispositions")),
        ("limits", ("limitations",)),
        ("links", ("link",)),
        ("impact", ("impact_analysis",)),
    )
    for canonical, candidates in aliases:
        if canonical not in data or data[canonical] is None:
            for candidate in candidates:
                if candidate in data and data[candidate] is not None:
                    data[canonical] = data[candidate]
                    break

    # The shared report keeps changed symbols under the impact result.  Expose
    # that existing evidence at the viewer's canonical top-level section.
    impact_value = data.get("impact")
    if (
        ("changed_symbols" not in data or data["changed_symbols"] is None)
        and isinstance(impact_value, Mapping)
        and "changed_symbols" in impact_value
    ):
        data["changed_symbols"] = impact_value["changed_symbols"]

    run_keys = (
        "repo",
        "repository",
        "base_ref",
        "head_ref",
        "base_sha",
        "base_commit",
        "base",
        "head_sha",
        "head_commit",
        "head",
        "generated_at",
        "created_at",
        "timestamp",
        "status",
    )
    # The canonical engine report stores commit metadata in ``revisions``;
    # early adapters emitted the same fields at the top level.  Copy only
    # fields that are actually present; this is normalization, not invention.
    run_value = data.get("run")
    run = dict(run_value) if isinstance(run_value, Mapping) else {}
    revisions = data.get("revisions")
    for key in run_keys:
        if key in run:
            continue
        if key in data:
            run[key] = data[key]
        elif isinstance(revisions, Mapping) and key in revisions:
            run[key] = revisions[key]
    data["run"] = run

    _set_default_section(data, "changed_symbols", [])
    _set_default_section(data, "observations", [])
    _set_default_section(data, "decisions", [])
    _set_default_section(data, "limits", [])
    for key in ("changed_symbols", "observations", "decisions", "limits"):
        data[key] = _collection(data[key])

    _set_default_section(data, "impact", {})
    _set_default_section(data, "links", {})
    if data["links"] not in ({}, None) and not isinstance(data["links"], Mapping):
        data["links"] = {"link": data["links"]}
    return data


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(mapping: Any, *keys: str, default: Any = _MISSING) -> Any:
    source = _mapping(mapping)
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return default


def _present(mapping: Any, *keys: str) -> Tuple[bool, Any]:
    source = _mapping(mapping)
    for key in keys:
        if key in source:
            return True, source[key]
    return False, None


def _items(value: Any) -> List[Any]:
    if value is _MISSING or value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=lambda item: repr(item))
    if isinstance(value, Mapping):
        return [value]
    return [value]


def _text(value: Any) -> str:
    """Markdown-escape one-line untrusted text."""

    if isinstance(value, (Mapping, list, tuple)):
        value = _json_text(value)
    else:
        value = str(value)
    value = _redact_text(value).replace("\r", " ").replace("\n", " ")
    for character in ("\\", "`", "*", "_", "[", "]", "<", ">", "|", "#"):
        value = value.replace(character, "\\" + character)
    return value


def _inline(value: Any) -> str:
    if isinstance(value, (Mapping, list, tuple)):
        value = _json_text(value)
    else:
        value = str(value)
    value = _redact_text(value).replace("\r", " ").replace("\n", " ")
    # A literal backtick cannot escape a code span reliably.  The unicode
    # spelling keeps the evidence visible without allowing Markdown injection.
    value = value.replace("`", "\\u0060")
    return f"`{value}`"


def _json_text(value: Any) -> str:
    safe = _sanitize(_plain(value))
    try:
        return json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return json.dumps(str(safe), ensure_ascii=False)


def _code_block(value: Any) -> str:
    text = _json_text(value).replace("`", "\\u0060")
    return f"```json\n{text}\n```"


def _value_line(lines: List[str], label: str, value: Any, *, code: bool = False) -> None:
    if code:
        lines.append(f"- **{label}:**")
        lines.append("")
        lines.append(_code_block(value))
    elif isinstance(value, (Mapping, list, tuple)):
        lines.append(f"- **{label}:**")
        lines.append("")
        lines.append(_code_block(value))
    else:
        lines.append(f"- **{label}:** {_inline(value)}")


def _record_label(record: Any, *keys: str) -> Any:
    if not isinstance(record, Mapping):
        return record
    value = _first(record, *keys, default=_MISSING)
    return value if value is not _MISSING else _MISSING


def _node_label(node: Any) -> str:
    if not isinstance(node, Mapping):
        return _inline(node)
    symbol = _first(node, "symbol", "qualified_symbol", "qualified_name", "name", "id", default=_MISSING)
    path = _first(node, "path", "file", "relative_path", default=_MISSING)
    line = _first(node, "line", "lineno", "line_number", default=_MISSING)
    if symbol is _MISSING and path is _MISSING:
        return _inline(_json_text(node))
    pieces: List[str] = []
    if symbol is not _MISSING:
        pieces.append(str(symbol))
    if path is not _MISSING:
        location = str(path)
        if line is not _MISSING:
            location += f":{line}"
        pieces.append(location)
    outside = _first(
        node,
        "outside_diff",
        "caller_outside_diff",
        "outside_changed_file",
        default=_MISSING,
    )
    if len(pieces) > 1:
        result = f"{pieces[0]} ({', '.join(pieces[1:])})"
    else:
        result = pieces[0] if pieces else ""
    if outside is True:
        result += " [outside diff]"
    return _inline(result)


def _path_label(path: Any) -> str:
    if isinstance(path, Mapping):
        nodes = _first(path, "hops", "nodes", "symbols", "chain", "path", default=_MISSING)
        if isinstance(nodes, (list, tuple)):
            label = " → ".join(_node_label(node) for node in nodes)
            flags = [text for key, text in (("outside_diff", "caller outside diff"), ("is_test", "test caller"))
                     if path.get(key) is True]
            return label + (f" [{', '.join(flags)}]" if flags else "")
        source = _first(path, "from", "source", "caller", "start", default=_MISSING)
        target = _first(path, "to", "target", "callee", "end", default=_MISSING)
        if source is not _MISSING or target is not _MISSING:
            left = _node_label(source) if source is not _MISSING else "?"
            right = _node_label(target) if target is not _MISSING else "?"
            return f"{left} → {right}"
        return _inline(_json_text(path))
    if isinstance(path, (list, tuple)):
        return " → ".join(_node_label(node) for node in path)
    return _text(path)


def _render_changed_symbols(lines: List[str], data: Mapping[str, Any]) -> None:
    lines.extend(["### Changed symbols", ""])
    entries = _items(_first(data, "changed_symbols", "changed", "symbols", default=[]))
    if not entries:
        lines.append("- No changed symbols were supplied in the report.")
        lines.append("")
        return
    for entry in entries:
        if isinstance(entry, Mapping):
            label = _record_label(entry, "qualified_symbol", "qualified_name", "symbol", "name", "id")
            if label is _MISSING:
                lines.append(f"- {_inline(_json_text(entry))}")
            else:
                lines.append(f"- **{_inline(label)}**")
                for field_label, field_keys in (
                    ("Path", ("path", "file", "relative_path")),
                    ("Line", ("line", "lineno", "line_number")),
                    ("Kind", ("kind", "type")),
                    ("Change", ("change", "change_type", "reason")),
                ):
                    present, value = _present(entry, *field_keys)
                    if present:
                        lines.append(f"  - **{field_label}:** {_inline(value)}")
        else:
            lines.append(f"- {_inline(entry)}")
    lines.append("")


def _render_impact(lines: List[str], data: Mapping[str, Any]) -> None:
    impact = _mapping(_first(data, "impact", "impact_analysis", default={}))
    paths_value = _first(
        impact,
        "paths",
        "impact_paths",
        "edges",
        "callers",
        "graph_paths",
        default=_MISSING,
    )
    if paths_value is _MISSING:
        paths_value = _first(data, "impact_paths", "paths", default=_MISSING)
    unknown_value = _first(
        impact,
        "unknowns",
        "unknown_edges",
        "unknown",
        "unresolved",
        default=_MISSING,
    )
    if unknown_value is _MISSING:
        unknown_value = _first(data, "unknown_edges", "unknowns", default=_MISSING)

    if paths_value is not _MISSING or unknown_value is not _MISSING or impact:
        lines.extend(["### Impact paths", ""])
        paths = _items(paths_value)
        if paths:
            for path in paths:
                lines.append(f"- {_path_label(path)}")
        else:
            lines.append("- No impact paths were supplied in the report.")
        lines.append("")

    if unknown_value is not _MISSING:
        lines.extend(["### Unknown impact edges", ""])
        unknowns = _items(unknown_value)
        if unknowns:
            for unknown in unknowns:
                if isinstance(unknown, Mapping):
                    reason = _first(unknown, "reason", "detail", "message", default=_MISSING)
                    edge = _path_label(unknown)
                    if reason is not _MISSING:
                        edge += f" — {_text(reason)}"
                    lines.append(f"- {edge}")
                else:
                    lines.append(f"- {_text(unknown)}")
        else:
            lines.append("- No unknown edges were recorded in the supplied impact analysis.")
        lines.append("")


def _observation_revision_value(
    observation: Mapping[str, Any], revision: str
) -> Tuple[bool, Any]:
    if revision == "base":
        direct_output = ("base_output", "base_result", "before_output", "old_output", "before")
        container_keys = ("base", "old", "before_revision")
    else:
        direct_output = ("head_output", "head_result", "after_output", "new_output", "after")
        container_keys = ("head", "new", "after_revision")

    # The shared comparison contract uses ``base`` and ``head`` mappings that
    # carry the revision SHA, status, duration, and output/exception.  Keep
    # that container intact in Markdown; reducing it to only output/exception
    # would discard execution evidence.  Older flattened fields remain a
    # fallback for adapters that predate the canonical contract.
    container_present, container = _present(observation, *container_keys)
    if container_present:
        return True, container

    present, value = _present(observation, *direct_output)
    if present:
        return True, value
    return False, None


def _render_observations(lines: List[str], data: Mapping[str, Any]) -> None:
    lines.extend(["### Behavior observations", ""])
    observations = _items(_first(data, "observations", "comparisons", "results", default=[]))
    if not observations:
        lines.append("- No behavior observations were supplied in the report.")
        lines.append("")
        return

    for index, observation in enumerate(observations, start=1):
        if not isinstance(observation, Mapping):
            lines.append(f"#### Observation {index}")
            lines.append("")
            lines.append(f"- {_inline(observation)}")
            lines.append("")
            continue

        probe = _mapping(_first(observation, "probe", "probe_info", default={}))
        name = _first(
            observation,
            "probe_name",
            "name",
            "identifier",
            "probe_id",
            "id",
            default=_MISSING,
        )
        if name is _MISSING:
            name = _first(probe, "name", "identifier", "id", default=f"Observation {index}")
        lines.extend([f"#### {_inline(name)}", ""])

        outcome_present, outcome = _present(
            observation, "outcome", "behavior_outcome", "classification", "result"
        )
        if outcome_present:
            _value_line(lines, "Outcome", outcome)
        else:
            lines.append("- **Outcome:** `outcome not provided`")
        status_present, observation_status = _present(observation, "status", "execution_status")
        if status_present:
            _value_line(lines, "Status", observation_status)

        probe_hash_present, probe_hash = _present(observation, "probe_hash", "input_hash", "hash")
        if not probe_hash_present:
            probe_hash_present, probe_hash = _present(probe, "probe_hash", "hash")
        if probe_hash_present:
            _value_line(lines, "Probe hash", probe_hash)

        input_present, probe_input = _present(
            observation, "input", "inputs", "probe_input", "arguments", "args"
        )
        if not input_present:
            input_present, probe_input = _present(probe, "input", "inputs", "arguments", "args")
        if input_present:
            _value_line(lines, "Input", probe_input, code=True)

        for revision, label in (("base", "Base output / exception"), ("head", "Head output / exception")):
            present, value = _observation_revision_value(observation, revision)
            if present:
                _value_line(lines, label, value, code=True)

        for field_label, field_keys in (
            ("Execution detail", ("detail", "details", "message", "error", "exception")),
            ("Timeout", ("timeout", "timed_out")),
            ("Setup status", ("setup_status", "setup", "import_status")),
        ):
            present, value = _present(observation, *field_keys)
            if present:
                _value_line(lines, field_label, value, code=isinstance(value, (Mapping, list, tuple)))

        for flag in ("same_on_tested_cases", "delta_observed", "inconclusive"):
            present, value = _present(observation, flag)
            if present:
                _value_line(lines, flag, value)

        nested_decision_present, nested_decision = _present(observation, "decision", "disposition")
        if nested_decision_present:
            _value_line(lines, "Recorded decision", nested_decision, code=isinstance(nested_decision, Mapping))
        lines.append("")


def _render_existing_tests(lines: List[str], data: Mapping[str, Any]) -> None:
    present, tests = _present(data, "tests", "test_results", "existing_tests")
    if not present:
        return
    lines.extend(["### Existing tests (separate from behavior outcomes)", ""])
    entries = _items(tests)
    if not entries:
        lines.append("- No existing-test results were supplied.")
    for entry in entries:
        if isinstance(entry, Mapping):
            name = _first(entry, "name", "test", "id", "revision", default=_MISSING)
            result = _first(entry, "outcome", "status", "result", "passed", default=_MISSING)
            if name is _MISSING and result is _MISSING:
                lines.append(f"- {_code_block(entry)}")
            else:
                label = _inline(name) if name is not _MISSING else "Test"
                value = _inline(result) if result is not _MISSING else "`result not provided`"
                counts = [f"{entry[key]} {key}" for key in ("passed", "failed", "errors")
                          if isinstance(entry.get(key), int)]
                lines.append(f"- **{label}:** {value}" + (f" ({', '.join(counts)})" if counts else ""))
        else:
            lines.append(f"- {_inline(entry)}")
    lines.append("")


def _render_decisions(lines: List[str], data: Mapping[str, Any]) -> None:
    _render_decision_list(
        lines,
        "Human decisions",
        _items(_first(data, "decisions", "dispositions", "decision", default=[])),
        "No human disposition was recorded; this renderer does not decide intent.",
    )
    prior = _items(data.get("prior_decisions"))
    if prior:
        _render_decision_list(lines, "Prior decisions (ledger)", prior, "")


def _render_needs_bob_action(lines: List[str], data: Mapping[str, Any]) -> None:
    refs = _items(data.get("needs_bob_action"))
    if not refs:
        return
    lines.extend(["### Impacted callers without a probe", ""])
    lines.extend(f"- {_node_label(ref)}" for ref in refs)
    lines.append("")


def _render_decision_list(lines: List[str], heading: str, decisions: List[Any], empty: str) -> None:
    lines.extend([f"### {heading}", ""])
    if not decisions:
        lines.append(f"- {empty}")
        lines.append("")
        return

    for index, decision in enumerate(decisions, start=1):
        if not isinstance(decision, Mapping):
            lines.append(f"- {_inline(decision)}")
            continue
        disposition_present, disposition = _present(
            decision, "disposition", "human_disposition", "intent", "decision"
        )
        if disposition_present and disposition not in (None, ""):
            lines.append(f"- **Disposition:** {_inline(disposition)}")
        else:
            lines.append("- **Disposition:** `not recorded`")

        target = decision.get("target")
        if isinstance(target, Mapping):
            lines.append(f"- **Target:** {_node_label(target)}")

        for field_label, field_keys in (
            ("Decision ID", ("decision_id", "id")),
            ("Observation", ("observation_id", "observation")),
            ("Symbol", ("symbol", "qualified_symbol", "name")),
            ("Path", ("path", "file", "relative_path")),
            ("Requirement", ("requirement_ref", "requirement")),
            ("Probe hash", ("probe_hash", "hash")),
            ("Status", ("status", "decision_status", "approval_status", "approved_status")),
            ("Recorded verdict field", ("verdict",)),
        ):
            present, value = _present(decision, *field_keys)
            if present:
                _value_line(lines, field_label, value)

        rationale_present, rationale = _present(decision, "rationale", "reason", "explanation")
        rationale_missing = rationale is None or (
            isinstance(rationale, str) and not rationale.strip()
        )
        if rationale_present and not rationale_missing:
            _value_line(
                lines,
                "Rationale",
                rationale,
                code=isinstance(rationale, (Mapping, list, tuple)),
            )
        elif (
            disposition_present
            and isinstance(disposition, str)
            and disposition.strip().lower() == "intended"
        ):
            lines.append("- **Rationale:** `not provided (required for intended)`")
    lines.append("")


def _render_limits(lines: List[str], data: Mapping[str, Any]) -> None:
    lines.extend(["### Limits", ""])
    limits = _items(_first(data, "limits", "limitations", default=[]))
    if not limits:
        lines.append("- No limit entries were supplied in the report.")
    else:
        for limit in limits:
            if isinstance(limit, Mapping):
                text = _first(limit, "text", "description", "reason", "message", default=_MISSING)
                if text is _MISSING:
                    lines.append(f"- {_code_block(limit)}")
                else:
                    lines.append(f"- {_text(text)}")
            else:
                lines.append(f"- {_text(limit)}")
    lines.append("")


def _markdown_safe_url(url: str) -> str | None:
    """Keep a URL inside an angle-bracket Markdown destination."""

    encoded: List[str] = []
    for character in url:
        codepoint = ord(character)
        if codepoint < 0x20 or character in "<>\\`":
            encoded.append(f"%{codepoint:02X}")
        else:
            encoded.append(character)
    safe_url = "".join(encoded)
    return safe_url or None


def _link_value(value: Any) -> Tuple[str, str] | None:
    label = "Link"
    url: Any = value
    if isinstance(value, Mapping):
        label = _first(value, "label", "name", "title", default=label)
        url = _first(value, "url", "href", "link", default=_MISSING)
        if url is _MISSING:
            return None
    if not isinstance(url, str):
        return None
    safe_url = _redact_text(url)
    safe_url = _markdown_safe_url(safe_url)
    if safe_url is None:
        return None
    return str(label), safe_url


def _render_links(lines: List[str], data: Mapping[str, Any]) -> None:
    links = _first(data, "links", "link", default={})
    if not isinstance(links, Mapping):
        links = {"link": links} if links else {}
    known = (
        ("action_run", "GitHub Action run"),
        ("action_url", "GitHub Action run"),
        ("artifact", "Report artifact"),
        ("artifact_url", "Report artifact"),
        ("pr", "Pull request"),
        ("pull_request", "Pull request"),
    )
    rendered: List[Tuple[str, str]] = []
    seen_keys: set[str] = set()
    for key, label in known:
        if key in links and links[key] not in (None, "") and key not in seen_keys:
            item = _link_value(links[key])
            if item is not None:
                rendered.append((label, item[1]))
            seen_keys.add(key)
    for key in sorted(links, key=str):
        if key in seen_keys or key in {item[0] for item in known}:
            continue
        item = _link_value(links[key])
        if item is not None:
            rendered.append((_text(key), item[1]))
    if not rendered:
        return
    lines.extend(["### Links", ""])
    for label, url in rendered:
        escaped_url = url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
        if _is_valid_http_url(url):
            lines.append(f"- [{_text(label)}](<{escaped_url}>)")
        else:
            # Never turn an unknown or local scheme into an active Markdown link.
            lines.append(f"- **{_text(label)}:** {_inline(url)}")
    lines.append("")


def _render_triage(lines: List[str], triage: Mapping[str, Any]) -> None:
    """What kind of PR this is: the first thing a reviewer reads."""
    if not triage:
        return
    reasons = "; ".join(_text(reason) for reason in _items(triage.get("reasons")))
    skipped = _items(triage.get("skipped_steps"))
    line = f"**Triage:** {_inline(triage.get('profile', 'unknown'))}" + (f" — {reasons}" if reasons else "")
    lines.extend([line + (f". Skipped: {', '.join(_text(step) for step in skipped)}." if skipped else ""), ""])


def _render_analysis(lines: List[str], data: Mapping[str, Any]) -> None:
    """One line naming each analyzed language and its tier; the tier's limits are in Limits."""

    analysis = _mapping(data.get("analysis"))
    if not analysis:
        return
    entries = [_mapping(entry) for entry in _items(analysis.get("languages"))] or [analysis]
    rendered = [
        f"{_inline(entry.get('language', 'unknown'))} (tier {_inline(entry.get('tier', 'unknown'))})"
        for entry in entries
    ]
    lines.extend([f"**Analyzed as:** {', '.join(rendered)}", ""])
    _render_runtime(lines, _mapping(analysis.get("runtime")))


def _render_runtime(lines: List[str], runtime: Mapping[str, Any]) -> None:
    """Which interpreter and probe runner executed this run (present only after a --run)."""
    parts = []
    if runtime.get("python"):
        version = f" {runtime['version']}" if runtime.get("version") else ""
        parts.append(f"{_inline(runtime['python'])}{_text(version)} (chosen by {_inline(runtime.get('source', 'unknown'))})")
    runner = _mapping(runtime.get("probe_runner"))
    if runner:
        parts.append(f"probe runner {_inline(runner.get('path', ''))} ({_text(runner.get('source', ''))}, {_inline(runner.get('sha256', ''))})")
    if parts:
        lines.extend([f"**Ran with:** {'; '.join(parts)}", ""])


def _render_extra_fields(lines: List[str], data: Mapping[str, Any]) -> None:
    known = {
        "schema_version",
        "fixture",
        "analysis",
        "triage",
        "run",
        "revisions",
        "repo",
        "repository",
        "base",
        "head",
        "timestamp",
        "base_sha",
        "head_sha",
        "generated_at",
        "status",
        "changed_symbols",
        "changed",
        "symbols",
        "impact",
        "impact_analysis",
        "impact_paths",
        "paths",
        "unknown_edges",
        "unknowns",
        "observations",
        "comparisons",
        "behavior_observations",
        "results",
        "decisions",
        "decision",
        "dispositions",
        "prior_decisions",
        "needs_bob_action",
        "tests",
        "test_results",
        "existing_tests",
        "limits",
        "limitations",
        "links",
        "link",
        "outcome_counts",
    }
    extras = {key: value for key, value in data.items() if key not in known}
    if not extras:
        return
    lines.extend(["### Additional report fields", "", _code_block(extras), ""])


def render_markdown(report: Any) -> str:
    """Render a deterministic, redacted Markdown evidence report.

    Only explicit report values are displayed as outcomes or dispositions.  In
    particular, ``delta_observed`` is not labelled a bug and
    ``same_on_tested_cases`` is not labelled safe.
    """

    data = to_web_data(report)
    lines: List[str] = ["## Behavior Review", ""]

    if data.get("fixture") is True:
        lines.extend([
            "> **Fixture data:** this report is synthetic and is not execution evidence.",
            "",
        ])

    _render_triage(lines, _mapping(data.get("triage")))
    _render_analysis(lines, data)

    schema_version = data.get("schema_version", _MISSING)
    if schema_version is not _MISSING:
        lines.append(f"**Schema version:** {_inline(schema_version)}")
        lines.append("")

    run = _mapping(data.get("run"))
    if run:
        lines.extend(["### Run", ""])
        for label, keys in (
            ("Repository", ("repo", "repository")),
            ("Status", ("status", "run_status")),
            ("Base commit", ("base_sha", "base_commit", "base")),
            ("Head commit", ("head_sha", "head_commit", "head")),
            ("Generated", ("generated_at", "created_at", "timestamp")),
        ):
            present, value = _present(run, *keys)
            if present:
                _value_line(lines, label, value)
        lines.append("")
    else:
        lines.extend(["### Run", "", "- Run metadata was not supplied.", ""])

    _render_changed_symbols(lines, data)
    _render_impact(lines, data)
    _render_existing_tests(lines, data)
    _render_observations(lines, data)
    _render_needs_bob_action(lines, data)
    _render_decisions(lines, data)
    _render_limits(lines, data)
    _render_links(lines, data)
    _render_extra_fields(lines, data)

    return "\n".join(lines).rstrip() + "\n"


# Compatibility names for the contract wording used by early adapters.
def render(report: Any) -> str:
    """Alias for :func:`render_markdown`."""

    return render_markdown(report)


def web_data(report: Any) -> Dict[str, Any]:
    """Alias for :func:`to_web_data`."""

    return to_web_data(report)


__all__ = ["render_markdown", "to_web_data", "render", "web_data"]
