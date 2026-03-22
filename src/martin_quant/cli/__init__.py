"""CLI package exports without eagerly importing martin_quant.cli.main."""


def main(*args, **kwargs):
    from martin_quant.cli.main import main as _main

    return _main(*args, **kwargs)


__all__ = ["main"]
