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
	"10": { "name": "assignBinbot", "structFormat": "!Bi", "keys": ["cmdId", "binbotNodeId"] },
	"11": { "name": "addLitterMarker", "structFormat": "!B", "keys": ["cmdId"] },
	"12": { "name": "toggleVirtualEmergencyStop", "structFormat": "!B?", "keys": ["cmdId", "enabled"] },
}

class Bluetooth:
	def __init__(self, serviceUuid: str, charUuid: str, autostart: bool = True):
		self.charUuid = charUuid
		self.client = None
		self.nearbyBtRobots = []
		self.nearbyBtRobotsDict = {}
		self.serviceUuid = serviceUuid
		self.timeout = 30.0 # Needs to be a large value as the devices may have a stable signal for a long time
		self._btUpdateCallback = None
		self._nearbyBtRobotsDictLock = Lock()

		self.scanner = BleakScanner(
			service_uuids=[serviceUuid],
			scanning_mode="active",
			detection_callback=self._nearbyRobotUpdateCallback
		)

		# Create a dedicated event loop for this class
		self.loop = asyncio.new_event_loop()

		# Start the main loop in a background daemon thread
		self._loopThread = Thread(target=self._startBackgroundLoop, daemon=True)
		self._loopThread.start()

		# Start the cleanup loop in a background daemon thread
		self._cleanupThread = Thread(target=self._cleanupStaleDevices, daemon=True)
		self._cleanupThread.start()

		if autostart:
			self.startScanning()

	def _startBackgroundLoop(self) -> None:
		""" This runs continuously in the background thread """

		asyncio.set_event_loop(self.loop)
		self.loop.run_forever()

	def _nearbyRobotUpdateCallback(self, device: Any, advData: Any) -> None:
		""" This is called whenever the signal strength of any device has changed """

		with self._nearbyBtRobotsDictLock:
			self.nearbyBtRobotsDict[device.address] = {
				"id": device.name,
				"mac": device.address,
				"rssi": advData.rssi,
				"last_seen": time()
			}

		# Update list
		self.nearbyBtRobots = list(self.nearbyBtRobotsDict.values())

		# Run the callback if it has been set
		if self._btUpdateCallback:
			self._btUpdateCallback(self.nearbyBtRobots)

	def _cleanupStaleDevices(self) -> None:
		while True:
			with self._nearbyBtRobotsDictLock:
				stale = [
					mac for mac, robot in self.nearbyBtRobotsDict.items()
					if (time() - robot["last_seen"]) > self.timeout
				]

				for mac in stale:
					del self.nearbyBtRobotsDict[mac]

				if len(stale) != 0:
					# Update list
					self.nearbyBtRobots = list(self.nearbyBtRobotsDict.values())

					# Run the callback if it has been set
					if self._btUpdateCallback:
						self._btUpdateCallback(self.nearbyBtRobots)

			sleep(1.0)

	def setBtUpdateCallback(self, callback: Callable[[list[dict]], None]) -> None:
		self._btUpdateCallback = callback

	def startScanning(self) -> None:
		""" Synchronous wrapper to start scanning. Blocks until started """

		future = asyncio.run_coroutine_threadsafe(self._asyncStartScanning(), self.loop)
		future.result()

	def stopScanning(self) -> None:
		""" Synchronous wrapper to stop scanning. Blocks until stopped """

		future = asyncio.run_coroutine_threadsafe(self._asyncStopScanning(), self.loop)
		future.result()

	def provision(self, macAddress: str, loraId: int, secretKey: str) -> Future:
		""" Synchronous wrapper for provisioning """

		return asyncio.run_coroutine_threadsafe(
			self._asyncProvision(macAddress, loraId, secretKey),
			self.loop
		)

	async def _asyncStartScanning(self) -> None:
		await self.scanner.start()
		print("[ BT ]: Scanning Started")

	async def _asyncStopScanning(self) -> None:
		await self.scanner.stop()
		print("[ BT ]: Scanning Stopped")

	async def _asyncProvision(self, macAddress: str, loraNodeId: int, secretKey: str) -> bool:
		print("[ BT ]: Attempting to Connect")

		# Properly stop the instance-based scanner
		try:
			await self.scanner.stop()
			await asyncio.sleep(1.0)
		except:
			pass # Ignore error if it wasn't running

		# Connect using the Client
		self.client = BleakClient(macAddress, timeout=30.0)

		try:
			print(f"[ BT ]: Connecting to {macAddress}...")
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

			payloadData = {
				"loraNodeId": loraNodeId,
				"secretKey": secretKey,
				"protocol": PROTOCOL
			}
			payload = dumps(payloadData, separators=(",", ":")).encode("utf-8")

			# Automatically chunk based on negotiated MTU
			characteristic = self.client.services.get_characteristic(self.charUuid)
			chunkSize = characteristic.max_write_without_response_size - 10
			print(f"[ BT ]: Payload Size: {len(payload)} bytes - Chunking into {chunkSize} byte segments")

			for i in range(0, len(payload), chunkSize):
				chunk = payload[i:i + chunkSize]
				await self.client.write_gatt_char(self.charUuid, chunk)

			print("[ BT ]: Provisioning Successful")

			return True

		except Exception as e:
			print(f"[ BT ]: Provisioning Failed: {e}")
			print(f"{len(str(e))} = {str(e)}")

			return False

