from tally_aloft.bootstrap.container import TASKS
from tally_aloft.bootstrap.settings import load_settings
from tally_aloft.bootstrap.worker import create_scheduler, create_worker

broker = create_worker(load_settings(), TASKS)
scheduler = create_scheduler(broker)
