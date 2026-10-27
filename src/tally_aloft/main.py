from tally_aloft.bootstrap.app import create_app
from tally_aloft.bootstrap.settings import load_settings

app = create_app(load_settings())
