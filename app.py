import logging
import socket
from comms import Comms, PROTOCOL
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
from json import JSONDecodeError, load, dump
from os import getenv
from os.path import exists

__version__ = 1.0

GPS_BASE_STATION_HOST = "reach-base.local"
GPS_BASE_STATION_PORT = 9001

app = Flask(__name__)
app.config["SECRET_KEY"] = getenv("FLASK_SECRET_KEY")
socketio = SocketIO(app)

log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

if not app.config["SECRET_KEY"]:
	raise ValueError("No FLASK_SECRET_KEY set - Please set the secret key before continuing")

# ---------------------------------------------------------------------------- #
#                                     STATE                                    #
# ---------------------------------------------------------------------------- #

state = {
	"comms": None,
	"robots": []
}

def onBtUpdate(nearbyRobots: list[dict]) -> None:
	"""
	Callback triggered when the list of nearby Bluetooth robots is updated
	Updates the global state and emits a 'robot_update' event to the frontend if changes occurred
	"""

	nearbyDict = { n["id"]: n for n in nearbyRobots }
	newRobots = []

	# Process existing robots
	for r in state["robots"]:
		# Keep connected or pairing robots regardless of proximity
		if r.get("connected") or r.get("status") == "pairing":
			newRobots.append(r)
		# Keep idle or failed robots ONLY if they are still nearby
		elif r["id"] in nearbyDict:
			# Update MAC if it changed, but keep status (idle / failed)
			r["mac"] = nearbyDict[r["id"]]["mac"]
			newRobots.append(r)
		else:
			pass

	# Add brand new robots found nearby
	existingIds = {r["id"] for r in newRobots}
	for nId, nRobot in nearbyDict.items():
		if nId not in existingIds:
			if "R-" in nId:
				newRobots.append({
					"id": nRobot["id"],
					"intId": int(nRobot["id"].split("-")[-1]),
					"binbotId": None,
					"ip": None,
					"mac": nRobot["mac"],
					"status": "idle",
					"connected": False,
					"batt": 0,
					"lat": 53.5290, # Default map center
					"lng": -2.2629,
					"zoneId": None
				})

	# Only emit if the robot list has structurally changed
	newSummary = [(r["id"], r["status"], r["connected"]) for r in newRobots]
	oldSummary = [(r["id"], r["status"], r["connected"]) for r in state["robots"]]

	if newSummary != oldSummary:
		state["robots"] = newRobots
		socketio.emit("robot_update", state["robots"])

def onLoraUpdate(cmdName: str, nodeId: str, payload: dict) -> None:
	"""
	Callback triggered when a LoRa update is received from a robot
	Emits appropriate events to the frontend based on the command received
	"""

	if cmdName == "heartbeat":
		socketio.emit("heartbeat", (nodeId, payload))
	elif cmdName == "sendCameraIpAddress":
		socketio.emit("send_camera_ip_address", (nodeId, payload))
	elif cmdName == "addLitterMarker":
		socketio.emit("add_litter_marker", (nodeId, payload))
	else:
		print(f"Unknown command sent: {cmdName} | {payload}")

# ---------------------------------------------------------------------------- #
#                                    ROUTES                                    #
# ---------------------------------------------------------------------------- #

@app.route("/")
def index() -> str:
	""" Renders the main dashboard page """

	return render_template("index.html", version=__version__, PROTOCOL=PROTOCOL)

@app.errorhandler(404)
def pageNotFound(e: Exception) -> tuple[str, int]:
	""" Handles 404 errors by rendering a custom 404 page """

	return render_template("404.html"), 404

def loadLitterMarkers() -> list[dict]:
	""" Loads litter marker data from a local JSON file """

	dataFile = "litter_data.json"

	if exists(dataFile):
		try:
			with open(dataFile, "r") as file:
				return load(file)
		except (JSONDecodeError, IOError):
			pass

	return []

