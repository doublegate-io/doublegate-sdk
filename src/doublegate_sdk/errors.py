"""Common base for SDK operational failures; no service or transport imports."""


class DoublegateError(Exception):
    """Catch SDK failures without swallowing unrelated application exceptions.

    Concrete exceptions retain their own safe diagnostic, protocol code and
    established ValueError/RuntimeError compatibility. ``kind`` names the category;
    specialized fields provide details without parsing human-readable text.
    """

    kind = 'sdk_error'
