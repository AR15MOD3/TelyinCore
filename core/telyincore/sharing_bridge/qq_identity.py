# Exact identity helpers extracted from inspected production source; no plugin import.
def _qq_id(value: object) -> str | None:

    if isinstance(value, bool) or not isinstance(value, (int, str)):

        return None

    said = str(value)

    if (not said.isascii() or not said.isdecimal() or len(said) > 20

            or int(said) <= 0):

        return None

    return str(int(said))

def _message_id(value: object) -> str | None:

    if isinstance(value, bool) or not isinstance(value, (int, str)):

        return None

    said = str(value).strip()

    digits = said[1:] if said.startswith("-") else said

    return said if digits.isascii() and digits.isdecimal() and len(said) <= 20 else None
