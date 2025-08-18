# Luminaire Control System - Technical Documentation

## Overview
The Luminaire Control System is a Python-based application designed to manage and control luminaire devices over a TCP server and provide real-time status updates via WebSocket. It supports manual and automated (scene-based) control of luminaire color temperature (CCT) and intensity, with state persistence and logging for debugging and monitoring.

## System Architecture
The application is modular, split into five Python modules to enhance maintainability:

- **config_manager.py**: Handles loading configuration from `config.yaml` and setting up logging with rotation.
- **state_manager.py**: Manages the application's state, including initialization and persistence to `state.pkl`.
- **luminaire_operations.py**: Core logic for controlling luminaire devices, including device management, command building, and scene scheduling.
- **luminaire_server.py**: Manages TCP server for luminaire communication and WebSocket server for client updates.
- **main.py**: Entry point, orchestrates initialization, and starts servers.

### Key Components
- **State Management**: A global `state` dictionary stores system status (e.g., `auto_mode`, `current_scene`, `cw`, `ww`, `connected_devices`). Persisted to `state.pkl` for recovery after restarts.
- **Scene Management**: Scenes are defined in CSV files in the `scenes` directory, specifying time, CCT, and intensity. The system interpolates values for smooth transitions.
- **Device Communication**: Luminaires connect via TCP (default port 5000), receiving commands in the format `*<ip3><ip4><cw*10><ww*10>##` and sending ACK responses.
- **WebSocket Interface**: Clients connect via WebSocket (default port 8765) to receive real-time updates and send control commands (e.g., set mode, adjust light).

## Module Details

### config_manager.py
- **Purpose**: Loads configuration from `config.yaml` and configures logging.
- **Key Functions**:
  - `load_config(config_path)`: Loads and parses `config.yaml`.
  - `setup_logging(config)`: Sets up `TimedRotatingFileHandler` for log rotation.
- **Dependencies**: `yaml`, `logging`, `logging.handlers`, `datetime`, `os`.

### state_manager.py
- **Purpose**: Initializes and persists the application state.
- **Key Functions**:
  - `initialize_state(config)`: Creates the initial `state` dictionary with defaults.
  - `save_state(state)`: Saves critical state to `state.pkl`.
  - `load_state(state)`: Loads persisted state from `state.pkl` if available.
- **State Structure**:
  ```python
  state = {
      "auto_mode": bool,
      "available_scenes": list,
      "current_scene": str,
      "loaded_scene": str,
      "cw": float,
      "ww": float,
      "scheduler": dict,
      "connected_devices": dict,
      "basicLogs": list,
      "advancedLogs": list,
      "scene_data": dict,
      "current_cct": float,
      "current_intensity": float,
      "is_manual_override": bool,
      "cpu_percent": float,
      "mem_percent": float,
      "temperature": float,
      "activationTime": str,
      "isSystemOn": bool,
      "last_state": dict
  }
  ```
- **Dependencies**: `pickle`, `datetime`, `logging`, `os`.

### luminaire_operations.py
- **Purpose**: Manages luminaire devices and scene scheduling.
- **Key Class**: `LuminaireOperations`
  - **Attributes**:
    - `config`: Configuration dictionary.
    - `state`: Shared state dictionary.
    - `devices`: Dictionary of connected luminaires (`{ip: {writer, last_seen, cw, ww}}`).
    - Locks for thread safety (`_devices_lock`, `_state_lock`, `_send_lock`).
  - **Key Methods**:
    - `add(ip, writer)`: Adds a luminaire device.
    - `disconnect(ip)`: Disconnects a luminaire.
    - `send(ip, cw, ww)`: Sends CW/WW values to a device.
    - `sendAll(cw, ww)`: Sends CW/WW values to all devices.
    - `calculate_cw_ww_from_cct_intensity(cct, intensity)`: Converts CCT/intensity to CW/WW.
    - `run_smooth_scheduler(csv_path, scene_data)`: Executes a scene with interpolated transitions.
    - `get_system_stats()`: Retrieves CPU, memory, and temperature stats.
- **Dependencies**: `asyncio`, `threading`, `logging`, `time`, `re`, `csv`, `datetime`, `psutil`.

