"""
The API app plus the test-only routes, configured from the environment.

The image tests run it with the image's default command, swapping only the app
path, so the server flags under test are the real ones.
"""

from tally_aloft.bootstrap.app import create_app
from tally_aloft.bootstrap.settings import load_settings
from tests.support import probe_routes

app = create_app(load_settings())
app.include_router(probe_routes.router)