class Lora:
	def __init__(self, port: str = "/dev/arduino_mega", baud: int = 115200):
		self.ready = False
		self._loraUpdateCallback = None

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

	def _listen(self) -> None:
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
						nodeId, hexPayload = line.split(":", 1)
						data = bytes.fromhex(hexPayload)
						cmdId = data[0]

						# Look up the command in the protocol
						cmdInfo = PROTOCOL.get(str(cmdId))
						if cmdInfo and "structFormat" in cmdInfo:
							unpacked = struct.unpack(cmdInfo["structFormat"], data)
							payloadDict = dict(zip(cmdInfo["keys"], unpacked)) # Zip keys and values into a dictionary
							print(f"[LoRa]: {nodeId} ({cmdInfo['name']}): {payloadDict}")

							if self._loraUpdateCallback is not None:
								self._loraUpdateCallback(cmdInfo["name"], nodeId, payloadDict)
						else:
							print(f"[LoRa]: {nodeId} (Unknown ID {cmdId}): {hexPayload}")
					except Exception as e:
						print(f"[LoRa]: Raw - {line} (Parse Error: {e})")
				else:
					print(f"[LoRa]: {line}")

	def transmit(self, cmdName: str, payload: dict, nodeId: int) -> None:
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
						print(f"[LoRa]: Sending {nodeId:02}:{payload} ({packed.hex()})")
						self.ser.write(f"{nodeId:02}:{packed.hex()}\n".encode("utf-8"))

				break
		else:
			# Simple command with just the ID as a single byte hex
			print(f"Received unknown command from UCS: {cmdName}")

	def getProtocolCmdId(self, cmdName: str) -> Optional[int]:
		for cmdId, info in PROTOCOL.items():
			if info["name"] == cmdName:
				return int(cmdId)

		return None

	def setLoraCallback(self, callback: Callable[[str, str, dict], None]):
		self._loraUpdateCallback = callback

class Comms:
	def __init__(self):
		# UUIDs should match the Jetson's BLE Server
		self.serviceUuid = "036f33e0-9573-4b0e-88d1-18af960d5a95"
		self.charUuid = "92eda5fb-c187-4f41-aaf2-3931b9cb4c56"

		self.lora = Lora()
		while not self.lora.ready:
			sleep(0.1)

		self.bt = Bluetooth(self.serviceUuid, self.charUuid)

	def connectNewRobot(self, mac: str, loraId: int, encryptionKey: str) -> bool:
		""" High-level method to pair a robot and assign it a LoRa ID """

		# Stop scanning while connecting to avoid radio interference
		self.bt.stopScanning()

		# Provision via BLE
		future = self.bt.provision(mac, loraId, encryptionKey)
		success = future.result() # Wait for the result

		# Resume scanning
		self.bt.startScanning()

		return success

if __name__ == "__main__":
	comms = Comms()

	try:
		while True:
			cmd = input("Command: ")

			if cmd == "list":
				print(f"Nearby: {comms.bt.nearbyBtRobots}")
			elif cmd == "pair":
				comms.connectNewRobot("F8:3D:C6:56:B1:BA", 67, "password123321")
			elif cmd:
				comms.lora.transmit(cmd, 67)
	except KeyboardInterrupt:
		print("Exiting")
