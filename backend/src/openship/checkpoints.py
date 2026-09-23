from langgraph.checkpoint.sqlite import SqliteSaver


def get_checkpointer() -> SqliteSaver:
    return SqliteSaver("checkpoints.db")
