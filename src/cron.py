import schedule
import time
import subprocess
from logger import logger

def job():
    logger.info("Starting scheduled job: updating RSS feeds.")
    subprocess.run(["sh", "start.sh"], check=False, capture_output=True, text=True)

schedule.every(24).hours.do(job)
logger.info('Scheduled job to run every 24 hours.')
# Run the job once at startup
job()

while True:
    schedule.run_pending()
    time.sleep(10)