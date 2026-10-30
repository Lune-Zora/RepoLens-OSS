def read_flag(value):
    key = value.lower()
    if key not in {"true", "false"}:
        raise ValueError("unsupported flag")
    return key == "true"


def format_flag(value):
    return "true" if value else "false"
