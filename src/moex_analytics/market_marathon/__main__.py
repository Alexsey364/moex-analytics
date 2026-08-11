import json

from moex_analytics.database import connection

from .core import run_full_marathon

with connection() as database:
    print(json.dumps(run_full_marathon(database), ensure_ascii=False, indent=2, default=str))
