"""
Turns a chat message into the text ElevenLabs reads aloud.

Every spoken unit remembers the (field, line) it came from, so the frontend
can highlight the element being read. The line numbers are a contract with
frontend/src/lib/markdownParser.tsx, which walks the same markdown the same
way: split on "\n", count lines from 1, skip blank lines and horizontal
rules, and treat a fenced code block or display math ($$ ... $$) as one
element owned by its opening line. If that parser changes how it walks
lines, change `_markdown_units` to match.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass

SUMMARY = "summary"
FULL = "full"
SCOPES = (SUMMARY, FULL)

# Reading code aloud symbol by symbol is useless and expensive, so each code
# block is replaced by one short sentence while the block is highlighted.
CODE_BLOCK_CUE = "There's a code example on screen."
# TeX read aloud is just as useless, so display math gets the same treatment.
EQUATION_CUE = "There's an equation on screen."

UNIT_SEPARATOR = "\n\n"

# Same patterns, in the same order, as markdownParser.tsx.
_HORIZONTAL_RULE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_DISPLAY_MATH_DELIMITERS = (("$$", "$$"), ("\\[", "\\]"))
_LINE_PATTERNS = (
    re.compile(r"^(#{1,6})\s+(.+)$"),  # heading
    re.compile(r"^>\s*(.*)$"),  # blockquote
    re.compile(r"^[-*]\s+(.+)$"),  # bullet item
    re.compile(r"^\d+\.\s+(.+)$"),  # numbered item
)
_INLINE_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
# Underscores are left alone: they are far more often part of an identifier
# like snake_case than markdown emphasis.
_EMPHASIS_MARKERS = re.compile(r"\*\*|__|~~|\*")
_TERMINAL_PUNCTUATION = ".!?:;"
# How many opening characters identify a unit inside the alignment.
_PROBE_LENGTH = 24


@dataclass(frozen=True)
class SpeechUnit:
    field: str
    line: int
    text: str


def is_lesson(message_json):
    return isinstance(message_json, dict) and bool(message_json)


def normalise_scope(message_json, scope):
    """A plain reply has no separate summary, so both scopes are one clip."""
    return scope if is_lesson(message_json) else FULL


def build_units(message_text, message_json, scope):
    """
    Units in reading order. A lesson's summary is its title and breakdown;
    the full narration adds the explanation. A plain reply is its text.
    """
    if not is_lesson(message_json):
        return _markdown_units("text", message_text or "")

    units = []
    title = _clean_inline(message_json.get("lesson_title") or "")
    if title:
        units.append(SpeechUnit("title", 1, _with_full_stop(title)))
    units += _markdown_units("breakdown", message_json.get("breakdown") or "")
    if scope == FULL:
        units += _markdown_units("explanation", message_json.get("explanation") or "")
    return units


def join_units(units):
    """The exact text sent to ElevenLabs, and where each unit starts in it."""
    offsets, cursor = [], 0
    for unit in units:
        offsets.append(cursor)
        cursor += len(unit.text) + len(UNIT_SEPARATOR)
    return UNIT_SEPARATOR.join(unit.text for unit in units), offsets


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def units_payload(units):
    """Units as JSON, for the browser voice to read when ElevenLabs can't."""
    return [{"field": u.field, "line": u.line, "text": u.text} for u in units]


def build_marks(units, offsets, characters, start_times):
    """
    When each unit starts playing, from ElevenLabs' per-character timings.

    The alignment normally lists exactly the characters that were sent, so
    each unit is found by searching forward for its opening characters. If
    normalisation changed them (numbers spelled out, for instance), the
    unit's relative position in the text is used instead: highlighting then
    drifts slightly rather than breaking.
    """
    if not units or not characters or len(start_times) != len(characters):
        return []

    spoken = "".join(characters)
    last_index = len(characters) - 1
    total_length = offsets[-1] + len(units[-1].text)
    marks, cursor, previous_start = [], 0, 0.0

    for unit, offset in zip(units, offsets):
        probe = unit.text[:_PROBE_LENGTH]
        index = spoken.find(probe, cursor)
        if index == -1:
            estimate = round(offset / total_length * last_index)
            index = min(last_index, max(cursor, estimate))
        else:
            cursor = index + len(probe)
        # Never let a fallback estimate make time run backwards.
        start = max(previous_start, round(float(start_times[index]), 3))
        marks.append({"field": unit.field, "line": unit.line, "start": start})
        previous_start = start

    return marks


def _markdown_units(field, markdown):
    units = []
    in_code_block = False
    math_close = None

    for line_number, line in enumerate(markdown.split("\n"), start=1):
        stripped = line.strip()

        if math_close:
            if stripped.endswith(math_close):
                math_close = None
            continue
        if stripped.startswith("```"):
            if not in_code_block:
                units.append(SpeechUnit(field, line_number, CODE_BLOCK_CUE))
            in_code_block = not in_code_block
            continue
        if in_code_block or not stripped or _HORIZONTAL_RULE.match(stripped):
            continue

        display_math = _display_math(stripped)
        if display_math:
            close, complete = display_math
            units.append(SpeechUnit(field, line_number, EQUATION_CUE))
            if not complete:
                math_close = close
            continue

        content = stripped
        for pattern in _LINE_PATTERNS:
            match = pattern.match(stripped)
            if match:
                content = match.group(match.lastindex)
                break

        text = _clean_inline(content)
        if text:
            units.append(SpeechUnit(field, line_number, _with_full_stop(text)))

    return units


def _display_math(line):
    """
    (close, complete) when the line starts display math, else None. Mirrors
    matchDisplayMath in markdownParser.tsx: `complete` means the equation ends
    on this line, otherwise a block runs until a line ending in `close`. An
    equation followed by more text is not display math.
    """
    for open_, close in _DISPLAY_MATH_DELIMITERS:
        if not line.startswith(open_):
            continue
        rest = line[len(open_) :]
        close_at = rest.find(close)
        if close_at == -1:
            return close, False
        if close_at == len(rest) - len(close):
            return close, True
        return None
    return None


def _clean_inline(text):
    """Markdown syntax and emoji out; the words of inline code stay in."""
    pieces, last = [], 0
    for match in _INLINE_CODE.finditer(text):
        pieces.append(_strip_markup(text[last : match.start()]))
        pieces.append(match.group(1))
        last = match.end()
    pieces.append(_strip_markup(text[last:]))

    spoken = "".join(ch for ch in "".join(pieces) if not _is_symbol(ch))
    return re.sub(r"\s+", " ", spoken).strip()


def _strip_markup(text):
    return _EMPHASIS_MARKERS.sub("", _LINK.sub(r"\1", text))


def _is_symbol(ch):
    # Emoji and pictographs, plus the invisible characters that join them.
    return unicodedata.category(ch) == "So" or ch in "️‍"


def _with_full_stop(text):
    """Headings and list items rarely end in punctuation; without it the
    voice runs straight into the next line."""
    return text if text[-1] in _TERMINAL_PUNCTUATION else f"{text}."
