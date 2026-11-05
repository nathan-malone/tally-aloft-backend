from tally_aloft.bootstrap.container import TASKS
from tally_aloft.bootstrap.observability import WORKER_SERVICE, configure_observability
from tally_aloft.bootstrap.settings import load_settings
from tally_aloft.bootstrap.worker import create_scheduler, create_worker

settings = load_settings()
configure_observability(settings, WORKER_SERVICE)
broker = create_worker(settings, TASKS)
scheduler = create_scheduler(broker)
