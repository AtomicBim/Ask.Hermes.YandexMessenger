"""Yandex Messenger system-prompt platform hint (``PlatformEntry.platform_hint``).

These bytes land in every cached system prompt: change them deliberately.
"""

PLATFORM_HINT = (
    "You are on Yandex Messenger (Яндекс Мессенджер). Messages render markdown-like "
    "markup: **bold**, __italic__ (double underscore; a single * is NOT italic here), "
    "~~strikethrough~~, ++underline++, `inline code`, ```code blocks```, [links](url). "
    "Headings (#) and tables are NOT supported: use bold labels and bullet lists. "
    "You can send images, voice, video and files natively by writing "
    "MEDIA:/absolute/path/to/file in your response (uploaded directly, no public URL needed). "
    "Interactive buttons (approvals, clarifying questions, pickers) are supported. "
    "Keep each message under 6000 characters; longer replies are split automatically."
)
