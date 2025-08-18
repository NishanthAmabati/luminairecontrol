import asyncio
import websockets
import json
import logging
import os
import datetime
import uvicorn
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from .state_manager import save_state

# Pydantic models
class LightControl(BaseModel):
    cct: float
    intensity: float

class CctControl(BaseModel):
    cct: float

class IntensityControl(BaseModel):
    intensity: float

class SceneRequest(BaseModel):
    scene: str

class ModeRequest(BaseModel):
    auto: bool

class ToggleSystemRequest(BaseModel):
    isSystemOn: bool

class LuminaireServer:
    def __init__(self, config, luminaire_ops, state, scene_data, clients):
        self.config = config
        self.luminaire_ops = luminaire_ops
        self.state = state
        self.scene_data = scene_data
        self.clients = clients
        self.host = config["server"]["host"]
        self.port = config["server"]["port"]
        self.running = False
        self.server = None
        self.app = FastAPI()
        self.app.state.luminaire_server = self  # Store the server instance in app.state
        logging.debug("LuminaireServer initialized")
        
        # Register FastAPI endpoints with API key dependency
        self.app.get("/status", dependencies=[Depends(self.verify_api_key)])(self.get_status)
        self.app.get("/status/essentials", dependencies=[Depends(self.verify_api_key)])(self.get_essentials)
        self.app.get("/status/luminaires", dependencies=[Depends(self.verify_api_key)])(self.get_luminaires)
        self.app.get("/status/cct", dependencies=[Depends(self.verify_api_key)])(self.get_cct)
        self.app.get("/status/intensity", dependencies=[Depends(self.verify_api_key)])(self.get_intenstiy)
        self.app.post("/set_cct", dependencies=[Depends(self.verify_api_key)])(self.set_cct)
        self.app.post("/set_intensity", dependencies=[Depends(self.verify_api_key)])(self.set_intensity)
        self.app.post("/set_mode", dependencies=[Depends(self.verify_api_key)])(self.set_mode)
        self.app.post("/toggle_system", dependencies=[Depends(self.verify_api_key)])(self.toggle_system)
        self.app.post("/load_scene", dependencies=[Depends(self.verify_api_key)])(self.load_scene)
        self.app.post("/activate_scene", dependencies=[Depends(self.verify_api_key)])(self.activate_scene)
        self.app.post("/stop_scheduler", dependencies=[Depends(self.verify_api_key)])(self.stop_scheduler)

    async def verify_api_key(self, api_key: str = Depends(APIKeyHeader(name="Authorization"))):
        try:
            expected_key = self.config["server"]["api_key"]
        except KeyError:
            raise HTTPException(status_code=500, detail="API key not configured in config.yaml")
        if not expected_key:
            raise HTTPException(status_code=500, detail="API key is empty in config.yaml")
        if api_key != f"Bearer {expected_key}":
            raise HTTPException(status_code=401, detail="Invalid API key")
        return api_key

    async def emit_status_update(self, websocket):
        """Emit a status update to a WebSocket client."""
        logging.debug("Emitting status update")
        state_update = {
            "auto_mode": self.state["auto_mode"],
            "available_scenes": self.state["available_scenes"],
            "current_scene": self.state["current_scene"],
            "loaded_scene": self.state["loaded_scene"],
            "cw": self.state["cw"],
            "ww": self.state["ww"],
            "scheduler": self.state["scheduler"],
            "connected_devices": self.state["connected_devices"],
            "basicLogs": self.state["basicLogs"],
            "advancedLogs": self.state["advancedLogs"],
            "current_cct": self.state["current_cct"],
            "current_intensity": self.state["current_intensity"],
            "is_manual_override": self.state["is_manual_override"],
            "cpu_percent": self.state["cpu_percent"],
            "mem_percent": self.state["mem_percent"],
            "temperature": self.state["temperature"],
            "activationTime": self.state["activationTime"],
            "isSystemOn": self.state["isSystemOn"],
        }
        if self.state["loaded_scene"] is not None or self.state["current_scene"] is not None:
            state_update["scene_data"] = self.state["scene_data"]
        await websocket.send(json.dumps(state_update))

    async def stream_logs(self, websocket):
        """Stream logs to a WebSocket client."""
        logging.debug("Starting log streaming")
        while True:
            if not websocket.close and (self.state["basicLogs"] or self.state["advancedLogs"]):
                await websocket.send(json.dumps({
                    "type": "log_update",
                    "basicLogs": self.state["basicLogs"],
                    "advancedLogs": self.state["advancedLogs"]
                }))
                logging.debug("Logs streamed to client")
            await asyncio.sleep(1.0)

    async def broadcast_system_stats(self):
        """Broadcast system stats to all WebSocket clients."""
        logging.debug("Starting system stats broadcast")
        while True:
            cpu_percent, mem_percent, temperature = self.luminaire_ops.get_system_stats()
            with self.luminaire_ops._state_lock:
                self.state["cpu_percent"] = cpu_percent
                self.state["mem_percent"] = mem_percent
                self.state["temperature"] = temperature
            update = {
                "type": "system_stats",
                "cpu_percent": cpu_percent,
                "mem_percent": mem_percent,
                "temperature": temperature,
            }
            for client in list(self.clients):
                if not client.close:
                    await client.send(json.dumps(update))
                    logging.debug(f"Broadcasted stats to {client.remote_address}")
            await asyncio.sleep(1)

    async def broadcast_live_updates(self):
        """Broadcast live updates to all WebSocket clients."""
        logging.debug("Starting live updates broadcast")
        while True:
            if self.state["scheduler"]["status"] == "running":
                with self.luminaire_ops._state_lock:
                    live_update = {
                        "type": "live_update",
                        "current_cct": self.state["current_cct"],
                        "current_intensity": self.state["current_intensity"],
                        "cw": self.state["cw"],
                        "ww": self.state["ww"],
                        "interval_progress": self.state["scheduler"]["interval_progress"]
                    }
                if self.clients:
                    for client in list(self.clients):
                        if not client.close:
                            await client.send(json.dumps(live_update))
                            logging.debug(f"Sent live update to {client.remote_address}")
            await asyncio.sleep(1)

    async def send_manual_updates(self):
        """Send manual updates to luminaires based on system state."""
        logging.debug("Starting manual updates broadcast")
        while True:
            if not self.state["isSystemOn"]:
                #async with self.luminaire_ops._devices_lock:
                if not self.luminaire_ops.devices:
                    logging.debug("No devices available, skipping sendAll")
                    await asyncio.sleep(1.0)
                    continue
                success, failed_ips = await self.luminaire_ops.sendAll(0, 0)
                if not success:
                    self.luminaire_ops.log_advanced(f"Failed to send zero values to IPs: {', '.join(failed_ips)}")
                    logging.warning(f"Failed to send zero values to IPs: {failed_ips}")
                else:
                    logging.debug("Sent CW=0, WW=0 to all luminaires (system off)")
            elif not self.state["auto_mode"]:
                #async with self.luminaire_ops._devices_lock:
                if not self.luminaire_ops.devices:
                    logging.debug("No devices available, skipping manual update")
                    await asyncio.sleep(1.0)
                    continue
                cw, ww, intensity = self.state["cw"], self.state["ww"], self.state["current_intensity"]
                success, failed_ips = await self.luminaire_ops.sendAll(cw, ww)
                if not success:
                    self.luminaire_ops.log_advanced(f"Manual update failed for IPs: {', '.join(failed_ips)}")
                    logging.warning(f"Manual update failed for IPs: {failed_ips}")
                else:
                    logging.debug(f"Manual update sent - CW: {cw}, WW: {ww}")
            await asyncio.sleep(1.0)

    async def start(self):
        """Start the TCP server for luminaire communication."""
        logging.debug(f"Starting server on {self.host}:{self.port}")
        try:
            self.server = await asyncio.start_server(self.handle_client, self.host, self.port)
            self.running = True
            logging.info(f"Luminaire Server started on {self.host}:{self.port}")
            async with self.server:
                await asyncio.gather(
                    self.server.serve_forever(),
                    self.broadcast_system_stats(),
                    self.broadcast_live_updates(),
                    self.send_manual_updates(),
                    self.status_loop(),
                    websockets.serve(self.websocket_handler, self.config["server"]["websocket_host"], self.config["server"]["websocket_port"]),
                    self.start_api_server()
                )
        except Exception as e:
            logging.error(f"Failed to start server: {e}", exc_info=True)
            await self._cleanup_server()
            raise

    async def handle_client(self, reader, writer):
        """Handle a luminaire client connection."""
        addr = writer.get_extra_info('peername')
        client_ip = addr[0] if addr else "unknown"
        logging.info(f"New luminaire connected: {client_ip}")
  #      with self.luminaire_ops._devices_lock:
        self.luminaire_ops.add(client_ip, writer)
        try:
            while self.running:
                data = await reader.read(1024)
                if not data:
                    break
                logging.info(f"Received from {client_ip}: {data.decode()}")
          #      with self.luminaire_ops._devices_lock:
                self.luminaire_ops.processACK(client_ip, data)
        except (ConnectionError, OSError) as e:
            logging.warning(f"Luminaire {client_ip} disconnected unexpectedly: {e}")
        except Exception as e:
            logging.error(f"Unexpected error handling {client_ip}: {e}", exc_info=True)
        finally:
      #      with self.luminaire_ops._devices_lock:
            self.luminaire_ops.disconnect(client_ip)
            logging.debug(f"Client {client_ip} handling terminated")

    async def shutdown(self):
        """Shut down the server."""
        logging.debug("Shutting down server")
        self.running = False
  #      with self.luminaire_ops._devices_lock:
        self.luminaire_ops.clearALL()
        await self._cleanup_server()
        logging.info("Luminaire Server shut down.")

    async def _cleanup_server(self):
        """Clean up server resources."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        logging.debug("Server cleaned up")

    async def status_loop(self):
        """Periodically update connected devices list."""
        logging.debug("Starting status loop")
        while True:
            with self.luminaire_ops._state_lock:
                self.state["connected_devices"] = self.luminaire_ops.list()
            logging.debug(f"Updated connected devices: {list(self.state['connected_devices'].keys())}")
            await asyncio.sleep(10.0)

    async def websocket_handler(self, websocket, path=None):
        """Handle WebSocket client connections."""
        logging.debug(f"New WebSocket connection from {websocket.remote_address}")
        self.clients.add(websocket)
        logging.info(f"New WebSocket client connected from {websocket.remote_address}")
        self.luminaire_ops.log_advanced("WebSocket connected")
        try:
            await self.emit_status_update(websocket)
            async for message in websocket:
                data = json.loads(message)
                logging.debug(f"Received message: {data}")
                action = data.get("type")
                now = datetime.datetime.now().strftime("%H:%M:%S")
                include_scene_data = False
                if action == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
                elif action == "set_mode":
                    logging.debug("Processing set_mode action: auto=%s", data["auto"])
                    state_update = {}
                    previous_scene = self.state["current_scene"]
                    self.state["auto_mode"] = data["auto"]
                    self.luminaire_ops.stop_event.set() if not data["auto"] else self.luminaire_ops.stop_event.clear()
                    self.luminaire_ops.log_basic(f"Switched to {'Auto' if data['auto'] else 'Manual'} mode")
                    if not data["auto"]:
                        self.state["scene_data"] = {"cct": [], "intensity": []}
                        self.state["loaded_scene"] = None
                        self.state["scheduler"]["status"] = "idle"
                        state_update = {
                            "auto_mode": self.state["auto_mode"],
                            "scene_data": self.state["scene_data"],
                            "current_scene": self.state["current_scene"],
                            "loaded_scene": self.state["loaded_scene"],
                            "scheduler": self.state["scheduler"],
                        }
                    elif data["auto"] and previous_scene:
                        if previous_scene in self.scene_data:
                            self.state["current_scene"] = previous_scene
                            self.state["scene_data"] = self.scene_data[previous_scene]
                            self.state["activationTime"] = datetime.datetime.now().strftime("%H:%M:%S")
                            self.state["loaded_scene"] = previous_scene
                            self.state["scheduler"]["status"] = "running"
                            state_update = {
                                "auto_mode": self.state["auto_mode"],
                                "scene_data": self.state["scene_data"],
                                "current_scene": self.state["current_scene"],
                                "loaded_scene": self.state["loaded_scene"],
                                "scheduler": self.state["scheduler"],
                                "activationTime": self.state["activationTime"],
                            }
                            logging.debug("Prepared state_update with scene_data: cct_length=%s, intensity_length=%s",
                                        len(self.state["scene_data"]["cct"]), len(self.state["scene_data"]["intensity"]))
                        else:
                            logging.warning("Previous scene %s not found in scene_data", previous_scene)
                            self.luminaire_ops.log_basic(f"Failed to reactivate scene {previous_scene}: not found")
                            self.state["current_scene"] = None
                            self.state["scene_data"] = {"cct": [], "intensity": []}
                            state_update = {
                                "auto_mode": self.state["auto_mode"],
                                "scene_data": self.state["scene_data"],
                                "current_scene": self.state["current_scene"],
                                "loaded_scene": self.state["loaded_scene"],
                                "scheduler": self.state["scheduler"],
                            }
                    else:
                        state_update = {"auto_mode": self.state["auto_mode"]}
                    if data["auto"] and previous_scene and previous_scene in self.scene_data:
                        scene_path = os.path.join(self.config["luminaire_operations"]["scene_directory"], previous_scene)
                        logging.debug("Reactivating scene: %s at path %s", previous_scene, scene_path)
                        try:
                            asyncio.create_task(self.luminaire_ops.run_smooth_scheduler(scene_path, self.scene_data))
                            self.luminaire_ops.log_basic(f"Reactivated scene: {previous_scene}")
                        except Exception as e:
                            logging.error("Failed to reactivate scene %s: %s", previous_scene, e, exc_info=True)
                            self.luminaire_ops.log_basic(f"Failed to reactivate scene {previous_scene}: {str(e)}")
                            self.state["scheduler"]["status"] = "idle"
                            self.state["current_scene"] = None
                            state_update = {
                                "auto_mode": self.state["auto_mode"],
                                "scene_data": {"cct": [], "intensity": []},
                                "current_scene": None,
                                "loaded_scene": None,
                                "scheduler": self.state["scheduler"],
                            }
                    if state_update:
                        logging.debug("Sending state_update: %s", state_update)
                        await websocket.send(json.dumps({"state_update": state_update}))
                    save_state(self.state)
                    logging.debug("Completed set_mode action")
                elif action == "load_scene":
                    self.state["loaded_scene"] = data["scene"]
                    if data["scene"] in self.scene_data:
                        self.state["scene_data"] = self.scene_data[data["scene"]]
                        include_scene_data = True
                    self.luminaire_ops.log_basic(f"Loaded scene: {data['scene']}")
                    save_state(self.state)
                    logging.debug(f"Loaded scene data: cct_length=%s, intensity_length=%s",
                                len(self.state["scene_data"]["cct"]), len(self.state["scene_data"]["intensity"]))
                elif action == "activate_scene":
                    if self.state["scheduler"]["status"] == "running" and self.state["current_scene"]:
                        self.luminaire_ops.stop_scheduler()
                        await asyncio.sleep(0.1)
                        logging.debug(f"Stopped running scene: {self.state['current_scene']}")
                    self.state["current_scene"] = data["scene"]
                    self.state["loaded_scene"] = data["scene"]
                    if self.state["auto_mode"] and data["scene"] in self.scene_data:
                        self.state["scene_data"] = self.scene_data[data["scene"]]
                        self.state["activationTime"] = now
                        self.state["scheduler"]["status"] = "running"
                        include_scene_data = True
                        asyncio.create_task(self.luminaire_ops.run_smooth_scheduler(
                            os.path.join(self.config["luminaire_operations"]["scene_directory"], data["scene"]), self.scene_data))
                    self.luminaire_ops.log_basic(f"Activated scene: {data['scene']}")
                    save_state(self.state)
                    logging.debug(f"Activated scene: {data['scene']}")
                elif action == "stop_scheduler":
                    self.luminaire_ops.stop_scheduler()
                    save_state(self.state)
                    logging.debug("Scheduler stopped")
                elif action == "manual_override":
                    self.state["is_manual_override"] = data.get("override", False)
                    if not self.state["is_manual_override"] and self.state["auto_mode"] and self.state["current_scene"]:
                        self.luminaire_ops.start_time = None
                        asyncio.create_task(self.luminaire_ops.run_smooth_scheduler(
                            os.path.join(self.config["luminaire_operations"]["scene_directory"], self.state["current_scene"]), self.scene_data))
                    self.luminaire_ops.log_basic(f"Manual override {'enabled' if data.get('override', False) else 'disabled'}")
                    logging.debug(f"Manual override set to {self.state['is_manual_override']}")
                elif action == "pause_resume":
                    if data.get("pause", False):
                        self.luminaire_ops.pause_scheduler()
                    else:
                        self.luminaire_ops.resume_scheduler()
                    logging.debug(f"Scheduler {'paused' if data.get('pause', False) else 'resumed'}")
                elif action == "adjust_light":
                    light_type, delta = data["light_type"], data["delta"]
                    if light_type == "cw":
                        self.luminaire_ops.adjust_cw(delta * 1.0)
                        self.state["cw"] = min(100, max(0, (self.state["cw"] or 50) + delta))
                        self.state["ww"] = 100 - self.state["cw"]
                    elif light_type == "ww":
                        self.luminaire_ops.adjust_cw(-delta * 1.0)
                        self.state["ww"] = min(100, max(0, (self.state["ww"] or 50) + delta))
                        self.state["cw"] = 100 - self.state["ww"]
                    self.state["current_cct"] = self.luminaire_ops.calculate_cct_from_cw_ww(self.state["cw"], self.state["ww"])
                    self.luminaire_ops.log_basic(f"Adjusted {light_type.upper()} by {delta}%")
                    save_state(self.state)
                    logging.debug(f"Adjusted {light_type} by {delta}%, New CW: {self.state['cw']}, WW: {self.state['ww']}")
                elif action == "sendAll":
                    cw, ww, intensity = data["cw"], data["ww"], data["intensity"]
                    self.state["cw"], self.state["ww"], self.state["current_intensity"] = cw, ww, intensity
                    self.state["current_cct"] = self.luminaire_ops.calculate_cct_from_cw_ww(cw, ww)
                    success, failed_ips = await self.luminaire_ops.sendAll(cw, ww)
                    save_state(self.state)
                    logging.debug(f"SendAll - Success: {success}, Failed IPs: {failed_ips}")
                elif action == "set_cct":
                    self.state["current_cct"] = data["cct"]
                    cw, ww = self.luminaire_ops.calculate_cw_ww_from_cct_intensity(self.state["current_cct"], self.state["current_intensity"])
                    self.state["cw"], self.state["ww"] = cw, ww
                    success, failed_ips = await self.luminaire_ops.sendAll(cw, ww)
                    self.luminaire_ops.log_basic(f"Set CCT to {data['cct']}K")
                    save_state(self.state)
                    logging.debug(f"Set CCT to {data['cct']}K, CW: {cw}, WW: {ww}")
                elif action == "toggle_system":
                    self.state["isSystemOn"] = data["isSystemOn"]
                    if not data["isSystemOn"]:
                        self.state["last_state"] = {
                            "auto_mode": self.state["auto_mode"],
                            "current_scene": self.state["current_scene"],
                            "cw": self.state["cw"],
                            "ww": self.state["ww"],
                            "current_intensity": self.state["current_intensity"]
                        }
                        if self.state["auto_mode"]:
                            self.luminaire_ops.stop_event.set()
                            self.state["scheduler"]["status"] = "stopped"
                            self.state["current_scene"] = None
                            self.state["loaded_scene"] = None
                            self.state["scene_data"] = {"cct": [], "intensity": []}
                            self.luminaire_ops.log_basic("Scheduler stopped due to system off")
                        success, failed_ips = await self.luminaire_ops.sendAll(0, 0)
                        self.luminaire_ops.log_basic("System turned OFF")
                    else:
                        if self.state["last_state"]["auto_mode"] and self.state["last_state"]["current_scene"]:
                            self.state["auto_mode"] = True
                            self.state["current_scene"] = self.state["last_state"]["current_scene"]
                            self.state["loaded_scene"] = self.state["last_state"]["current_scene"]
                            self.state["scene_data"] = self.scene_data[self.state["current_scene"]]
                            self.state["activationTime"] = datetime.datetime.now().strftime("%H:%M:%S")
                            asyncio.create_task(self.luminaire_ops.run_smooth_scheduler(
                                os.path.join(self.config["luminaire_operations"]["scene_directory"], self.state["current_scene"]), self.scene_data))
                            self.luminaire_ops.log_basic(f"Restored and activated scene: {self.state['current_scene']}")
                        else:
                            self.state["cw"] = self.state["last_state"]["cw"]
                            self.state["ww"] = self.state["last_state"]["ww"]
                            self.state["current_intensity"] = self.state["last_state"]["current_intensity"]
                            self.state["current_cct"] = self.luminaire_ops.calculate_cct_from_cw_ww(self.state["cw"], self.state["ww"])
                            success, failed_ips = await self.luminaire_ops.sendAll(self.state["cw"], self.state["ww"])
                        self.luminaire_ops.log_basic("System turned ON")
                    save_state(self.state)
                    logging.debug(f"System toggled to {'ON' if data['isSystemOn'] else 'OFF'}")
                state_update = {
                    "auto_mode": self.state["auto_mode"],
                    "available_scenes": self.state["available_scenes"],
                    "current_scene": self.state["current_scene"],
                    "loaded_scene": self.state["loaded_scene"],
                    "cw": self.state["cw"],
                    "ww": self.state["ww"],
                    "scheduler": self.state["scheduler"],
                    "connected_devices": self.state["connected_devices"],
                    "basicLogs": self.state["basicLogs"],
                    "advancedLogs": self.state["advancedLogs"],
                    "current_cct": self.state["current_cct"],
                    "current_intensity": self.state["current_intensity"],
                    "is_manual_override": self.state["is_manual_override"],
                    "cpu_percent": self.state["cpu_percent"],
                    "mem_percent": self.state["mem_percent"],
                    "temperature": self.state["temperature"],
                    "activationTime": self.state["activationTime"],
                    "isSystemOn": self.state["isSystemOn"],
                }
                if include_scene_data:
                    state_update["scene_data"] = self.state["scene_data"]
                    logging.debug("Sending state_update with scene_data: cct_length=%s, intensity_length=%s",
                                len(state_update["scene_data"]["cct"]), len(state_update["scene_data"]["intensity"]))
                for client in self.clients:
                    await client.send(json.dumps(state_update))
                logging.debug("State updated and broadcasted to clients")
            asyncio.create_task(self.stream_logs(websocket))
        except Exception as e:
            self.luminaire_ops.log_advanced(f"WebSocket handler error: {e}")
            logging.error(f"WebSocket handler error: {e}", exc_info=True)
        finally:
            self.clients.remove(websocket)
            self.luminaire_ops.log_advanced("WebSocket disconnected")
            logging.info(f"WebSocket client disconnected from {websocket.remote_address}")
            logging.debug("Client removed from clients set")

    async def start_api_server(self):
        config = uvicorn.Config(self.app, host=self.config["server"]["host"], port=self.config["server"].get("api_port", 8000))
        server = uvicorn.Server(config)
        await server.serve()

    # FastAPI endpoints
    async def get_status(self):
        return self.state

    async def get_essentials(self):
        mode = "auto" if self.state["auto_mode"] == True else "manual"
        if mode == "manual":
            return {
            "status": "success", 
            "issystemon": self.state["isSystemOn"], 
            "mode": {mode}, 
            "cpu": self.state["cpu_percent"], 
            "memory": self.state["mem_percent"], 
            "temperature": self.state["temperature"], 
            "cct": self.state["current_cct"], 
            "intensity": self.state["current_intensity"],
            "luminaires": self.state["connected_devices"]
        }
        else:
            return {
            "status": "success", 
            "issystemon": self.state["isSystemOn"], 
            "mode": {mode},
            "current scene": self.state["current_scene"], 
            "cpu": self.state["cpu_percent"], 
            "memory": self.state["mem_percent"], 
            "temperature": self.state["temperature"], 
            "cct": self.state["current_cct"], 
            "intensity": self.state["current_intensity"],
            "luminaires": self.state["connected_devices"]
        }

    async def get_luminaires(self):
        connected = self.state["connected_devices"]
        return {"status": "success", "total connected": len(connected), "luminaires": self.state["connected_devices"]}

    async def get_cct(self):
        return {"status": "success", "current cct": self.state["current_cct"]}

    async def get_intenstiy(self):
        return {"status": "success", "current cct": self.state["current_intensity"]}

    async def set_cct(self, control: CctControl):
    #async def set_cct(self, control: CctControl):
        #async with self.luminaire_ops._state_lock:
            #async with self.luminaire_ops._devices_lock:
        if not self.luminaire_ops.devices:
            logging.warning("No devices available to set CCT")
            self.state["current_cct"] = control.cct
            self.state["cw"] = 0
            self.state["ww"] = 0
            save_state(self.state)
            return {"status": "success", "cct": control.cct, "warning": "No devices connected"}
        cw, ww = self.luminaire_ops.calculate_cw_ww_from_cct_intensity(control.cct, self.state["current_intensity"])
        success, failed_ips = await self.luminaire_ops.sendAll(cw, ww)
        if not success:
            raise HTTPException(status_code=500, detail=f"Failed to send to: {failed_ips}")
        self.state["current_cct"] = control.cct
        self.state["cw"] = cw
        self.state["ww"] = ww
        save_state(self.state)
        logging.info(f"Set CCT to {control.cct}K, CW: {cw}, WW: {ww}")
        return {"status": "success", "cct": control.cct}

    async def set_intensity(self, control: IntensityControl):
    #async def set_intensity(self, control: IntensityControl):
        #async with self.luminaire_ops._state_lock:
        #    async with self.luminaire_ops._devices_lock:
        if not self.luminaire_ops.devices:
            logging.warning("No devices available to set intensity")
            self.state["current_intensity"] = control.intensity
            self.state["cw"] = 0
            self.state["ww"] = 0
            save_state(self.state)
            return {"status": "success", "intensity": control.intensity, "warning": "No devices connected"}
        cw, ww = self.luminaire_ops.calculate_cw_ww_from_cct_intensity(self.state["current_cct"], control.intensity)
        success, failed_ips = await self.luminaire_ops.sendAll(cw, ww)
        if not success:
            raise HTTPException(status_code=500, detail=f"Failed to send to: {failed_ips}")
        self.state["current_intensity"] = control.intensity
        self.state["cw"] = cw
        self.state["ww"] = ww
        save_state(self.state)
        logging.info(f"Set intensity to {control.intensity}, CW: {cw}, WW: {ww}")
        return {"status": "success", "intensity": control.intensity}

    async def set_mode(self, request: ModeRequest):
        #async with self.luminaire_ops._state_lock:
        self.state["auto_mode"] = request.auto
        if not request.auto:
            self.state["scheduler"]["status"] = "idle"
            self.luminaire_ops.stop_event.set()
            self.luminaire_ops.log_basic("Switched to Manual mode")
        else:
            self.luminaire_ops.stop_event.clear()
            self.luminaire_ops.log_basic("Switched to Auto mode")
        save_state(self.state)
        logging.info(f"Set mode to {'Auto' if request.auto else 'Manual'}")
        return {"status": "success", "auto_mode": request.auto}

    async def toggle_system(self, request: ToggleSystemRequest):
        #async with self.luminaire_ops._state_lock:
        self.state["isSystemOn"] = request.isSystemOn
        if not request.isSystemOn:
            self.state["scheduler"]["status"] = "idle"
            self.state["last_state"] = {
                "auto_mode": self.state["auto_mode"],
                "current_scene": self.state["current_scene"],
                "cw": self.state["cw"],
                "ww": self.state["ww"],
                "current_intensity": self.state["current_intensity"]
            }
            #async with self.luminaire_ops._devices_lock:
            if not self.luminaire_ops.devices:
                logging.debug("No devices available, skipping toggle system sendAll")
            else:
                success, failed_ips = await self.luminaire_ops.sendAll(0, 0)
                if not success:
                    self.luminaire_ops.log_advanced(f"Failed to send zero values to IPs: {', '.join(failed_ips)}")
                    logging.warning(f"Failed to send zero values to IPs: {failed_ips}")
            self.luminaire_ops.log_basic("System turned OFF")
            logging.info("System turned OFF")
        else:
            if self.state["last_state"]["auto_mode"] and self.state["last_state"]["current_scene"]:
                self.state["auto_mode"] = True
                self.state["current_scene"] = self.state["last_state"]["current_scene"]
                self.state["loaded_scene"] = self.state["last_state"]["current_scene"]
                self.state["scene_data"] = self.scene_data[self.state["current_scene"]]
                self.state["activationTime"] = datetime.datetime.now().strftime("%H:%M:%S")
                asyncio.create_task(self.luminaire_ops.run_smooth_scheduler(
                    os.path.join(self.config["luminaire_operations"]["scene_directory"], self.state["current_scene"]), self.scene_data))
                self.luminaire_ops.log_basic(f"Restored and activated scene: {self.state['current_scene']}")
            else:
                self.state["cw"] = self.state["last_state"]["cw"]
                self.state["ww"] = self.state["last_state"]["ww"]
                self.state["current_intensity"] = self.state["last_state"]["current_intensity"]
                self.state["current_cct"] = self.luminaire_ops.calculate_cct_from_cw_ww(self.state["cw"], self.state["ww"])
                async with self.luminaire_ops._devices_lock:
                    if not self.luminaire_ops.devices:
                        logging.debug("No devices available, skipping toggle system sendAll")
                    else:
                        success, failed_ips = await self.luminaire_ops.sendAll(self.state["cw"], self.state["ww"])
                        if not success:
                            self.luminaire_ops.log_advanced(f"Failed to send values to IPs: {', '.join(failed_ips)}")
                            logging.warning(f"Failed to send values to IPs: {failed_ips}")
                self.luminaire_ops.log_basic("System turned ON")
            logging.info("System turned ON")
        save_state(self.state)
        return {"status": "success", "isSystemOn": request.isSystemOn}

    async def load_scene(self, request: SceneRequest):
        #async with self.luminaire_ops._state_lock:
        if request.scene not in self.state["available_scenes"]:
            raise HTTPException(status_code=400, detail="Scene not found")
        self.state["loaded_scene"] = request.scene
        self.state["scene_data"] = self.scene_data[request.scene]
        self.state["scheduler"]["status"] = "pending"
        self.luminaire_ops.log_basic(f"Loaded scene: {request.scene}")
        save_state(self.state)
        logging.info(f"Loaded scene: {request.scene}")
        return {"status": "success", "scene": request.scene}

    async def activate_scene(self, request: SceneRequest):
        #async with self.luminaire_ops._state_lock:
        if request.scene not in self.state["available_scenes"]:
            raise HTTPException(status_code=400, detail="Scene not found")
        if self.state["scheduler"]["status"] == "running" and self.state["current_scene"]:
            await self.luminaire_ops.stop_scheduler()
            await asyncio.sleep(0.1)
            logging.debug(f"Stopped running scene: {self.state['current_scene']}")
        self.state["current_scene"] = request.scene
        self.state["loaded_scene"] = request.scene
        self.state["scene_data"] = self.scene_data[request.scene]
        self.state["auto_mode"] = True
        self.state["scheduler"]["status"] = "running"
        self.state["activationTime"] = datetime.datetime.now().strftime("%H:%M:%S")
        asyncio.create_task(self.luminaire_ops.run_smooth_scheduler(
            os.path.join(self.config["luminaire_operations"]["scene_directory"], request.scene), self.scene_data))
        self.luminaire_ops.log_basic(f"Activated scene: {request.scene}")
        save_state(self.state)
        logging.info(f"Activated scene: {request.scene}")
        return {"status": "success", "scene": request.scene}

    async def stop_scheduler(self):
        #async with self.luminaire_ops._state_lock:
        await self.luminaire_ops.stop_scheduler()
        self.state["scheduler"]["status"] = "idle"
        self.state["current_scene"] = None
        self.state["loaded_scene"] = None
        self.state["scene_data"] = {"cct": [], "intensity": []}
        save_state(self.state)
        self.luminaire_ops.log_basic("Scheduler stopped")
        logging.info("Scheduler stopped")
        return {"status": "success"}