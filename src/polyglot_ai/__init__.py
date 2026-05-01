try:
    from importlib.metadata import version, PackageNotFoundError

    __version__ = version("polyglot-ai")
except PackageNotFoundError:
    __version__ = "0.12.0"
