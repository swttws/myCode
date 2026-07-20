from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


JSONValue = object
JSONRPCId = int | str


@dataclass(frozen=True)
class JSONRPCError:
    code: int
    message: str
    data: Mapping[str, object] | None = None


class JSONRPCMessageKind(str, Enum):
    RESPONSE = "response"
    REQUEST = "request"
    NOTIFICATION = "notification"


@dataclass(frozen=True)
class ParsedJSONRPCMessage:
    kind: JSONRPCMessageKind
    id: JSONRPCId | None = None
    method: str | None = None
    params: Mapping[str, object] | None = None
    result: JSONValue | None = None
    error: JSONRPCError | None = None


class MCPProtocolError(ValueError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def make_request(
    request_id: JSONRPCId,
    method: str,
    params: Mapping[str, object] | None = None,
) -> dict[str, object]:
    _validate_id(request_id)
    _validate_method(method)
    message: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        message["params"] = dict(params)
    return message


def make_notification(
    method: str,
    params: Mapping[str, object] | None = None,
) -> dict[str, object]:
    _validate_method(method)
    message: dict[str, object] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = dict(params)
    return message


def make_success_response(request_id: JSONRPCId, result: JSONValue) -> dict[str, object]:
    _validate_id(request_id)
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def make_error_response(
    request_id: JSONRPCId | None,
    code: int,
    message: str,
    data: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if request_id is not None:
        _validate_id(request_id)
    if isinstance(code, bool) or not isinstance(code, int):
        raise ValueError("JSON-RPC error code must be an integer")
    if not isinstance(message, str) or not message:
        raise ValueError("JSON-RPC error message must be a non-empty string")

    error: dict[str, object] = {"code": code, "message": message}
    if data is not None:
        error["data"] = dict(data)
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def make_cancel_notification(
    request_id: JSONRPCId,
    *,
    reason: str | None = None,
) -> dict[str, object]:
    _validate_id(request_id)
    params: dict[str, object] = {"requestId": request_id}
    if reason is not None:
        params["reason"] = reason
    return make_notification("notifications/cancelled", params)


def parse_message(message: object) -> ParsedJSONRPCMessage:
    if not isinstance(message, dict):
        raise MCPProtocolError("not_object", "JSON-RPC message must be an object")
    if message.get("jsonrpc") != "2.0":
        raise MCPProtocolError("invalid_version", "JSON-RPC version must be 2.0")

    if "method" in message:
        return _parse_method_message(message)
    return _parse_response(message)


def _parse_method_message(message: Mapping[str, object]) -> ParsedJSONRPCMessage:
    method = message.get("method")
    if not isinstance(method, str) or not method:
        raise MCPProtocolError("invalid_method", "JSON-RPC method must be a non-empty string")
    if "result" in message or "error" in message:
        raise MCPProtocolError("invalid_message", "JSON-RPC method message has response fields")

    raw_params = message.get("params", {})
    if not isinstance(raw_params, dict):
        raise MCPProtocolError("invalid_params", "JSON-RPC params must be an object")
    params = dict(raw_params)

    if "id" not in message:
        return ParsedJSONRPCMessage(
            kind=JSONRPCMessageKind.NOTIFICATION,
            method=method,
            params=params,
        )

    request_id = message["id"]
    try:
        _validate_id(request_id)
    except ValueError as exc:
        raise MCPProtocolError("invalid_id", "JSON-RPC request id is invalid") from exc
    return ParsedJSONRPCMessage(
        kind=JSONRPCMessageKind.REQUEST,
        id=request_id,
        method=method,
        params=params,
    )


def _parse_response(message: Mapping[str, object]) -> ParsedJSONRPCMessage:
    has_result = "result" in message
    has_error = "error" in message
    if has_result and has_error:
        raise MCPProtocolError("invalid_response", "JSON-RPC response has result and error")
    if not has_result and not has_error:
        raise MCPProtocolError("invalid_message", "JSON-RPC message is neither request nor response")
    if "id" not in message:
        raise MCPProtocolError("missing_id", "JSON-RPC response is missing id")

    request_id = message["id"]
    try:
        _validate_id(request_id)
    except ValueError as exc:
        raise MCPProtocolError("invalid_id", "JSON-RPC response id is invalid") from exc

    if has_result:
        return ParsedJSONRPCMessage(
            kind=JSONRPCMessageKind.RESPONSE,
            id=request_id,
            result=message["result"],
        )

    error = _parse_error(message["error"])
    return ParsedJSONRPCMessage(
        kind=JSONRPCMessageKind.RESPONSE,
        id=request_id,
        error=error,
    )


def _parse_error(raw: object) -> JSONRPCError:
    if not isinstance(raw, dict):
        raise MCPProtocolError("invalid_error", "JSON-RPC error must be an object")
    code = raw.get("code")
    message = raw.get("message")
    data = raw.get("data")
    if isinstance(code, bool) or not isinstance(code, int):
        raise MCPProtocolError("invalid_error", "JSON-RPC error code must be an integer")
    if not isinstance(message, str) or not message:
        raise MCPProtocolError("invalid_error", "JSON-RPC error message is invalid")
    if data is not None and not isinstance(data, dict):
        raise MCPProtocolError("invalid_error", "JSON-RPC error data must be an object")
    return JSONRPCError(code=code, message=message, data=data)


def _validate_method(method: object) -> None:
    if not isinstance(method, str) or not method:
        raise ValueError("JSON-RPC method must be a non-empty string")


def _validate_id(request_id: object) -> None:
    if isinstance(request_id, bool) or not isinstance(request_id, (int, str)):
        raise ValueError("JSON-RPC id must be an integer or string")
