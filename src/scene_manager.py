import os
import csv
import logging
from src.utils import downsample_data

scene_data = {}

def load_scenes(ops, config):
    logging.debug("Loading scenes")
    scene_dir = config["luminaire_operations"]["scene_directory"]
    if not os.path.exists(scene_dir):
        os.makedirs(scene_dir)
    ops.state_manager.state["available_scenes"] = [f for f in os.listdir(scene_dir) if f.endswith('.csv')]
    for scene in ops.state_manager.state["available_scenes"]:
        try:
            with open(os.path.join(scene_dir, scene), newline='') as csvfile:
                reader = csv.reader(csvfile)
                next(reader)  # Skip header
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
                scene_data[scene] = {
                    "cct": downsampled_cct,
                    "intensity": downsampled_intensity
                }
            logging.info(f"Loaded scene {scene}.")
        except Exception as e:
            logging.error(f"Error loading scene {scene}: {e}", exc_info=True)