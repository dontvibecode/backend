import base64
import re
from datetime import datetime

from django.contrib.postgres.search import SearchQuery, SearchVector
from django.db.models import F, Q
from django.db.models.fields.json import KeyTextTransform, KeyTransform

from chatbot.models import Message

DEFAULT_PAGE_SIZE = 12
MAX_PAGE_SIZE = 50
MAX_QUERY_LENGTH = 100


class InvalidCursor(ValueError):
    pass


def lesson_search_vector():
    """The words a shared lesson can be found by: its title, tags and summary."""
    return SearchVector(
        KeyTextTransform("lesson_title", "json"),
        KeyTextTransform("tags", "json"),
        KeyTextTransform("breakdown", "json"),
        config="english",
    )


def update_search_vector(message_id, shared):
    Message.objects.filter(id=message_id).update(
        search_vector=lesson_search_vector() if shared else None
    )


def encode_cursor(shared_at, message_id):
    raw = f"{shared_at.isoformat()}|{message_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor):
    try:
        shared_at, message_id = (
            base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        )
        return datetime.fromisoformat(shared_at), int(message_id)
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidCursor("Invalid cursor") from error


def _prefix_search_query(text):
    """
    "recur pyth" becomes recur:* & pyth:*, so a lesson matches while the user
    is still typing a word. Only word characters reach the query, which keeps
    user input from breaking Postgres's query syntax.
    """
    words = re.findall(r"\w+", text)
    if not words:
        return None
    return SearchQuery(
        " & ".join(f"{word}:*" for word in words),
        search_type="raw",
        config="english",
    )


def search_shared_lessons(query="", cursor=None, limit=DEFAULT_PAGE_SIZE):
    """
    One page of shared lessons, newest first, plus the cursor for the next page
    (None on the last page).

    Pages are cut by position (shared_at, id) rather than by count: the database
    jumps straight to the cursor on the feed index instead of counting past
    every earlier row, and lessons shared mid-scroll can't shift later pages.
    """
    lessons = Message.objects.filter(shared=True)

    search_query = _prefix_search_query(query)
    if search_query is not None:
        lessons = lessons.filter(search_vector=search_query)

    if cursor is not None:
        shared_at, message_id = cursor
        lessons = lessons.filter(
            Q(shared_at__lt=shared_at) | Q(shared_at=shared_at, id__lt=message_id)
        )

    # One extra row reveals whether another page exists without a COUNT query.
    rows = list(
        lessons.order_by("-shared_at", "-id")
        .annotate(
            title=KeyTextTransform("lesson_title", "json"),
            breakdown=KeyTextTransform("breakdown", "json"),
            tags=KeyTransform("tags", "json"),
            author=F("shared_by__username"),
        )
        .values(
            "id", "title", "breakdown", "tags", "author", "shared_at", "likes", "forks"
        )[: limit + 1]
    )

    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = (
        encode_cursor(page[-1]["shared_at"], page[-1]["id"]) if has_more else None
    )
    return page, next_cursor
