"""Validation and runtime metadata extraction for Tater wake bundles."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urljoin, urlparse


_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(
    value: Any,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return round(max(minimum, min(maximum, result)), 3)


def _integer(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))


def _has_model(section: Any, suffixes: tuple[str, ...]) -> bool:
    if not isinstance(section, dict):
        return False
    direct = _text(section.get("model"))
    if direct.lower().endswith(suffixes):
        return True
    artifacts = section.get("artifacts")
    return bool(
        isinstance(artifacts, dict)
        and any(
            isinstance(row, dict)
            and _text(row.get("file") or row.get("path")).lower().endswith(suffixes)
            for row in artifacts.values()
        )
    )


def _has_verified_model(section: Any, suffixes: tuple[str, ...]) -> bool:
    if not isinstance(section, dict):
        return False
    direct = _text(section.get("model"))
    digest = _text(section.get("model_sha256"))
    try:
        size = int(section.get("model_size_bytes") or 0)
    except (TypeError, ValueError):
        size = 0
    if direct.lower().endswith(suffixes) and _SHA256.fullmatch(digest) and size > 0:
        return True
    artifacts = section.get("artifacts")
    if not isinstance(artifacts, dict):
        return False
    for row in artifacts.values():
        if not isinstance(row, dict):
            continue
        try:
            artifact_size = int(row.get("size_bytes") or 0)
        except (TypeError, ValueError):
            artifact_size = 0
        if (
            _text(row.get("file") or row.get("path")).lower().endswith(suffixes)
            and _SHA256.fullmatch(_text(row.get("sha256")))
            and artifact_size > 0
        ):
            return True
    return False


def _revision(section: Any, raw: bytes) -> str:
    if isinstance(section, dict):
        for key in ("model_sha256", "manifest_sha256", "metadata_sha256"):
            candidate = _text(section.get(key))
            if _SHA256.fullmatch(candidate):
                return candidate.lower()
    return hashlib.sha256(raw).hexdigest()


def _esphome_manifest_url(bundle_url: str, payload: dict[str, Any], micro: dict[str, Any]) -> str:
    manifest = _text(micro.get("manifest"))
    if manifest.lower().endswith(".json"):
        return urljoin(bundle_url, manifest)
    key = _text(payload.get("key"))
    if not key:
        filename = urlparse(bundle_url).path.rsplit("/", 1)[-1]
        key = filename.removesuffix(".wake-bundle.json")
    if not key:
        raise ValueError("The wake bundle does not identify its ESPHome manifest.")
    return urljoin(bundle_url, f"{key}.esphome.json")


def parse_wake_bundle(
    payload: Any,
    bundle_url: str,
    raw: bytes,
    *,
    required_engine: str = "",
    nww_architecture: str = "cnn",
) -> dict[str, Any]:
    """Validate a schema-v1/v3 bundle and return firmware profile metadata."""
    if not isinstance(payload, dict):
        raise ValueError("The Tater wake bundle must contain a JSON object.")
    schema = payload.get("schema_version")
    if schema not in {1, 2, 3} or _text(payload.get("type")) != "tater_wake_word_bundle":
        raise ValueError("The URL is not a supported Tater wake bundle.")

    micro = payload.get("micro_wake_word")
    oww = payload.get("open_wake_word")
    nano = payload.get("nano_wake_word")
    strict_artifacts = schema == 3
    has_mww = isinstance(micro, dict) and (
        _has_verified_model(micro, (".tflite",))
        if strict_artifacts
        else _has_model(micro, (".tflite",))
    )
    has_oww = (
        _has_verified_model(oww, (".onnx", ".tflite"))
        if strict_artifacts
        else _has_model(oww, (".onnx", ".tflite"))
    )

    variants = nano.get("variants") if isinstance(nano, dict) else {}
    valid_variants: dict[str, dict[str, Any]] = {}
    if isinstance(variants, dict):
        for architecture in ("cnn", "gru"):
            variant = variants.get(architecture)
            if isinstance(variant, dict) and (
                _has_verified_model(variant, (".onnx",))
                if strict_artifacts
                else _has_model(variant, (".onnx",))
            ):
                valid_variants[architecture] = variant

    architecture = _text(nww_architecture).lower()
    if architecture not in {"cnn", "gru"}:
        architecture = "cnn"
    if required_engine == "mww" and not has_mww:
        raise ValueError("The selected wake bundle does not contain a microWakeWord model.")
    if required_engine == "oww" and not has_oww:
        raise ValueError("The selected wake bundle does not contain an openWakeWord model.")
    if required_engine == "nww" and architecture not in valid_variants:
        preferred = _text(
            nano.get("default_architecture") if isinstance(nano, dict) else ""
        ).lower()
        if preferred in valid_variants:
            architecture = preferred
        elif valid_variants:
            architecture = next(iter(valid_variants))
        else:
            raise ValueError(
                "The selected wake bundle does not contain a NanoWakeWord "
                "CNN or GRU model."
            )
    if required_engine in {"oww", "nww"} and not has_mww:
        raise ValueError(
            "The selected wake bundle needs a microWakeWord model for "
            "satellites that cannot run the companion engine."
        )

    micro = micro if isinstance(micro, dict) else {}
    micro_runtime = micro.get("micro") if isinstance(micro.get("micro"), dict) else {}
    tater_runtime = (
        micro.get("tater_native")
        if isinstance(micro.get("tater_native"), dict)
        else {}
    )
    oww = oww if isinstance(oww, dict) else {}
    selected_nww = valid_variants.get(architecture, {})
    result: dict[str, Any] = {
        "schema_version": int(schema),
        "wake_word": _text(
            payload.get("wake_word") or payload.get("label") or payload.get("key")
        ),
        "bundle_has_mww": has_mww,
        "bundle_has_oww": has_oww,
        "manifest_url": _esphome_manifest_url(bundle_url, payload, micro)
        if has_mww
        else "",
        "wake_model_revision": _revision(micro, raw),
        "oww_model_revision": _revision(oww, raw),
        "nww_model_revision": _revision(selected_nww or nano, raw),
        "nww_available_architectures": ",".join(valid_variants),
        "nww_architecture": architecture,
        "wake_profile_threshold": _number(
            micro_runtime.get("probability_cutoff"),
            0.98,
            minimum=0.01,
            maximum=0.99,
        ),
        "wake_profile_sliding_window": _integer(
            micro_runtime.get("sliding_window_size"),
            5,
            minimum=1,
            maximum=10,
        ),
        "wake_profile_close_miss_threshold": _number(
            tater_runtime.get("close_miss_threshold"),
            0.81,
            minimum=0.01,
            maximum=0.99,
        ),
        "oww_profile_threshold": _number(
            oww.get("recommended_threshold"),
            0.99,
            minimum=0.01,
            maximum=1.0,
        ),
        "oww_profile_patience": _integer(
            oww.get("recommended_patience"),
            3,
            minimum=1,
            maximum=20,
        ),
        "oww_profile_confirmation_threshold": _number(
            oww.get("recommended_confirmation_threshold"),
            0.92,
            minimum=0.01,
            maximum=1.0,
        ),
        "oww_profile_confirmation_patience": _integer(
            oww.get("recommended_confirmation_patience"),
            3,
            minimum=1,
            maximum=20,
        ),
    }
    for candidate in ("cnn", "gru"):
        variant = valid_variants.get(candidate, {})
        result[f"nww_{candidate}_profile_threshold"] = _number(
            variant.get("recommended_threshold"),
            0.99,
            minimum=0.01,
            maximum=1.0,
        )
        result[f"nww_{candidate}_profile_patience"] = _integer(
            variant.get("recommended_patience"),
            3,
            minimum=1,
            maximum=20,
        )
    result["nww_profile_threshold"] = result[
        f"nww_{architecture}_profile_threshold"
    ]
    result["nww_profile_patience"] = result[
        f"nww_{architecture}_profile_patience"
    ]
    return result
