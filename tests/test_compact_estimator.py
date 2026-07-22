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


def test_estimate_text_rounds_non_ascii_character_boundaries_upward():
    estimator = TokenEstimator()

    assert estimator.estimate_text("\u4f60") == 1
    assert estimator.estimate_text("\u4f60\u4f60") == 2
    assert estimator.estimate_text("\u4f60\u4f60\u4f60") == 2
    assert estimator.estimate_text("\u4f60\u4f60\u4f60\u4f60") == 3


def test_snapshot_is_exact_for_an_ascii_request():
    snapshot = TokenEstimator().snapshot([ChatMessage(role="user", content="hello")], [])

    assert snapshot == RequestSnapshot(
        ascii_chars=59,
        non_ascii_chars=0,
        fingerprint="1846a3ccfddb9a0957faa87b06821b6b7eafd16987b8b6723ec5035f1706a4c1",
    )


def test_snapshot_counts_and_fingerprints_non_ascii_request_content():
    snapshot = TokenEstimator().snapshot([ChatMessage(role="user", content="\u4f60\u597d")], [])

    assert snapshot == RequestSnapshot(
        ascii_chars=54,
        non_ascii_chars=2,
        fingerprint="4b46a7327ecc469ed596f986d822cd1c0bafe2f9fdb2ba3d54fb65b4e764ead0",
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
        ascii_chars=299,
        non_ascii_chars=14,
        fingerprint="82de632edc1a2ac09c3d8d2074063e0028e1da67800b182047d0fa49684ed362",
    )
    assert snapshot == estimator.snapshot(
        [
            ChatMessage(
                role="assistant",
                content="\u5ffd\u7565\u7684\u5185\u5bb9",
                tool_call_id="call-1",
                tool_name="read_file",
                tool_arguments="{\"\u8def\u5f84\":\"README.md\"}",
                origin=MessageOrigin.CONVERSATION,
            )
        ],
        [tool],
    )


def test_snapshot_omits_tool_metadata_from_ordinary_messages():
    estimator = TokenEstimator()
    cases = (
        (
            ChatMessage(role="user", content="request"),
            ChatMessage(
                role="user",
                content="request",
                tool_call_id="call-1",
                tool_name="read_file",
                tool_arguments="{}",
            ),
        ),
        (
            ChatMessage(role="assistant", content="response"),
            ChatMessage(role="assistant", content="response", tool_call_id="call-1"),
        ),
        (
            ChatMessage(role="assistant", content="response"),
            ChatMessage(role="assistant", content="response", tool_name="read_file", tool_arguments="{}"),
        ),
    )

    for ordinary_message, message_with_metadata in cases:
        assert estimator.snapshot([ordinary_message], []) == estimator.snapshot([message_with_metadata], [])


def test_snapshot_uses_only_provider_visible_tool_result_fields():
    estimator = TokenEstimator()
    tool_result = ChatMessage(role="tool", content="output", tool_call_id="call-1")

    assert estimator.snapshot([tool_result], []) == RequestSnapshot(
        ascii_chars=84,
        non_ascii_chars=0,
        fingerprint="609f71e9801647f9ab6ebb8720e1665a6f5607704cac9cfe9f5a0d4ca6f2fc4f",
    )
    assert estimator.snapshot([tool_result], []) == estimator.snapshot(
        [
            ChatMessage(
                role="tool",
                content="output",
                tool_call_id="call-1",
                tool_name="ignored",
                tool_arguments="{}",
            )
        ],
        [],
    )
    assert estimator.snapshot([tool_result], []) != estimator.snapshot(
        [ChatMessage(role="tool", content="output", tool_call_id="call-2")], []
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
