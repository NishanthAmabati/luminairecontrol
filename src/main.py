import asyncio
import signal
import logging
import os
import csv
from .config_manager import load_config, setup_logging
from .state_manager import initialize_state, load_state, save_state
from .luminaire_operations import LuminaireOperations
from .luminaire_server import LuminaireServer

def downsample_data(data, points_per_hour=6):
    """Downsample data to a specified number of points per hour."""
    logging.debug(f"Downsampling data: {len(data)} points to ~{points_per_hour * 24} points")
    downsampled = []
    step = max(1, 3600 // 10 // points_per_hour)
    for i in range(0, len(data), step):
        downsampled.append(data[i])
    logging.debug(f"Downsampled to {len(downsampled)} points")
    return downsampled

def load_scenes(luminaire_ops, config, state):
    """Load scenes from CSV files."""
    scene_data = {}
    logging.debug("Loading scenes")
    scene_dir = config["luminaire_operations"]["scene_directory"]
    if not os.path.exists(scene_dir):
        os.makedirs(scene_dir)
    state["available_scenes"] = [f for f in os.listdir(scene_dir) if f.endswith('.csv')]
    for scene in state["available_scenes"]:
        try:
            with open(os.path.join(scene_dir, scene), newline='') as csvfile:
                reader = csv.reader(csvfile)
                next(reader)
                scene_data_list = [(int(row[0].split(':')[0]) * 60 + int(row[0].split(':')[1]), float(row[1]), float(row[2])) for row in reader]
                full_cct_data = []
                full_intensity_data = []
                for i in range(len(scene_data_list)):
                    start_min, start_cct, start_intensity = scene_data_list[i]
                    end_min, end_cct, end_intensity = scene_data_list[(i + 1) % len(scene_data_list)]
                    time_diff = ((end_min - start_min + 1440) % 1440) * 60
                    cct_diff = end_cct - start_cct
                    intensity_diff = end_intensity - start_intensity
                    for j in range(1800):
                        t = j / 1799
                        interpolated_cct = start_cct + (cct_diff * t)
                        interpolated_intensity = start_intensity + (intensity_diff * t)
                        full_cct_data.append(interpolated_cct)
                        full_intensity_data.append(interpolated_intensity)
                downsampled_cct = downsample_data(full_cct_data, points_per_hour=6)
                downsampled_intensity = downsample_data(full_intensity_data, points_per_hour=6)
                logging.debug(f"Interpolated and downsampled {scene}: {len(downsampled_cct)} CCT points, {len(downsampled_intensity)} intensity points")
                scene_data[scene] = {
                    "cct": downsampled_cct,
                    "intensity": downsampled_intensity
                }
            logging.info(f"Loaded scene {scene}.")
            logging.debug(f"Scene data for {scene}: {len(scene_data[scene]['cct'])} CCT points, {len(scene_data[scene]['intensity'])} intensity points")
        except Exception as e:
            logging.error(f"Error loading scene {scene}: {e}", exc_info=True)
    return scene_data

async def main():
    """Main entry point for the luminaire control system."""
    config = load_config()
    setup_logging(config)
    state = initialize_state(config)
    load_state(state)
    luminaire_ops = LuminaireOperations(config, state)
    scene_data = load_scenes(luminaire_ops, config, state)
    clients = set()
    server = LuminaireServer(config, luminaire_ops, state, scene_data, clients)
    if state["isSystemOn"]:
        if state["auto_mode"] and state["current_scene"]:
            scene_path = os.path.join(config["luminaire_operations"]["scene_directory"], state["current_scene"])
            state["loaded_scene"] = state["current_scene"]
            if state["current_scene"] in scene_data:
                state["scene_data"] = scene_data[state["current_scene"]]
                logging.debug(f"Restored scene_data for {state['current_scene']}: {len(state['scene_data']['cct'])} CCT points, {len(state['scene_data']['intensity'])} intensity points")
            else:
                state["scene_data"] = {"cct": [], "intensity": []}
                logging.warning(f"Scene {state['current_scene']} not found in scene_data during restoration")
            state["activationTime"] = datetime.datetime.now().strftime("%H:%M:%S")
            state["scheduler"]["status"] = "running"
            asyncio.create_task(luminaire_ops.run_smooth_scheduler(scene_path, scene_data))
            luminaire_ops.log_basic(f"Restored and activated scene after power-on: {state['current_scene']}")
            logging.info(f"Restored and activated scene after power-on: {state['current_scene']}")
        else:
            #async with luminaire_ops._devices_lock:
            if not luminaire_ops.devices:
                logging.debug("No devices available, skipping restore sendAll")
            else:
                success, failed_ips = await luminaire_ops.sendAll(state["cw"], state["ww"])
                luminaire_ops.log_basic(f"Restored manual settings after power-on: CW={state['cw']}, WW={state['ww']}, Intensity={state['current_intensity']}")
                logging.info(f"Restored manual settings after power-on: CW={state['cw']}, WW={state['ww']}, Intensity={state['current_intensity']}")
    try:
        await server.start()
    except KeyboardInterrupt:
        logging.info("Received shutdown signal")
        await server.shutdown()
    except Exception as e:
        logging.error(f"Main loop error: {e}", exc_info=True)
        await server.shutdown()

if __name__ == "__main__":
    asyncio.run(main())