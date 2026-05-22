import logging
from comms import Comms, PROTOCOL
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
from os import getenv

__version__ = 1.0

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
	# "robots": [
	# 	{ "id": "R-007", "status": "idle", "connected": True, "batt": 42, "lat": 53.52999682543191, "lng": -2.2593507826159829, "zoneId": None, "marker": None },
	# 	{ "id": "R-067", "status": "idle", "connected": False, "batt": 87, "lat": 53.53220795931625, "lng": -2.2649145104813844, "zoneId": None, "marker": None },
	# ]
}

def on_bt_update(nearby_robots):
	nearby_dict = { n["id"]: n for n in nearby_robots }
	new_robots = []

	# Process existing robots
	for r in state["robots"]:
		# Keep connected or pairing robots regardless of proximity
		if r.get("connected") or r.get("status") == "pairing":
			new_robots.append(r)
		# Keep idle or failed robots ONLY if they are still nearby
		elif r["id"] in nearby_dict:
			# Update MAC if it changed, but keep status (idle / failed)
			r["mac"] = nearby_dict[r["id"]]["mac"]
			new_robots.append(r)
		else:
			pass

	# Add brand new robots found nearby
	existing_ids = {r["id"] for r in new_robots}
	for nid, nrobot in nearby_dict.items():
		if nid not in existing_ids:
			if "R-" in nid:
				new_robots.append({
					"id": nrobot["id"],
					"intId": int(nrobot["id"].split("-")[-1]),
					"binbotId": None,
					"ip": None,
					"mac": nrobot["mac"],
					"status": "idle",
					"connected": False,
					"batt": 0,
					"lat": 53.5290, # Default map center
					"lng": -2.2629,
					"zoneId": None
				})

	# Only emit if the robot list has structurally changed
	new_summary = [(r["id"], r["status"], r["connected"]) for r in new_robots]
	old_summary = [(r["id"], r["status"], r["connected"]) for r in state["robots"]]

	if new_summary != old_summary:
		state["robots"] = new_robots
		socketio.emit("robot_update", state["robots"])

def on_lora_update(cmdName, nodeId, payload):
	if cmdName == "heartbeat":
		socketio.emit("heartbeat", (nodeId, payload))
	elif cmdName == "sendCameraIpAddress":
		socketio.emit("send_camera_ip_address", (nodeId, payload))
	else:
		print(f"Unknown command sent: {cmdName} | {payload}")

# ---------------------------------------------------------------------------- #
#                                    ROUTES                                    #
# ---------------------------------------------------------------------------- #

@app.route("/")
def index():
	return render_template("index.html", version=__version__, PROTOCOL=PROTOCOL)

@socketio.on("connect")
def on_connect():
	emit("robot_update", state["robots"])

@socketio.on("pair_robot")
def pair_robot(data):
	targetRobotId = data["id"]
	targetRobotMac = data["mac"]

	# Mark as pairing in state and notify frontend
	for robot in state["robots"]:
		if robot["id"] == targetRobotId:
			robot["status"] = "pairing"
			break
	socketio.emit("robot_update", state["robots"])

	# Pair to the robot
	success = state["comms"].connect_new_robot(targetRobotMac, int(targetRobotId.split("-")[-1]), "password123321")

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
def send_data(data):
	cmdName = data.get("cmdName")
	payload = data.get("payload")
	nodeId = data.get("nodeId")

	# Done like this in order to have cmdId first in the keys - makes the debugging messages nicer
	# There is likely a better solution
	payload = { "cmdId": state["comms"].lora.getProtocolCmdId(cmdName), **payload }

	state["comms"].lora.transmit(cmdName, payload, nodeId)

@app.errorhandler(404)
def pageNotFound(e):
	return render_template("404.html"), 404

if __name__ == "__main__":
	if getenv("WERKZEUG_RUN_MAIN") == "true":
		state["comms"] = Comms()
		state["comms"].bt.set_bt_update_callback(on_bt_update)
		state["comms"].lora.set_lora_callback(on_lora_update)

	socketio.run(app, debug=True, use_reloader=True)
