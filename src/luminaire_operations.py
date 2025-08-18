import asyncio
import threading
import logging
import time
import re
import csv
import datetime
import psutil
import os

class LuminaireOperations:
    def __init__(self, config, state):
        self._devices_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self.config = config
        self.state = state
        self.min_cct = config["luminaire_operations"]["min_cct"]
        self.max_cct = config["luminaire_operations"]["max_cct"]
        self.min_intensity = config["luminaire_operations"]["min_intensity"]
        self.max_intensity = config["luminaire_operations"]["max_intensity"]
        self.INACTIVITY_THRESHOLD = config["luminaire_operations"]["inactivity_threshold"]
        self.devices = {}
        self.current_interval_index = 0
        self.total_intervals = 0
        self.start_time = None
        self.stop_event = threading.Event()
        self.paused = False
        self.current_scheduler_task = None
        logging.debug("LuminaireOperations initialized")

    def stop_scheduler(self):
        """Stop the current scheduler task and reset state."""
        self.stop_event.set()
        if self.current_scheduler_task is not None:
            self.current_scheduler_task.cancel()
            self.current_scheduler_task = None
            logging.debug("Current scheduler task canceled")
        with self._state_lock:
            self.state["scene_data"] = {"cct": [], "intensity": []}
            self.state["current_scene"] = None
            self.state["loaded_scene"] = None
            self.state["scheduler"]["status"] = "idle"
        self.log_basic("Scheduler stopped")
        logging.debug("Scheduler stopped and state reset")

    def get_system_stats(self):
        """Fetch system statistics (CPU, memory, temperature)."""
        logging.debug("Fetching system stats")
        cpu_percent, mem_percent = psutil.cpu_percent(interval=None), psutil.virtual_memory().percent
        temperature = None
        try:
            temps = psutil.sensors_temperatures()
            for sensor in ['coretemp', 'k10temp', 'cpu_thermal']:
                if sensor in temps and temps[sensor]:
                    temperature = temps[sensor][0].current
                    break
            if temperature is None:
                logging.debug("No temperature sensor data available")
            else:
                logging.debug(f"Temperature: {temperature}°C")
        except (AttributeError, NotImplementedError):
            logging.warning("Temperature monitoring not supported on this platform")
        logging.debug(f"System stats - CPU: {cpu_percent}%, Mem: {mem_percent}%, Temp: {temperature}°C")
        return cpu_percent, mem_percent, temperature

    def add(self, ip: str, writer):
        """Add a luminaire device."""
        with self._devices_lock:
            self.devices[ip] = {"writer": writer, "last_seen": time.time(), "cw": 50.0, "ww": 50.0}
            if ip not in self.state["connected_devices"]:
                self.state["connected_devices"][ip] = {"cw": 50.0, "ww": 50.0}
            self.log_advanced(f"Luminaire connected: {ip}")
            logging.info(f"Added luminaire {ip}")
            logging.debug(f"Device list updated: {list(self.devices.keys())}")

    def disconnect(self, ip: str):
        """Disconnect a luminaire device."""
        with self._devices_lock:
            if ip in self.devices:
                writer = self.devices[ip].get("writer")
                if writer:
                    writer.close()
                del self.devices[ip]
                if ip in self.state["connected_devices"]:
                    del self.state["connected_devices"][ip]
                self.log_advanced(f"Luminaire disconnected: {ip}")
            logging.info(f"Disconnected {ip}")
            logging.debug(f"Device list after disconnect: {list(self.devices.keys())}")

    def clearALL(self):
        """Disconnect all luminaire devices."""
        with self._devices_lock:
            for ip in list(self.devices.keys()):
                self.disconnect(ip)
            self.state["connected_devices"] = {}
        self.log_advanced("All luminaires disconnected.")
        logging.info("All luminaires disconnected.")
        logging.debug("Device list cleared")

    def processACK(self, ip: str, response: str) -> bool:
        """Process ACK response from a luminaire."""
        logging.debug(f"Processing ACK from {ip}: {response}")
        try:
            if isinstance(response, bytes):
                response = response.decode('utf-8', errors='ignore')
            match = re.match(r"\*001(\d{3})(\d{3})ACK(\d{3})(\d{3})#", response)
            if not match:
                logging.warning(f"Invalid ACK format from {ip}: {response}")
                return False
            cw_raw, ww_raw = match.group(3), match.group(4)
            cw, ww = int(cw_raw) / 10, int(ww_raw) / 10
            with self._devices_lock:
                if ip in self.devices:
                    self.update_cw_ww_intensity(ip, cw, ww)
                    self.log_advanced(f"Received [{ip}]: {response}")
                    logging.debug(f"Updated device {ip} - CW: {cw}%, WW: {ww}%")
                    return True
            return False
        except Exception as e:
            self.log_advanced(f"Error processing ACK for {ip}: {e}")
            logging.error(f"Error processing ACK for {ip}: {e}", exc_info=True)
            return False

    def log_basic(self, message: str):
        """Log a basic message to state."""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        with self._state_lock:
            self.state["basicLogs"].append(f"[{timestamp}] {message}")
            self.state["basicLogs"] = self.state["basicLogs"][-self.config["luminaire_operations"]["log_basic_max_entries"]:]
        logging.info(f"Basic Log: {message}")

    def log_advanced(self, message: str):
        """Log an advanced message to state."""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        with self._state_lock:
            self.state["advancedLogs"].append(f"[{timestamp}] {message}")
            self.state["advancedLogs"] = self.state["advancedLogs"][-self.config["luminaire_operations"]["log_advanced_max_entries"]:]
        logging.debug(f"Advanced Log: {message}")

    def list(self) -> dict:
        """List connected devices."""
        logging.debug("Listing connected devices")
        with self._devices_lock:
            now = time.time()
            devices = {ip: {"cw": data.get("cw"), "ww": data.get("ww")} for ip, data in self.devices.items() if now - data["last_seen"] < self.INACTIVITY_THRESHOLD}
            logging.debug(f"Active devices: {list(devices.keys())}")
            return devices

    def update_cw_ww_intensity(self, ip: str, cw: float, ww: float):
        """Update CW/WW values for a device."""
        with self._devices_lock:
            if ip in self.devices:
                self.devices[ip].update({"cw": cw, "ww": ww, "last_seen": time.time()})
                self.state["connected_devices"][ip] = {"cw": cw, "ww": ww}
                logging.debug(f"Updated {ip} - CW: {cw}%, WW: {ww}%")

    async def send(self, ip: str, cw: float, ww: float) -> bool:
        """Send CW/WW values to a specific device."""
        logging.debug(f"Sending to {ip} - CW: {cw}%, WW: {ww}%")
        retries = 0
        with self._devices_lock:
            if ip not in self.devices:
                logging.warning(f"Device {ip} not found for sending")
                return False
            writer = self.devices[ip]["writer"]
        while retries < self.config["luminaire_operations"]["max_retries"]:
            try:
                command = self.buildCommand(ip, cw, ww)
                writer.write(command.encode())
                await writer.drain()
                self.log_advanced(f"Sent [{ip}]: {command}")
                logging.debug(f"Successfully sent to {ip}")
                return True
            except (ConnectionError, OSError) as e:
                retries += 1
                self.log_advanced(f"Error sending to {ip} (retry {retries}/{self.config['luminaire_operations']['max_retries']}): {e}")
                logging.warning(f"Error sending to {ip} (retry {retries}/{self.config['luminaire_operations']['max_retries']}): {e}")
                if retries >= self.config["luminaire_operations"]["max_retries"]:
                    self.disconnect(ip)
            except Exception as e:
                retries += 1
                self.log_advanced(f"Unexpected error sending to {ip} (retry {retries}/{self.config['luminaire_operations']['max_retries']}): {e}")
                logging.warning(f"Unexpected error sending to {ip} (retry {retries}/{self.config['luminaire_operations']['max_retries']}): {e}")
                if retries >= self.config["luminaire_operations"]["max_retries"]:
                    self.disconnect(ip)
            await asyncio.sleep(0.5)
        logging.error(f"Failed to send to {ip} after {self.config['luminaire_operations']['max_retries']} retries")
        return False

    async def sendAll(self, cw: float, ww: float) -> tuple[bool, list]:
        """Send CW/WW values to all devices."""
        logging.debug(f"Sending to all devices - CW: {cw}%, WW: {ww}%")
        failed_ips = []
        tasks = []
        with self._devices_lock:
            if not self.devices:
                logging.warning("No devices available to send to")
                return False, []
            for ip, device in self.devices.items():
                command = self.buildCommand(ip, cw, ww)
                tasks.append(self.async_send(ip, device["writer"], command))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for ip, result in zip(list(self.devices.keys()), results):
            if isinstance(result, Exception):
                failed_ips.append(ip)
                self.log_advanced(f"Error sending to {ip}: {result}")
                logging.warning(f"Error sending to {ip}: {result}")
        success = len(failed_ips) == 0
        if not success:
            self.log_advanced(f"Failed to send to luminaires: {', '.join(failed_ips)}")
        self.state["current_cct"] = self.calculate_cct_from_cw_ww(cw, ww)
        logging.debug(f"SendAll completed - Success: {success}, Failed IPs: {failed_ips}")
        return success, failed_ips

    async def async_send(self, ip: str, writer, command: str) -> None:
        """Helper method to send a command to a device."""
        try:
            writer.write(command.encode())
            await writer.drain()
            self.log_advanced(f"Sent [{ip}]: {command}")
            logging.debug(f"Successfully sent to {ip}")
        except Exception as e:
            self.log_advanced(f"Error sending to {ip}: {e}")
            logging.warning(f"Error sending to {ip}: {e}")
            raise

    def calculate_cw_ww_from_cct_intensity(self, cct: float, intensity: float) -> tuple[float, float]:
        """Calculate CW/WW from CCT and intensity."""
        logging.debug(f"Calculating CW/WW from CCT: {cct}, Intensity: {intensity}")
        cct = max(self.min_cct, min(self.max_cct, cct))
        intensity = max(self.min_intensity, min(self.max_intensity, intensity))
        intensity_percent = intensity / self.max_intensity
        cw_base = (cct - self.min_cct) / ((self.max_cct - self.min_cct) / 100.0)
        ww_base = 100.0 - cw_base
        cw = max(0.0, min(99.99, cw_base * intensity_percent))
        ww = max(0.0, min(99.99, ww_base * intensity_percent))
        logging.debug(f"Calculated - CW: {cw}%, WW: {ww}%")
        return cw, ww

    def calculate_cct_from_cw_ww(self, cw: float, ww: float) -> float:
        """Calculate CCT from CW/WW values."""
        logging.debug(f"Calculating CCT from CW: {cw}%, WW: {ww}%")
        total = cw + ww
        cct = 3500 if total == 0 else self.min_cct + ((cw / total) * 100 * ((self.max_cct - self.min_cct) / 100.0))
        logging.debug(f"Calculated CCT: {cct}K")
        return cct

    def buildCommand(self, ip: str, cw: float, ww: float) -> str:
        """Build command string for a luminaire."""
        logging.debug(f"Building command for {ip} - CW: {cw}%, WW: {ww}%")
        try:
            ip_parts = ip.split(".")
            ip3, ip4 = f"{int(ip_parts[2]):03}", f"{int(ip_parts[3]):03}"
            command = f"*{ip3}{ip4}{int(cw*10):03}{int(ww*10):03}##"
            logging.debug(f"Built command: {command}")
            return command
        except (ValueError, IndexError) as e:
            self.log_advanced(f"Error building command for {ip}: {e}")
            logging.error(f"Error building command for {ip}: {e}", exc_info=True)
            raise ValueError(f"Invalid IP: {ip}")

    async def run_smooth_scheduler(self, csv_path: str, scene_data: dict):
        """Run the scheduler for a given scene."""
        logging.debug(f"Starting scheduler for {csv_path}")
        self.stop_event.clear()
        self.paused = False
        self.current_scheduler_task = asyncio.current_task()
        self.state["scheduler"]["status"] = "running"
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_basic(f"Activated scene: {os.path.basename(csv_path)}")
        try:
            scene_name = os.path.basename(csv_path)
            if scene_name not in scene_data:
                raise FileNotFoundError(f"Scene {scene_name} not found in scene_data")

            with open(csv_path, newline='') as csvfile:
                reader = csv.reader(csvfile)
                next(reader)
                scene_data_list = [(int(row[0].split(':')[0]) * 60 + int(row[0].split(':')[1]), float(row[1]), float(row[2])) for row in reader]

            self.total_intervals = len(scene_data_list)
            self.state["scheduler"]["total_interbands"] = 8640

            start_time = datetime.datetime.now()
            seconds_since_midnight = (start_time.hour * 3600) + (start_time.minute * 60) + start_time.second
            self.current_interval_index = seconds_since_midnight
            self.start_time = time.time()
            logging.debug(f"Scheduler started at index: {self.current_interval_index}, start_time: {self.start_time}")

            last_interval_update = self.current_interval_index
            update_interval = self.config.get("luminaire_operations", {}).get("scheduler_update_interval", 2.0)

            while not self.stop_event.is_set() and self.current_interval_index < 86400:
                loop_start = time.time()
                if self.paused:
                    await asyncio.sleep(0.1)
                    logging.debug("Scheduler paused")
                    continue

                elapsed_time = time.time() - self.start_time
                current_idx = int(seconds_since_midnight + elapsed_time) % 86400
                self.current_interval_index = current_idx

                current_interval = (current_idx // 1800) % self.total_intervals
                next_interval = (current_interval + 1) % self.total_intervals
                interval_progress = (current_idx % 1800) / 1799

                start_min, start_cct, start_intensity = scene_data_list[current_interval]
                end_min, end_cct, end_intensity = scene_data_list[next_interval]
                time_diff = ((end_min - start_min + 1440) % 1440) * 60
                cct_diff = end_cct - start_cct
                intensity_diff = end_intensity - start_intensity

                with self._state_lock:
                    self.state["current_cct"] = start_cct + (cct_diff * interval_progress)
                    self.state["current_intensity"] = start_intensity + (intensity_diff * interval_progress)
                    cw, ww = self.calculate_cw_ww_from_cct_intensity(self.state["current_cct"], self.state["current_intensity"])
                    self.state["cw"], self.state["ww"] = cw, ww
                    self.state["scheduler"]["interval_progress"] = (current_idx / 86400) * 100
                    self.state["scheduler"]["current_interval"] = current_idx // 10

                success, failed_ips = await self.sendAll(cw, ww)
                if not success:
                    with self._state_lock:
                        self.state["alert"] = f"Failed to send to luminaires: {', '.join(failed_ips)}"
                    logging.warning(f"SendAll failed for IPs: {failed_ips}")
                else:
                    logging.debug(f"Successfully sent CW: {cw}, WW: {ww} to luminaires")

                if current_idx // 10 != last_interval_update // 10:
                    last_interval_update = current_idx
                    logging.info(f"Interval update - Index: {current_idx}, Progress: {self.state['scheduler']['interval_progress']}%")

                logging.info(f"Index: {current_idx}, Interval: {current_interval}, "
                            f"Progress: {interval_progress:.2f}, CCT: {self.state['current_cct']:.1f}K, "
                            f"Intensity: {self.state['current_intensity']:.1f}lux, CW: {cw:.1f}%, WW: {ww:.1f}%")

                elapsed = time.time() - loop_start
                sleep_time = max(0, update_interval - elapsed)
                await asyncio.sleep(sleep_time)

            if not self.stop_event.is_set():
                with self._state_lock:
                    self.state["scheduler"]["status"] = "completed"
                now = datetime.datetime.now().strftime("%H:%M:%S")
                self.log_basic(f"Scene completed: {os.path.basename(csv_path)}")
                logging.info("Scene execution completed successfully!")
                logging.debug("Scheduler loop completed")
        except FileNotFoundError:
            self.log_advanced(f"CSV file not found: {csv_path}")
            logging.error(f"CSV file not found: {csv_path}", exc_info=True)
            self.state["scheduler"]["status"] = "failed"
        except Exception as e:
            self.log_advanced(f"Error running scheduler: {e}")
            logging.error(f"Error running scheduler: {e}", exc_info=True)
            self.state["scheduler"]["status"] = "failed"
        finally:
            logging.debug(f"Scheduler for {csv_path} terminated")
            with self._state_lock:
                self.state["scheduler"]["status"] = "idle" if self.state["scheduler"]["status"] != "failed" else "failed"

    async def cleanup_stale_devices(self):
        """Periodically clean up stale devices."""
        while True:
            with self._devices_lock:
                now = time.time()
                stale_ips = [ip for ip, data in self.devices.items() if now - data["last_seen"] > self.INACTIVITY_THRESHOLD]
                for ip in stale_ips:
                    await asyncio.sleep(0.1)
                    self.disconnect(ip)
            await asyncio.sleep(self.config["luminaire_operations"]["cleanup_interval"])

    def pause_scheduler(self):
        """Pause the scheduler."""
        self.paused = True
        self.log_basic("Scheduler paused")
        logging.info("Scheduler paused")

    def resume_scheduler(self):
        """Resume the scheduler."""
        self.paused = False
        self.start_time = time.time() - (self.current_interval_index * self.config["luminaire_operations"]["scheduler_update_interval"])
        self.log_basic("Scheduler resumed")
        logging.info("Scheduler resumed")
        logging.debug(f"Resumed at index: {self.current_interval_index}, start_time: {self.start_time}")

    def adjust_cw(self, delta: float, ip: str = None) -> bool:
        """Adjust CW value for a specific device or all devices."""
        logging.debug(f"Adjusting CW by {delta} for IP: {ip}")
        if ip:
            with self._devices_lock:
                if ip not in self.devices or self.devices[ip].get("cw") is None:
                    logging.warning(f"Device {ip} not found or no CW data")
                    return False
                current_cw = self.devices[ip]["cw"]
                new_cw = max(0.0, min(100.0, current_cw + delta))
                self.log_basic(f"Adjusted CW to {new_cw}%")
                return asyncio.run_coroutine_threadsafe(self.send(ip, new_cw, 100 - new_cw), asyncio.get_event_loop()).result()
        else:
            with self._devices_lock:
                device_with_cw = next((ip for ip, data in self.devices.items() if data.get("cw") is not None), None)
                if not device_with_cw:
                    logging.warning("No device with CW data found")
                    return False
                current_cw = self.devices[device_with_cw]["cw"]
            new_cw = max(0.0, min(100.0, current_cw + delta))
            self.log_basic(f"Adjusted CW to {new_cw}%")
            return asyncio.run_coroutine_threadsafe(self.sendAll(new_cw, 100 - new_cw), asyncio.get_event_loop()).result()[0]