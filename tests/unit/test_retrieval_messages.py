"""Messages in retrieval rows: capped at 240 characters, with the matched words marked."""

from __future__ import annotations

from serenedb_log_mcp.tools._retrieval import ELLIPSIS, HIGHLIGHT_OPTIONS, MARK, MESSAGE_CHARS, shape_message


def test_a_short_message_is_returned_unchanged() -> None:
    assert shape_message("data TLB error interrupt") == "data TLB error interrupt"


def test_no_message_stays_none() -> None:
    assert shape_message(None) is None


def test_a_long_message_is_cut_to_the_cap_with_an_ellipsis() -> None:
    text = shape_message("x" * 600)

    assert text is not None
    assert len(text) == MESSAGE_CHARS
    assert text.endswith("…")


def test_a_highlight_of_the_whole_message_is_returned_with_its_marks() -> None:
    assert (
        shape_message("ciod: failed to read", "**ciod**: **failed** to read")
        == "**ciod**: **failed** to read"
    )


def test_a_cut_through_a_marked_word_keeps_the_marks_balanced_and_the_cap() -> None:
    # The match starts before MARK_BY, so the window stays at the start and the cut runs through the
    # long matched token.
    message = "a" * 170 + " " + "x" * 100 + " " + "b" * 100
    highlighted = "a" * 170 + " **" + "x" * 100 + "** " + "b" * 100

    text = shape_message(message, highlighted)

    assert text is not None
    assert len(text) <= MESSAGE_CHARS
    assert text.count(MARK) % 2 == 0
    assert text.endswith("…")


def test_a_highlight_that_cannot_be_located_in_the_message_is_still_capped() -> None:
    text = shape_message("short", "**other** " + "z" * 300)

    assert text is not None
    assert len(text) <= MESSAGE_CHARS


def test_the_highlight_covers_the_whole_message() -> None:
    # MaxWords trimmed short messages that would fit: '...pending task (spawning). Skip.' came back
    # as '**Skip**' (measured on 26.10.1); HighlightAll returns the full text with the marks.
    assert "HighlightAll=true" in HIGHLIGHT_OPTIONS
    assert "MaxWords" not in HIGHLIGHT_OPTIONS


def test_a_fully_highlighted_short_message_is_returned_as_it_is() -> None:
    message = "[instance: 7bcc] During sync_power_state the instance has a pending task (spawning). Skip."
    highlighted = (
        "[instance: 7bcc] During sync_power_state the instance has a pending task (spawning). **Skip**."
    )

    assert shape_message(message, highlighted) == highlighted


def test_a_long_message_whose_only_match_is_near_the_end_keeps_the_match_in_view() -> None:
    message = "word " * 100 + "is in state = COMMUNICATION_ERROR, timedOut = true"
    highlighted = message.replace("state", f"{MARK}state{MARK}")

    text = shape_message(message, highlighted)

    assert text is not None
    assert f"{MARK}state{MARK}" in text
    assert text.startswith(ELLIPSIS)
    assert text.endswith("timedOut = true")  # the tail fits, so nothing is cut after the match
    assert len(text) <= MESSAGE_CHARS


def test_a_match_far_into_a_very_long_message_starts_the_window_shortly_before_it() -> None:
    message = "a" * 300 + " failed " + "b" * 400
    highlighted = message.replace("failed", f"{MARK}failed{MARK}")

    text = shape_message(message, highlighted)

    assert text is not None
    assert text.startswith(ELLIPSIS)
    assert text.endswith(ELLIPSIS)
    assert len(text) == MESSAGE_CHARS
    assert 40 <= text.index(MARK) <= 80  # about 60 characters of context before the match


def test_a_match_near_the_start_of_a_long_message_keeps_the_start() -> None:
    message = "ciod failed " + "x" * 400
    highlighted = message.replace("failed", f"{MARK}failed{MARK}")

    text = shape_message(message, highlighted)

    assert text is not None
    assert text.startswith(f"ciod {MARK}failed{MARK}")
    assert text.endswith(ELLIPSIS)
