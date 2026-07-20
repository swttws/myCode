from __future__ import annotations

import pytest

from mycode.mcp.jsonrpc import (
    JSONRPCError,
    JSONRPCMessageKind,
    MCPProtocolError,
    make_cancel_notification,
    make_error_response,
    make_notification,
    make_request,
    make_success_response,
    parse_message,
)


def test_builds_jsonrpc_request_and_notification():
    assert make_request(3, "tools/list", {"cursor": "next"}) == {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/list",
        "params": {"cursor": "next"},
    }
    assert make_notification("notifications/initialized", {}) == {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }


def test_builds_success_error_and_cancel_messages():
    assert make_success_response("ping-1", {}) == {
        "jsonrpc": "2.0",
        "id": "ping-1",
        "result": {},
    }
    assert make_error_response("request-1", -32601, "Method not found") == {
        "jsonrpc": "2.0",
        "id": "request-1",
        "error": {"code": -32601, "message": "Method not found"},
    }
    assert make_cancel_notification(9, reason="timeout") == {
        "jsonrpc": "2.0",
        "method": "notifications/cancelled",
        "params": {"requestId": 9, "reason": "timeout"},
    }


def test_parse_success_response_preserves_id_and_result():
    parsed = parse_message({"jsonrpc": "2.0", "id": 7, "result": {"tools": []}})

    assert parsed.kind is JSONRPCMessageKind.RESPONSE
    assert parsed.id == 7
    assert parsed.result == {"tools": []}
    assert parsed.error is None


def test_parse_error_response_returns_structured_error():
    parsed = parse_message(
        {
            "jsonrpc": "2.0",
            "id": "call-1",
            "error": {"code": -32000, "message": "failed", "data": {"retry": False}},
        }
    )

    assert parsed.kind is JSONRPCMessageKind.RESPONSE
    assert parsed.id == "call-1"
    assert parsed.error == JSONRPCError(-32000, "failed", {"retry": False})


def test_parse_notification_and_server_request():
    notification = parse_message(
        {"jsonrpc": "2.0", "method": "notifications/tools/list_changed", "params": {}}
    )
    request = parse_message({"jsonrpc": "2.0", "id": 4, "method": "ping"})

    assert notification.kind is JSONRPCMessageKind.NOTIFICATION
    assert notification.method == "notifications/tools/list_changed"
    assert request.kind is JSONRPCMessageKind.REQUEST
    assert request.id == 4
    assert request.params == {}


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ([], "not_object"),
        ({"jsonrpc": "1.0", "id": 1, "result": {}}, "invalid_version"),
        ({"jsonrpc": "2.0", "id": 1, "result": {}, "error": {}}, "invalid_response"),
        ({"jsonrpc": "2.0", "result": {}}, "missing_id"),
        ({"jsonrpc": "2.0", "id": 1}, "invalid_message"),
        ({"jsonrpc": "2.0", "method": ""}, "invalid_method"),
        ({"jsonrpc": "2.0", "id": True, "method": "ping"}, "invalid_id"),
        (
            {"jsonrpc": "2.0", "id": 1, "error": {"code": "bad", "message": "failed"}},
            "invalid_error",
        ),
        (
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -1}},
            "invalid_error",
        ),
        ({"jsonrpc": "2.0", "method": "ping", "params": []}, "invalid_params"),
    ],
)
def test_parse_rejects_invalid_messages_without_echoing_payload(message, category):
    secret = "do-not-leak"
    if isinstance(message, dict):
        message["secret"] = secret

    with pytest.raises(MCPProtocolError) as captured:
        parse_message(message)

    assert captured.value.category == category
    assert secret not in str(captured.value)


@pytest.mark.parametrize("method", ["", None, 42])
def test_message_builders_reject_invalid_methods(method):
    with pytest.raises(ValueError, match="method"):
        make_request(1, method)


def test_error_builder_rejects_boolean_error_code():
    with pytest.raises(ValueError, match="error code"):
        make_error_response(1, True, "bad")

