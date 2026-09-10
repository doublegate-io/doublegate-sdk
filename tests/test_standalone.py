import importlib.util


def test_standalone_namespace_exists():
    assert importlib.util.find_spec('doublegate_sdk') is not None


def test_service_namespace_is_not_installed():
    assert importlib.util.find_spec('doublegate') is None
