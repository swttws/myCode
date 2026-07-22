from mycode.compact.estimator import TokenEstimator
from mycode.compact.models import RequestSnapshot
from mycode.llm import ChatMessage, MessageOrigin
from mycode.tool import ToolDefinition, ToolKind


def test_estimate_text_uses_asymmetric_ascii_and_non_ascii_rates():
    estimator = TokenEstimator()

    assert estimator.estimate_text("") == 0
    assert estimator.estimate_text("abcd") == 1
    assert estimator.estimate_text("\u4f60\u597d") == 2
    assert estimator.estimate_text("abc\u4f60") == 2


def test_snapshot_is_exact_for_an_ascii_request():
    snapshot = TokenEstimator().snapshot([ChatMessage(role="user", content="hello")], [])

    assert snapshot == RequestSnapshot(
        ascii_chars=118,
        non_ascii_chars=0,
        fingerprint="2ffe96c2f55135643c48083a5395a7f609b88e0101086e9a57a6c600c1b96eda",
    )


def test_snapshot_counts_and_fingerprints_non_ascii_request_content():
    snapshot = TokenEstimator().snapshot([ChatMessage(role="user", content="\u4f60\u597d")], [])

    assert snapshot == RequestSnapshot(
        ascii_chars=113,
        non_ascii_chars=2,
        fingerprint="d2dedac969aacbc54a60443d4fddc0322a4d95d1bfad11d860234570bfbac377",
    )


def test_snapshot_includes_message_and_provider_visible_tool_fields_but_not_origin():
    estimator = TokenEstimator()
    message = ChatMessage(
        role="assistant",
        content="\u8c03\u7528",
        tool_call_id="call-1",
        tool_name="read_file",
        tool_arguments="{\"\u8def\u5f84\":\"README.md\"}",
        origin=MessageOrigin.COMPACT_SUMMARY,
    )
    tool = ToolDefinition(
        name="read_file",
        description="\u8bfb\u53d6\u6587\u4ef6",
        parameters={
            "required": ["\u8def\u5f84"],
            "properties": {"\u8def\u5f84": {"description": "\u76ee\u6807\u6587\u4ef6", "type": "string"}},
            "type": "object",
        },
        kind=ToolKind.READ,
        grant_arguments=("\u8def\u5f84",),
    )

    snapshot = estimator.snapshot([message], [tool])

    assert snapshot == RequestSnapshot(
        ascii_chars=284,
        non_ascii_chars=16,
        fingerprint="5737fceeb5186023e4c9f5e112a0809298e93d9994b7e78475b396d4d0a9ce85",
    )
    assert snapshot == estimator.snapshot(
        [
            ChatMessage(
                role="assistant",
                content="\u8c03\u7528",
                tool_call_id="call-1",
                tool_name="read_file",
                tool_arguments="{\"\u8def\u5f84\":\"README.md\"}",
                origin=MessageOrigin.CONVERSATION,
            )
        ],
        [tool],
    )


def test_snapshot_excludes_local_tool_kind_and_permission_metadata():
    estimator = TokenEstimator()
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}}
    read_tool = ToolDefinition("read_file", "Read", parameters, ToolKind.READ, ("path",))
    write_tool = ToolDefinition("read_file", "Read", parameters, ToolKind.WRITE, ("path", "force"))

    read_snapshot = estimator.snapshot([], [read_tool])
    write_snapshot = estimator.snapshot([], [write_tool])

    assert (
        read_snapshot.ascii_chars,
        read_snapshot.non_ascii_chars,
        read_snapshot.fingerprint,
    ) == (
        write_snapshot.ascii_chars,
        write_snapshot.non_ascii_chars,
        write_snapshot.fingerprint,
    )


def test_snapshot_normalizes_mapping_order_without_reordering_sequences():
    estimator = TokenEstimator()
    first_parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path"},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["path"],
    }
    second_parameters = {
        "required": ["path"],
        "properties": {
            "limit": {"minimum": 1, "type": "integer"},
            "path": {"description": "Path", "type": "string"},
        },
        "type": "object",
    }
    first_tool = ToolDefinition("read_file", "Read", first_parameters, ToolKind.READ, ("path",))
    second_tool = ToolDefinition("read_file", "Read", second_parameters, ToolKind.READ, ("path",))
    messages = [ChatMessage(role="user", content="first"), ChatMessage(role="user", content="second")]

    assert estimator.snapshot(messages, [first_tool]) == estimator.snapshot(messages, [second_tool])
    assert estimator.snapshot(messages, [first_tool]) != estimator.snapshot(list(reversed(messages)), [first_tool])
