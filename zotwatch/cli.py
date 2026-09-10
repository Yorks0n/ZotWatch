"""Public console entry point; the legacy entry uses the same pipeline."""


def main(argv=None):
    from src.cli import main as legacy_main

    return legacy_main(argv)
