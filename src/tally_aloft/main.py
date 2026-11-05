from tally_aloft.bootstrap.app import create_app
from tally_aloft.bootstrap.observability import (
    API_SERVICE,
    configure_observability,
    instrument_app,
)
from tally_aloft.bootstrap.settings import load_settings

settings = load_settings()
configure_observability(settings, API_SERVICE)
app = instrument_app(create_app(settings))
