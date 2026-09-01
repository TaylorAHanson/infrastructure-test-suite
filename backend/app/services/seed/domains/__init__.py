"""Domain modules: each contributes its own slice of the seed catalog.

Nothing here imports the individual modules — :mod:`app.services.seed.registry`
discovers them at runtime by walking this package's ``__path__``. Dropping a
new ``*.py`` module in here with a module-level ``CONTRIBUTION`` is enough to
register it; this file never needs to change.
"""
