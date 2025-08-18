import logging
import logging.handlers
import datetime

def setup_logging(config):
    timestamp = datetime.datetime.now().strftime(config["logging"]["filename_template"])
    handler = logging.handlers.TimedRotatingFileHandler(
        timestamp,
        when=config["logging"]["rotation_when"],
        interval=config["logging"]["rotation_interval"],
        backupCount=config["logging"]["rotation_backup_count"]
    )
    logging.basicConfig(
        level=getattr(logging, config["logging"]["level"]),
        format="%(asctime)s [%(levelname)s] - %(message)s",
        handlers=[handler]
    )

def downsample_data(data, points_per_hour=6):
    logging.debug(f"Downsampling data: {len(data)} points to ~{points_per_hour * 24} points")
    downsampled = []
    step = max(1, 3600 // 10 // points_per_hour)
    for i in range(0, len(data), step):
        downsampled.append(data[i])
    logging.debug(f"Downsampled to {len(downsampled)} points")
    return downsampled