### luminaire_server.py
- **Purpose**: Runs TCP and WebSocket servers for device and client communication.
- **Key Class**: `LuminaireServer`
  - **Attributes**:
    - `config`, `luminaire_ops`, `state`, `scene_data`, `clients`.
    - `host`, `port`: TCP server settings.
    - `running`, `server`: Server status and instance.
  - **Key Methods**:
    - `start()`: Starts the TCP server.
    - `handle_client(reader, writer)`: Handles luminaire TCP connections.
    - `websocket_handler(websocket, path)`: Handles WebSocket client commands.
    - `broadcast_system_stats()`: Periodically broadcasts CPU/memory stats.
    - `broadcast_live_updates()`: Broadcasts live scene updates.
- **Dependencies**: `asyncio`, `websockets`, `json`, `logging`, `os`, `state_manager`.

### main.py
- **Purpose**: Application entry point, initializes components, and starts servers.
- **Key Functions**:
  - `downsample_data(data, points_per_hour)`: Downsamples scene data for WebSocket efficiency.
  - `load_scenes(luminaire_ops, config)`: Loads and interpolates scene CSV files.
  - `main()`: Async entry point, initializes state, loads scenes, and starts servers.
- **Dependencies**: `asyncio`, `resource`, `logging`, `os`, `csv`, `datetime`, `config_manager`, `state_manager`, `luminaire_operations`, `luminaire_server`.

## Setup Instructions
1. **Directory Structure**:
   ```
   backend/src/
   ├── __init__.py
   ├── config_manager.py
   ├── state_manager.py
   ├── luminaire_operations.py
   ├── luminaire_server.py
   ├── main.py
   ├── config.yaml
   ├── logs/
   └── scenes/
       └── test_scene.csv
   ```

2. **Install Dependencies**:
   ```bash
   pip3 install websockets psutil pyyaml
   ```

3. **Create `config.yaml`**:
   ```yaml
   logging:
     filename_template: "logs/luminaire_%Y%m%d.log"
     level: "DEBUG"
     rotation_when: "midnight"
     rotation_interval: 1
     rotation_backup_count: 7
   server:
     host: "0.0.0.0"
     port: 5000
     websocket_host: "0.0.0.0"
     websocket_port: 8765
   luminaire_operations:
     min_cct: 2700
     max_cct: 6500
     min_intensity: 0
     max_intensity: 1000
     inactivity_threshold: 30
     max_retries: 3
     cleanup_interval: 60
     scheduler_update_interval: 2.0
     scene_directory: "scenes"
     total_intervals: 8640
     log_basic_max_entries: 100
     log_advanced_max_entries: 200
   ```

4. **Create Scene Files**:
   - Place CSV files in `scenes/` with the format:
     ```csv
     Time,CCT,Intensity
     00:00,2700,500
     12:00,4000,750
     23:59,2700,500
     ```

5. **Run the Application**:
   ```bash
   cd backend
   python3 -m src.main
   ```

## Maintenance Guidelines
- **Logging**: Logs are written to `logs/luminaire_YYYYMMDD.log`. Check for errors if issues arise.
- **State Persistence**: Critical state is saved to `state.pkl`. Ensure write permissions.
- **Thread Safety**: Use `_devices_lock` and `_state_lock` for thread-safe operations on `devices` and `state`.
- **Error Handling**: Most methods log errors to `basicLogs` or `advancedLogs`. Monitor these via WebSocket.
- **Extending Functionality**:
  - Add new WebSocket commands in `luminaire_server.py:websocket_handler`.
  - Extend `luminaire_operations.py` for new device protocols.
  - Update `config.yaml` for new configuration options.
- **Testing**:
  - Simulate luminaire connections with a TCP client sending ACKs (e.g., `*001xxxxyyyACKcccddd#`).
  - Test WebSocket commands using a client like `wscat`:
    ```bash
    wscat -c ws://localhost:8765
    > {"type": "set_mode", "auto": true}
    ```

## Known Issues
- Ensure ports 5000 (TCP) and 8765 (WebSocket) are not blocked.
- Scene CSV files must have valid `HH:MM` time formats and numeric CCT/intensity values.
- Temperature monitoring may not work on all platforms (handled gracefully).

## Development Notes
- **Dependencies**: Python 3.8+, `websockets`, `psutil`, `pyyaml`.
- **Version Control**: Use Git for version control. Commit changes to individual modules.
- **Testing**: Write unit tests for `luminaire_operations.py` methods using `unittest` or `pytest`.
- **Performance**: Monitor `broadcast_system_stats` and `broadcast_live_updates` for WebSocket performance with many clients.