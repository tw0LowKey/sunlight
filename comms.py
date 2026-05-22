import asyncio
import struct
from bleak import BleakScanner, BleakClient
from json import dumps
from serial import Serial
from threading import Lock, Thread
from time import sleep, time

PROTOCOL = {
	"0":  { "name": "heartbeat", "structFormat": "!Bhdd", "keys": ["cmdId", "battery", "lat", "lng"] },
	"1":  { "name": "areaCoords", "structFormat": "!Bdddd", "keys": ["cmdId", "topLeftLatitude", "topLeftLongitude", "bottomRightLatitude", "bottomRightLongitude"] },
	"2":  { "name": "sendCameraIpAddress", "structFormat": "!BI", "keys": ["cmdId", "ipAddress"] },
	"3":  { "name": "movement", "structFormat": "!Bc", "keys": ["cmdId", "direction"] },
	"4":  { "name": "resumeAuto", "structFormat": "!B", "keys": ["cmdId"] },
	"5":  { "name": "setArmStatus", "structFormat": "!B?", "keys": ["cmdId", "enabled"] },
	"6":  { "name": "setBeeperStatus", "structFormat": "!B?", "keys": ["cmdId", "enabled"] },
	"7":  { "name": "returnToStart", "structFormat": "!B", "keys": ["cmdId"] },
	"8":  { "name": "sendLeaderToFollower", "structFormat": "!Biddff?", "keys": ["cmdId", "seq", "latitude", "longitude", "orientation_z", "orientation_w", "call_bin"] },
	"9":  { "name": "sendFollowerToLeader", "structFormat": "!Bi?ff?", "keys": ["cmdId", "seq", "parked", "park_x", "park_y", "bin_ready"] },
	"10": { "name": "assignBinbot", "structFormat": "!Bi", "keys": ["cmdId", "binbotNodeId"] }
}

class Bluetooth:
	def __init__(self, service_uuid: str, char_uuid: str, autostart: bool = True):
		self.char_uuid = char_uuid
		self.client = None
		self.nearby_bt_robots = []
		self.nearby_bt_robots_dict = {}
		self.service_uuid = service_uuid
		self.timeout = 30.0 # Needs to be a large value as the devices may have a stable signal for a long time
		self._bt_update_callback = None
		self._nearby_bt_robots_dict_lock = Lock()

		self.scanner = BleakScanner(
			service_uuids=[service_uuid],
			scanning_mode="active",
			detection_callback=self._nearby_robot_update_callback
		)

		# Create a dedicated event loop for this class
		self.loop = asyncio.new_event_loop()

		# Start the main loop in a background daemon thread
		self._loop_thread = Thread(target=self._start_background_loop, daemon=True)
		self._loop_thread.start()

		# Start the cleanup loop in a background daemon thread
		self._cleanup_thread = Thread(target=self._cleanup_stale_devices, daemon=True)
		self._cleanup_thread.start()

		if autostart:
			self.start_scanning()

	def _start_background_loop(self):
		""" This runs continuously in the background thread """

		asyncio.set_event_loop(self.loop)
		self.loop.run_forever()

	def _nearby_robot_update_callback(self, device, adv_data):
		""" This is called whenever the signal strength of any device has changed """

		with self._nearby_bt_robots_dict_lock:
			self.nearby_bt_robots_dict[device.address] = {
				"id": device.name,
				"mac": device.address,
				"rssi": adv_data.rssi,
				"last_seen": time()
			}

		# Update list
		self.nearby_bt_robots = list(self.nearby_bt_robots_dict.values())

		# Run the callback if it has been set
		if self._bt_update_callback:
			self._bt_update_callback(self.nearby_bt_robots)

	def _cleanup_stale_devices(self):
		while True:
			with self._nearby_bt_robots_dict_lock:
				stale = [
					mac for mac, robot in self.nearby_bt_robots_dict.items()
					if (time() - robot["last_seen"]) > self.timeout
				]

				for mac in stale:
					del self.nearby_bt_robots_dict[mac]

				if len(stale) != 0:
					# Update list
					self.nearby_bt_robots = list(self.nearby_bt_robots_dict.values())

					# Run the callback if it has been set
					if self._bt_update_callback:
						self._bt_update_callback(self.nearby_bt_robots)

			sleep(1.0)

	def set_bt_update_callback(self, callback):
		self._bt_update_callback = callback

	def start_scanning(self):
		""" Synchronous wrapper to start scanning. Blocks until started """

		future = asyncio.run_coroutine_threadsafe(self._async_start_scanning(), self.loop)
		future.result()

	def stop_scanning(self):
		""" Synchronous wrapper to stop scanning. Blocks until stopped """

		future = asyncio.run_coroutine_threadsafe(self._async_stop_scanning(), self.loop)
		future.result()

	def provision(self, mac_address, lora_id, secret_key):
		""" Synchronous wrapper for provisioning """

		return asyncio.run_coroutine_threadsafe(
			self._async_provision(mac_address, lora_id, secret_key),
			self.loop
		)

	async def _async_start_scanning(self):
		await self.scanner.start()
		print("[ BT ]: Scanning Started")

	async def _async_stop_scanning(self):
		await self.scanner.stop()
		print("[ BT ]: Scanning Stopped")

	async def _async_provision(self, mac_address, loraNodeId, secret_key):
		print("[ BT ]: Attempting to Connect")

		# Properly stop the instance-based scanner
		try:
			await self.scanner.stop()
			await asyncio.sleep(1.0)
		except:
			pass # Ignore error if it wasn't running

		# Connect using the Client
		self.client = BleakClient(mac_address, timeout=30.0)

		try:
			print(f"[ BT ]: Connecting to {mac_address}...")
			for attempt in range(3):
				try:
					await self.client.connect()
					break
				except Exception as e:
					print(f"[ BT ]: Provisioning Failed (Attempt {attempt + 1}): {e}")
					await asyncio.sleep(2)
			else:
				return False

			print("[ BT ]: Connected - Sending Initial Payload")

			payload_data = {
				"loraNodeId": loraNodeId,
				"secretKey": secret_key,
				"protocol": PROTOCOL
			}
			payload = dumps(payload_data, separators=(",", ":")).encode("utf-8")

			# Automatically chunk based on negotiated MTU
			characteristic = self.client.services.get_characteristic(self.char_uuid)
			chunkSize = characteristic.max_write_without_response_size - 10
			print(f"[ BT ]: Payload Size: {len(payload)} bytes - Chunking into {chunkSize} byte segments")

			for i in range(0, len(payload), chunkSize):
				chunk = payload[i:i + chunkSize]
				await self.client.write_gatt_char(self.char_uuid, chunk)

			print("[ BT ]: Provisioning Successful")

			return True

		except Exception as e:
			print(f"[ BT ]: Provisioning Failed: {e}")
			print(f"{len(str(e))} = {str(e)}")

			return False

