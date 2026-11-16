class PermanentError(Exception):
    """Retrying won't help (bad payload, rejected recipient): dead-letter immediately."""
