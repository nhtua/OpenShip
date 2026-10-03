# Import all models so Alembic's autogenerate can discover them.
from ..auth.models import Base, User  # noqa: F401
from ..chat.models import Conversation, Message  # noqa: F401

__all__ = ["Base", "User", "Conversation", "Message"]
