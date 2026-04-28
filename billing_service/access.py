from __future__ import annotations

from typing import Any, Dict, Mapping

SERVICE_ACCESS_NONE = "none"
SERVICE_ACCESS_REQUEST = "request"
SERVICE_ACCESS_OBSERVE = "observe"
SERVICE_ACCESS_USE = "use"
SERVICE_ACCESS_CONTROL = "control"

SERVICE_ACCESS_ORDER = {
    SERVICE_ACCESS_NONE: 0,
    SERVICE_ACCESS_REQUEST: 1,
    SERVICE_ACCESS_OBSERVE: 2,
    SERVICE_ACCESS_USE: 3,
    SERVICE_ACCESS_CONTROL: 4,
}


def normalize_access_level(value: Any, *, default: str = SERVICE_ACCESS_NONE) -> str:
    cleaned = str(value or default).strip().lower() or default
    if cleaned not in SERVICE_ACCESS_ORDER:
        return default
    return cleaned


def access_at_least(current: Any, required: Any) -> bool:
    current_level = normalize_access_level(current)
    required_level = normalize_access_level(required)
    return SERVICE_ACCESS_ORDER[current_level] >= SERVICE_ACCESS_ORDER[required_level]


def _coerce_groups(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_values = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple, set)):
        raw_values = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw_values = []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(lowered)
    return normalized


def parse_service_access_header(value: Any) -> Dict[str, str]:
    raw = str(value or "").strip()
    if not raw:
        return {}
    payload: Dict[str, str] = {}
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        service_key, access_level = item.split("=", 1)
        cleaned_service = str(service_key or "").strip().lower()
        if not cleaned_service:
            continue
        payload[cleaned_service] = normalize_access_level(access_level)
    return payload


def _coerce_service_access_map(value: Any) -> Dict[str, Dict[str, Any]]:
    payload: Dict[str, Dict[str, Any]] = {}
    if isinstance(value, list):
        items = value
    elif isinstance(value, Mapping):
        items = []
        for service_key, entry in value.items():
            if isinstance(entry, Mapping):
                items.append({"service_key": service_key, **dict(entry)})
            else:
                items.append({"service_key": service_key, "access_level": entry})
    else:
        items = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        service_key = str(item.get("service_key") or item.get("key") or "").strip().lower()
        if not service_key:
            continue
        access_level = normalize_access_level(item.get("access_level") or item.get("level"))
        public_access_level = normalize_access_level(item.get("public_access_level"), default=SERVICE_ACCESS_NONE)
        visible_access_level = normalize_access_level(
            item.get("visible_access_level"),
            default=access_level if access_at_least(access_level, SERVICE_ACCESS_REQUEST) else public_access_level,
        )
        payload[service_key] = {
            **dict(item),
            "service_key": service_key,
            "access_level": access_level,
            "public_access_level": public_access_level,
            "visible_access_level": visible_access_level,
            "visible": bool(item.get("visible")) or visible_access_level != SERVICE_ACCESS_NONE,
            "can_use": access_at_least(access_level, SERVICE_ACCESS_USE),
            "can_control": access_at_least(access_level, SERVICE_ACCESS_CONTROL),
        }
    return payload


def _identity_is_service_account(payload: Mapping[str, Any]) -> bool:
    identity_type = str(payload.get("identity_type") or "").strip().lower()
    role = str(payload.get("role") or "").strip().lower()
    return identity_type == "service_account" or role == "service_account"


def resolve_identity_service_access(
    payload: Mapping[str, Any] | None,
    *,
    debug_value: Any = None,
) -> Dict[str, Dict[str, Any]]:
    identity = dict(payload or {})
    is_service_account = _identity_is_service_account(identity)
    default_role = "service_account" if is_service_account else "user"
    role = str(identity.get("role") or default_role).strip().lower() or default_role
    groups = _coerce_groups(identity.get("groups"))
    authenticated = bool(identity.get("authenticated"))
    resolved = {
        "billing": {
            "service_key": "billing",
            "access_level": SERVICE_ACCESS_NONE,
            "public_access_level": SERVICE_ACCESS_NONE,
            "visible_access_level": SERVICE_ACCESS_NONE,
            "visible": False,
            "can_use": False,
            "can_control": False,
        }
    }
    if authenticated and not is_service_account:
        resolved["billing"] = {
            "service_key": "billing",
            "access_level": SERVICE_ACCESS_USE,
            "public_access_level": SERVICE_ACCESS_NONE,
            "visible_access_level": SERVICE_ACCESS_USE,
            "visible": True,
            "can_use": True,
            "can_control": False,
        }
    if role == "admin" or "admin" in groups:
        resolved["billing"] = {
            "service_key": "billing",
            "access_level": SERVICE_ACCESS_CONTROL,
            "public_access_level": SERVICE_ACCESS_NONE,
            "visible_access_level": SERVICE_ACCESS_CONTROL,
            "visible": True,
            "can_use": True,
            "can_control": True,
        }
    resolved.update(_coerce_service_access_map(identity.get("service_access")))
    for service_key, access_level in parse_service_access_header(debug_value).items():
        current = dict(resolved.get(service_key) or {"service_key": service_key})
        current["access_level"] = normalize_access_level(access_level)
        current["visible_access_level"] = current["access_level"]
        current["visible"] = current["access_level"] != SERVICE_ACCESS_NONE
        current["can_use"] = access_at_least(current["access_level"], SERVICE_ACCESS_USE)
        current["can_control"] = access_at_least(current["access_level"], SERVICE_ACCESS_CONTROL)
        resolved[service_key] = current
    return resolved


def service_access_level(identity: Mapping[str, Any] | None, service_key: str) -> str:
    services = resolve_identity_service_access(identity)
    return normalize_access_level((services.get(str(service_key or "").strip().lower()) or {}).get("access_level"))


def can_use_service(identity: Mapping[str, Any] | None, service_key: str, *, minimum: str = SERVICE_ACCESS_USE) -> bool:
    return access_at_least(service_access_level(identity, service_key), minimum)


def can_use_billing(identity: Mapping[str, Any] | None) -> bool:
    return can_use_service(identity, "billing", minimum=SERVICE_ACCESS_USE)


def can_control_billing(identity: Mapping[str, Any] | None) -> bool:
    return can_use_service(identity, "billing", minimum=SERVICE_ACCESS_CONTROL)
