"""Template context processors owned by the assistant app."""

from django.contrib.messages.api import MessageFailure, get_messages

#: Hard cap so a backlog can never bury the page in toasts.
MAX_FLASH_MESSAGES = 3


def flash_messages(request):
    """Expose Django's messages framework as ``flash_messages``.

    The built-in ``messages`` context variable is a landmine: any view that
    puts its own ``messages`` key in the context (e.g. the History page, which
    lists inbound messages) shadows it. When that happened the toasts rendered
    every inbound message in bulk *and* the real flash messages were never
    consumed, so they piled up and re-appeared later. Using a dedicated name —
    plus de-duplication and a hard cap — keeps the notification area clean and
    predictable.
    """
    try:
        storage = get_messages(request)
    except MessageFailure:  # middleware/app not installed
        return {"flash_messages": []}

    seen, unique = set(), []
    for message in storage:
        key = (message.level, str(message))
        if key in seen:
            continue
        seen.add(key)
        unique.append(message)

    return {"flash_messages": unique[:MAX_FLASH_MESSAGES]}