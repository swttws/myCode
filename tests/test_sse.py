from mycode.protocols.sse import SSEEvent, parse_sse_events


def test_parse_single_data_event():
    events = list(parse_sse_events(["data: hello", ""]))

    assert events == [SSEEvent(event=None, data="hello")]


def test_parse_named_event():
    events = list(parse_sse_events(["event: message", "data: hello", ""]))

    assert events == [SSEEvent(event="message", data="hello")]


def test_parse_multiline_data_event():
    events = list(parse_sse_events(["data: hello", "data: world", ""]))

    assert events == [SSEEvent(event=None, data="hello\nworld")]


def test_parse_bytes_and_ignores_comments():
    events = list(parse_sse_events([b": keepalive", b"data: hello", b""]))

    assert events == [SSEEvent(event=None, data="hello")]


def test_parse_done_marker():
    events = list(parse_sse_events(["data: [DONE]", ""]))

    assert events == [SSEEvent(event=None, data="[DONE]")]
