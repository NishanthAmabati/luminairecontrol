import pickle
import datetime
import logging
import os

# Define path for state persistence
STATE_FILE = "state.pkl"

def initialize_state(config):
    """Initialize the global state dictionary."""
    state = {
        "auto_mode": False,
        "available_scenes": [],
        "current_scene": None,
        "loaded_scene": None,
        "cw": 50.0,
        "ww": 50.0,
        "scheduler": {
            "current_cct": 3500,
            "current_interval": 0,
            "total_intervals": config["luminaire_operations"]["total_intervals"],
            "status": "idle",
            "interval_progress": 0
        },
        "connected_devices": {},
        "basicLogs": [],
        "advancedLogs": [],
        "scene_data": {"cct": [], "intensity": []},
        "current_cct": 3500,
        "current_intensity": 250,
        "is_manual_override": False,
        "cpu_percent": 0.0,
        "mem_percent": 0.0,
        "temperature": None,
        "activationTime": None,
        "isSystemOn": True,
        "last_state": {
            "auto_mode": False,
            "current_scene": None,
            "cw": 50.0,
            "ww": 50.0,
            "current_intensity": 250
        }
    }
    logging.debug("State initialized")
    return state

def save_state(state):
    """Save critical state to a pickle file."""
    try:
        persistent_state = {
            "auto_mode": state["auto_mode"],
            "current_scene": state["current_scene"],
            "cw": state["cw"],
            "ww": state["ww"],
            "current_intensity": state["current_intensity"],
            "isSystemOn": state["isSystemOn"]
        }
        with open(STATE_FILE, "wb") as f:
            pickle.dump(persistent_state, f)
        logging.debug(f"State saved to {STATE_FILE}")
    except Exception as e:
        logging.error(f"Failed to save state to {STATE_FILE}: {e}", exc_info=True)

def load_state(state):
    """Load persisted state from a pickle file if available."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "rb") as f:
                persisted_state = pickle.load(f)
                # Restore critical state
                state["auto_mode"] = persisted_state.get("auto_mode", state["auto_mode"])
                state["current_scene"] = persisted_state.get("current_scene", state["current_scene"])
                state["cw"] = persisted_state.get("cw", state["cw"])
                state["ww"] = persisted_state.get("ww", state["ww"])
                state["current_intensity"] = persisted_state.get("current_intensity", state["current_intensity"])
                state["isSystemOn"] = persisted_state.get("isSystemOn", state["isSystemOn"])
                # Update last_state for consistency
                state["last_state"] = {
                    "auto_mode": state["auto_mode"],
                    "current_scene": state["current_scene"],
                    "cw": state["cw"],
                    "ww": state["ww"],
                    "current_intensity": state["current_intensity"]
                }
                logging.debug(f"State loaded from {STATE_FILE}")
    except Exception as e:
        logging.error(f"Failed to load state from {STATE_FILE}: {e}", exc_info=True)