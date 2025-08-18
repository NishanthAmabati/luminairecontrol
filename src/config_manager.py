import yaml
import logging
import logging.handlers
import datetime
import os

def load_config(config_path="config.yaml"):
    """Load configuration from a YAML file."""
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        logging.debug(f"Configuration loaded from {config_path}")
        return config
    except Exception as e:
        logging.error(f"Failed to load config from {config_path}: {e}", exc_info=True)
        raise

def setup_logging(config):
    """Configure logging with rotation based on config settings."""
    log_dir = config["logging"]["log_dir"]
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    timestamp = datetime.datetime.now().strftime(config["logging"]["filename_template"])
    log_file_path = os.path.join(log_dir, timestamp)
    handler = logging.handlers.TimedRotatingFileHandler(
        log_file_path,
        when=config["logging"]["rotation_when"],
        interval=config["logging"]["rotation_interval"],
        backupCount=config["logging"]["rotation_backup_count"]
    )
    logging.basicConfig(
        level=getattr(logging, config["logging"]["level"]),
        format="%(asctime)s [%(levelname)s] - %(message)s",
        handlers=[handler]
    )    
    logging.debug("Logging configured with rotation in {log_dir}")