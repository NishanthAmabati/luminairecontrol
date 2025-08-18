import asyncio
import json
import logging
import datetime
import websockets
from src.scene_manager import scene_data

async def websocket_handler(websocket, path, ops, server, state_manager, config):
    logging.debug(f"New WebSocket connection from {websocket.remote_address}")
    state_manager.clients.add(websocket)
    logging.info(f"New WebSocket client connected from {websocket.remote_address}")
    ops.log_advanced("WebSocket connected")
    try:
        await server.emit_status_update(websocket)
        async for message in websocket:
            data = json.loads(message)
            logging.debug(f"Received message: {data}")
            action = data.get("type")
            now = datetime.datetime.now().strftime("%H:%M:%S")
            include_scene_data = False
            # Include all WebSocket action handlers (set_mode, load_scene, etc.)
            # Use state_manager.state, ops, server, and scene_data as needed
    except Exception as e:
        ops.log_advanced(f"WebSocket handler error: {e}")
        logging.error(f"WebSocket handler error: {e}", exc_info=True)
    finally:
        state_manager.clients.remove(websocket)
        ops.log_advanced("WebSocket disconnected")
        logging.info(f"WebSocket client disconnected from {websocket.remote_address}")

async def emit_status_update(websocket, state_manager):
    state_update = {key: state_manager.state[key] for key in state_manager.state}
    if state_manager.state["loaded_scene"] or state_manager.state["current_scene"]:
        state_update["scene_data"] = state_manager.state["scene_data"]
    await websocket.send(json.dumps(state_update))
    logging.debug("Status update emitted")