class Lora:
	def __init__(self, port="/dev/arduino_mega", baud=115200):
		self.ready = False
		self._lora_update_callback = None

		try:
			self.ser = Serial(port, baud, timeout=1)
			print(f"\n[LoRa]: Connected on {port}")
		except Exception as e:
			self.ser = None
			print(f"[LoRa]: Error: {e}")
			exit()

		self.listener = Thread(target=self._listen, daemon=True)
		self.listener.start()

		while not self.ready:
			sleep(0.1)

	def _listen(self):
		while self.ser:
			if self.ser.in_waiting > 0:
				line = self.ser.readline().decode("utf-8", errors="replace").strip()

				if line == "RADIO MODULE READY":
					self.ready = True
					print(f"[LoRa]: {line}")
					continue

				# Check for formatted packet (node_id:payload)
				if ":" in line:
					try:
						node_id, hex_payload = line.split(":", 1)
						data = bytes.fromhex(hex_payload)
						cmd_id = data[0]

						# Look up the command in the protocol
						cmd_info = PROTOCOL.get(str(cmd_id))
						if cmd_info and "structFormat" in cmd_info:
							unpacked = struct.unpack(cmd_info["structFormat"], data)
							# Zip keys and values into a dictionary
							payload_dict = dict(zip(cmd_info["keys"], unpacked))
							print(f"[LoRa]: {node_id} ({cmd_info['name']}): {payload_dict}")

							if self._lora_update_callback is not None:
								self._lora_update_callback(cmd_info["name"], node_id, payload_dict)
						else:
							print(f"[LoRa]: {node_id} (Unknown ID {cmd_id}): {hex_payload}")
					except Exception as e:
						print(f"[LoRa]: Raw - {line} (Parse Error: {e})")
				else:
					print(f"[LoRa]: {line}")

	def transmit(self, cmdName: str, payload: dict, node_id: int):
		# Encode the data
		for cid, info in PROTOCOL.items():
			if info["name"] == cmdName:
				if "structFormat" in info:
					# Extract values in correct order based on 'keys'
					# Skip cmdId in keys as it's the cid itself
					args = [int(cid)]

					for key in info["keys"][1:]: # Skip the command ID as it is always first
						val = payload.get(key)

						# Convert to bytes if it's a single character for '!c'
						if isinstance(val, str) and len(val) == 1:
							val = val.encode("ascii")

						args.append(val)

					try:
						packed = struct.pack(info["structFormat"], *args)
					except struct.error as e:
						print(f"[LoRa]: The struct packing has failed - the issue is likely that the command keys do not match those that are trying to be sent - payload ({list(payload.keys())}) | command keys ({info['keys']}): {e}")

						return None

					# Send the data to the arduino
					if self.ser:
						print(f"[LoRa]: Sending {node_id:02}:{payload} ({packed.hex()})")
						self.ser.write(f"{node_id:02}:{packed.hex()}\n".encode("utf-8"))

				break
		else:
			# Simple command with just the ID as a single byte hex
			print(f"Received unknown command from UCS: {cmdName}")

	def getProtocolCmdId(self, cmdName: str):
		for cmdId, info in PROTOCOL.items():
			if info["name"] == cmdName:
				return int(cmdId)

		return None

	def set_lora_callback(self, callback):
		self._lora_update_callback = callback

class Comms:
	def __init__(self):
		# UUIDs should match the Jetson's BLE Server
		self.service_uuid = "036f33e0-9573-4b0e-88d1-18af960d5a95"
		self.char_uuid = "92eda5fb-c187-4f41-aaf2-3931b9cb4c56"

		self.lora = Lora()
		while not self.lora.ready:
			sleep(0.1)

		self.bt = Bluetooth(self.service_uuid, self.char_uuid)

	def connect_new_robot(self, mac, lora_id, encryption_key):
		""" High-level method to pair a robot and assign it a LoRa ID """

		# Stop scanning while connecting to avoid radio interference
		self.bt.stop_scanning()

		# Provision via BLE
		future = self.bt.provision(mac, lora_id, encryption_key)
		success = future.result() # Wait for the result

		# Resume scanning
		self.bt.start_scanning()

		return success

if __name__ == "__main__":
	comms = Comms()

	try:
		while True:
			cmd = input("Command: ")

			if cmd == "list":
				print(f"Nearby: {comms.bt.nearby_bt_robots}")
			elif cmd == "pair":
				comms.connect_new_robot("F8:3D:C6:56:B1:BA", 67, "password123321")
			elif cmd:
				comms.lora.transmit(cmd, 67)
	except KeyboardInterrupt:
		print("Exiting")
