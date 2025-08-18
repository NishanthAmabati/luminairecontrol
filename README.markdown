# Luminaire Control System

A Python-based system for controlling luminaire devices via TCP and providing real-time updates to clients via WebSocket. Supports manual control and automated scene scheduling with state persistence and logging.

## Features
- **Luminaire Control**: Adjust color temperature (CCT) and intensity of connected luminaires.
- **Scene Scheduling**: Run predefined scenes from CSV files with smooth transitions.
- **WebSocket Interface**: Real-time status updates and control for clients.
- **State Persistence**: Saves system state to `state.pkl` for recovery.
- **Logging**: Rotated logs for debugging and monitoring.

## Prerequisites
- Python 3.8+
- Dependencies: `websockets`, `psutil`, `pyyaml`
- TCP port 5000 and WebSocket port 8765 open

## Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/your-repo/luminaire-control-system.git
   cd luminaire-control-system/backend
   ```

2. Install dependencies:
   ```bash
   pip install websockets psutil pyyaml
   ```

3. Create `config.yaml` in `backend/src/`:
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

4. Create `scenes` directory with sample CSV files:
   ```bash
   mkdir -p backend/src/scenes
   ```
   Example `scenes/test_scene.csv`:
   ```csv
   Time,CCT,Intensity
   00:00,2700,500
   12:00,4000,750
   23:59,2700,500
   ```

## Usage
1. Navigate to the `backend` directory:
   ```bash
   cd backend
   ```

2. Run the application:
   ```bash
   python3 -m src.main
   ```

3. Connect clients:
   - **Luminaires**: Connect via TCP to `localhost:5000`, sending ACKs like `*001xxxxyyyACKcccddd#`.
   - **WebSocket Clients**: Connect to `ws://localhost:8765`. Example commands:
     ```json
     {"type": "set_mode", "auto": true}
     {"type": "activate_scene", "scene": "test_scene.csv"}
     {"type": "adjust_light", "light_type": "cw", "delta": 10}
     ```

## Project Structure
```
backend/src/
├── __init__.py
├── config_manager.py    # Configuration and logging setup
├── state_manager.py     # State initialization and persistence
├── luminaire_operations.py # Luminaire control and scheduling
├── luminaire_server.py  # TCP and WebSocket servers
├── main.py              # Application entry point
├── config.yaml          # Configuration file
├── logs/                # Log files
└── scenes/              # Scene CSV files
```

## Contributing
- Fork the repository and create a feature branch.
- Submit pull requests with clear descriptions and tests.
- Follow PEP 8 for code style.

## License
MIT License