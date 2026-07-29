"""Minimal localhost-only Ollama client."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener


class LocalEndpointError(RuntimeError):
    pass


def _loopback_url(endpoint: str, suffix: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise LocalEndpointError("only an HTTP loopback endpoint is allowed")
    return endpoint.rstrip("/") + suffix


def _post_json(endpoint: str, suffix: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = Request(
        _loopback_url(endpoint, suffix),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise LocalEndpointError("localhost endpoint returned a non-object")
    return value


def _get_json(endpoint: str, suffix: str, timeout: float) -> dict[str, Any]:
    opener = build_opener(ProxyHandler({}))
    with opener.open(_loopback_url(endpoint, suffix), timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise LocalEndpointError("localhost endpoint returned a non-object")
    return value


def model_info(*, endpoint: str, model: str, timeout: float = 30.0) -> dict[str, Any]:
    payload = _post_json(endpoint, "/api/show", {"model": model}, timeout)
    tags = _get_json(endpoint, "/api/tags", timeout).get("models", [])
    matching = next(
        (item for item in tags if isinstance(item, dict) and item.get("name") == model), None
    )
    if matching is None or not matching.get("digest"):
        raise LocalEndpointError("model tag has no observable artifact digest")
    version = _get_json(endpoint, "/api/version", timeout).get("version")
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    return {
        "model": model,
        "modified_at": payload.get("modified_at"),
        "family": details.get("family"),
        "parameter_size": details.get("parameter_size"),
        "quantization_level": details.get("quantization_level"),
        "artifact_digest": matching["digest"],
        "size_bytes": matching.get("size"),
        "runtime_version": version,
        "requested_context": 4096,
        "modelfile_digest": __import__("hashlib").sha256(
            str(payload.get("modelfile", "")).encode("utf-8")
        ).hexdigest(),
    }


def generate_json(
    *,
    endpoint: str,
    model: str,
    system: str,
    prompt: str,
    timeout: float = 180.0,
    seed: int = 20260730,
    num_predict: int = 512,
) -> tuple[dict[str, Any], dict[str, Any]]:
    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "system": system,
        "prompt": prompt,
        "options": {"temperature": 0, "seed": seed, "num_predict": num_predict, "num_ctx": 4096},
    }
    payload = _post_json(endpoint, "/api/generate", body, timeout)
    try:
        result = json.loads(payload["response"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise LocalEndpointError("model did not return a JSON object") from exc
    if not isinstance(result, dict):
        raise LocalEndpointError("model JSON response must be an object")
    usage = {
        "model": payload.get("model", model),
        "total_duration_ns": payload.get("total_duration"),
        "load_duration_ns": payload.get("load_duration"),
        "prompt_eval_count": payload.get("prompt_eval_count"),
        "eval_count": payload.get("eval_count"),
    }
    return result, usage
