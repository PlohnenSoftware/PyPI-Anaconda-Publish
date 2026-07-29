from fixture_pkg import __version__, greet


def test_greet():
    assert greet("world") == "hello world"


def test_dependencies_are_importable():
    import packaging  # noqa: F401
    import yaml  # noqa: F401


def test_version():
    assert __version__ == "0.1.0"
