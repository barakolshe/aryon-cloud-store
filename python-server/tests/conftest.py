"""Shared test setup.

Several suites re-import the app package to exercise import-time behaviour: the engine is
built when `app.database` is imported, and the settings when `app.config` is, so a test
that wants a different environment has to get a fresh import.

Clearing the leaf modules alone is not enough, and fails in a way that is easy to misread.
`from app.routes import health` in app/main.py reads the attribute the `app.routes`
package object still holds, so a surviving package hands back the *previous* submodule
while `import_module` builds a new one. The app then wires itself from one copy while a
test holds the other, which surfaces as a dependency override that silently does not
apply -- the endpoint reaches the real database instead of the stub. Dropping the whole
`app.*` tree keeps the two in step.

Modules already imported at collection time stay usable: a class removed from sys.modules
keeps working through the references its functions hold, so the bound names in a test
module still resolve to the objects that module imported.
"""
import sys

import pytest


def forget_app_modules():
    for name in [name for name in sys.modules if name == 'app' or name.startswith('app.')]:
        del sys.modules[name]


@pytest.fixture(autouse=True)
def isolate_app_imports():
    """Give every test a clean slate, and leave one behind for the next."""
    forget_app_modules()
    yield
    forget_app_modules()