@socketio.on("connect")
def onConnect() -> None:
	""" Handles a new frontend connection - emits current robot and litter marker data to the newly connected client """

	emit("robot_update", state["robots"])
	emit("litter_update", loadLitterMarkers())

@socketio.on("pair_robot")
def pairRobot(data: dict) -> None:
	""" Initiates the pairing process for a robot selected from the frontend sidebar """

	targetRobotId = data["id"]
	targetRobotMac = data["mac"]

	# Mark as pairing in state and notify frontend
	for robot in state["robots"]:
		if robot["id"] == targetRobotId:
			robot["status"] = "pairing"
			break
	socketio.emit("robot_update", state["robots"])

	# Pair to the robot
	success = state["comms"].connectNewRobot(targetRobotMac, int(targetRobotId.split("-")[-1]), "password123321")

	if success:
		for r in state["robots"]:
			if r["id"] == targetRobotId:
				r["connected"] = True
				r["status"] = "online"
				r["batt"] = 100
				r["lat"] = 53.5290
				r["lng"] = -2.2629
				break
	else:
		# Set status to failed if unsuccessful
		for r in state["robots"]:
			if r["id"] == targetRobotId:
				r["status"] = "failed"
				break

	socketio.emit("robot_update", state["robots"])

@socketio.on("send_data")
def sendData(data: dict) -> None:
	""" Sends data to a robot via LoRa """

	cmdName = data.get("cmdName")
	payload = data.get("payload")
	nodeId = data.get("nodeId")

	# Done like this in order to have cmdId first in the keys - makes the debugging messages nicer
	# There is likely a better solution
	payload = { "cmdId": state["comms"].lora.getProtocolCmdId(cmdName), **payload }

	state["comms"].lora.transmit(cmdName, payload, nodeId)

@socketio.on("add_litter_marker")
def addLitterMarker(data: dict) -> None:
	""" Adds a new litter marker to the local JSON storage """

	# Path to the data file
	dataFile = "litter_data.json"

	# Load existing data or start fresh
	if exists(dataFile):
		try:
			with open(dataFile, "r") as file:
				litterMarkers = load(file)
		except (JSONDecodeError, IOError):
			litterMarkers = []
	else:
		litterMarkers = []

	# Add the new marker data
	litterMarkers.append(data)

	# Save back to file
	with open(dataFile, "w") as file:
		dump(litterMarkers, file, indent=4)

	print(f"Litter marker saved: {data.get('timestamp')} at {data.get('lat')}, {data.get('lng')}")

@socketio.on("get_gps_base")
def getGpsBase() -> dict:
	""" Retrieves GPS data from the base station """

	try:
		# Create a socket
		clientSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		clientSocket.settimeout(2.0) # 2 second timeout

		# Connect to the server
		clientSocket.connect((GPS_BASE_STATION_HOST, GPS_BASE_STATION_PORT))

		# Receive the data from the base station
		data = clientSocket.recv(1024)
		clientSocket.close()

		if not data:
			return { "error": "No data received" }

		# Data format: date time lat lng alt ...
		decodedData = data.decode("utf-8", errors="ignore").strip()
		parts = decodedData.split()

		if len(parts) >= 4:
			return {
				"lat": float(parts[2]),
				"lng": float(parts[3])
			}

		return { "error": f"Invalid data format: {decodedData}" }

	except socket.timeout:
		return { "error": "Connection timed out" }
	except ConnectionRefusedError:
		return { "error": "Connection refused - Check if the server is running" }
	except Exception as e:
		return { "error": f"An error occurred: {str(e)}"}

if __name__ == "__main__":
	if getenv("WERKZEUG_RUN_MAIN") == "true":
		state["comms"] = Comms()
		state["comms"].bt.setBtUpdateCallback(onBtUpdate)
		state["comms"].lora.setLoraCallback(onLoraUpdate)

	socketio.run(app, debug=True, use_reloader=True)
