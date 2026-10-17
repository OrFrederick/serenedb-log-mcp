"""Messages in retrieval rows: capped at 240 characters, with the matched words marked."""

from __future__ import annotations

from serenedb_log_mcp.tools._retrieval import MARK, MESSAGE_CHARS, shape_message


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


def test_a_highlighted_passage_from_inside_a_long_message_says_where_text_was_left_out() -> None:
    message = "start " * 10 + "the failed word" + " end" * 10

    text = shape_message(message, "the **failed** word")

    assert text == "…the **failed** word…"


def test_a_cut_through_a_marked_word_keeps_the_marks_balanced_and_the_cap() -> None:
    message = "a" * 230 + " failed " + "b" * 50
    highlighted = "a" * 230 + " **failed** " + "b" * 50

    text = shape_message(message, highlighted)

    assert text is not None
    assert len(text) <= MESSAGE_CHARS
    assert text.count(MARK) % 2 == 0
    assert text.endswith("…")


def test_a_highlight_that_cannot_be_located_in_the_message_is_still_capped() -> None:
    text = shape_message("short", "**other** " + "z" * 300)

    assert text is not None
    assert len(text) <= MESSAGE_CHARS


def test_punctuation_the_highlighter_drops_at_the_edges_is_put_back_without_an_ellipsis() -> None:
    # Measured on 26.10.1: ts_highlight leaves out a leading '[' and the trailing '.'.
    message = "[instance: e9a1] Took 19.49 seconds to spawn the instance on the hypervisor."
    highlighted = "instance: e9a1] Took 19.49 seconds to **spawn** the instance on the hypervisor"

    assert shape_message(message, highlighted) == (
        "[instance: e9a1] Took 19.49 seconds to **spawn** the instance on the hypervisor."
    )
