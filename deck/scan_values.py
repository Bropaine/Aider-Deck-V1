"""Read configured credential values locally for the Git disclosure check."""

from . import envfile


def workspace_values(root):
    """Use Deck's normal dotenv quoting, expansion and credential name matching."""
    environment = envfile.environment(root)
    values = set()
    for name, value in environment.values.items():
        if value and envfile.is_sensitive(name):
            values.add(value)
            if value.lower().startswith("bearer ") and value[7:].strip():
                values.add(value[7:].strip())
    return frozenset(values)
