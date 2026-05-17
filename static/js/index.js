/* -------------------------------------------------------------------------- */
/*                              GLOBAL VARIABLES                              */
/* -------------------------------------------------------------------------- */

const socket = io();

// const robots = [
// 	{ id: "R-104", status: "idle", connected: false, batt: 0, lat: 53.52999682543191, lng: -2.2593507826159829, zoneId: null, marker: null },
// 	{ id: "R-734", status: "idle", connected: false, batt: 0, lat: 53.53220795931625, lng: -2.2649145104813844, zoneId: null, marker: null },
// 	{ id: "R-885", status: "idle", connected: false, batt: 0, lat: 53.52786927789453, lng: -2.2626089407439194, zoneId: null, marker: null },
// 	{ id: "R-992", status: "idle", connected: false, batt: 0, lat: 53.53145812092611, lng: -2.2628335175292723, zoneId: null, marker: null },
// ];
let robots = [];

socket.on("robot_update", (data) => {
	// Sync robots array while preserving marker references
	const newRobots = data.map(updated => {
		const existing = robots.find(r => r.id === updated.id);
		return { ...updated, marker: existing ? existing.marker : null };
	});

	// Remove markers for robots no longer in the list
	robots.forEach(old => {
		if (old.marker && !data.find(n => n.id === old.id)) {
			map.removeLayer(old.marker);
		}
	});

	robots = newRobots;
	updateMapMarkers();
	renderSidebar();
});

let accentPrimary = "#00ffcc";
let accentSecondary = "#ffae00";
let accentDanger = "#ff3366";
let drawnItems;
let map;
let tileLayer;
let selectedZoneId = null;
let startLat = 53.52902931948096;
let startLng = -2.2629539937033964;
let teleopIndex = null;
let zones = [];

/* ----------------------------------- DOM ---------------------------------- */
const activeList = document.getElementById("activeList");
const inactiveList = document.getElementById("inactiveList");
const zoneDivider = document.getElementById("zoneDivider");
const listHeader = document.getElementById("listHeaderTitle");
const contextMenu = document.getElementById("contextMenu");
const themeToggleButton = document.getElementById("themeToggleButton");

/* -------------------------------------------------------------------------- */
/*                                    INIT                                    */
/* -------------------------------------------------------------------------- */
function init() {
	// Auto-Centering Option (Disabled)
	if (false && "geolocation" in navigator) {
		navigator.geolocation.getCurrentPosition((pos) => {
			// Successful callback
			startLat = pos.coords.latitude;
			startLng = pos.coords.longitude;
			initMap();
		}, () => {
			// Unsuccessful callback
			initMap();
		});
	} else {
		initMap();
	}

	renderSidebar();

	document.addEventListener("click", (e) => {
		contextMenu.style.display = "none";
	});

	setupTeleopArrowListeners();
	setupThemeToggle();
}

function setupThemeToggle() {
	if (!themeToggleButton) return;

	themeToggleButton.addEventListener("click", () => {
		document.body.classList.toggle("light-mode");
		const isLight = document.body.classList.contains("light-mode");

		// Update Map Tiles
		const newUrl = isLight
			? "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
			: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";

		tileLayer.setUrl(newUrl);

		// Update accent colors for JS-driven components
		accentPrimary = isLight ? "#006d56" : "#00ffcc";

		// Update existing markers and drawings
		updateMapMarkers();
		drawnItems.eachLayer(layer => {
			if (layer.setStyle) {
				layer.setStyle({ color: accentPrimary });
			}
		});

		// Refresh sidebar to update any color-dependent text
		renderSidebar();
	});
}

/* -------------------------------------------------------------------------- */
/*                                     MAP                                    */
/* -------------------------------------------------------------------------- */
function initMap() {
	map = L.map("map", {
		center: [startLat, startLng],
		zoom: 16,
		zoomControl: false
	});

	L.control.zoom({ position: "topright" }).addTo(map);

	tileLayer = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
		attribution: "&copy; OpenStreetMap &copy; CARTO",
		subdomains: "abcd",
		maxZoom: 19
	}).addTo(map);

	drawnItems = new L.FeatureGroup();
	map.addLayer(drawnItems);

	const drawControl = new L.Control.Draw({
		position: "topright",
		draw: {
			polygon: false,
			polyline: false,
			circle: false,
			marker: false,
			circlemarker: false,
			rectangle: {
				shapeOptions: {
					color: accentPrimary,
					fillOpacity: 0.1,
					weight: 2
				}
			}
		},
		edit: {
			featureGroup: drawnItems,
			remove: true
		}
	});
	map.addControl(drawControl);

	setTimeout(() => {
		const drawRectButton = document.querySelector(".leaflet-draw-draw-rectangle");

		if (drawRectButton) {
			drawRectButton.title = "Click to Draw / Dbl-Click for Precision";
			drawRectButton.addEventListener("dblclick", (e) => {
				e.stopPropagation();
				e.preventDefault();
				openPrecisionZoneModal();
			});
		}
	}, 500);

	map.on(L.Draw.Event.CREATED, function (e) {
		createZoneFromLayer(e.layer);
	});

	map.on(L.Draw.Event.DELETED, function(e) {
		if (selectedZoneId) deselectZone();

		e.layers.eachLayer(layer => {
			robots.forEach(r => {
				if (r.zoneId === layer.zoneId) {
					r.zoneId = null;
					r.status = "idle";
				}
			});
		});

		updateMapMarkers();
		renderSidebar();
	});

	map.on("click", (e) => {
		if (
			e.originalEvent.target.classList.contains("leaflet-container") &&
			selectedZoneId !== null
		) { deselectZone(); }
	});

	const mapDiv = document.getElementById("map");
	const resizeObserver = new ResizeObserver(() => {
		map.invalidateSize({ pan: true });
	});
	resizeObserver.observe(mapDiv);

	updateMapMarkers();
}

/* ---------------------------------- ZONES --------------------------------- */
function createZoneFromLayer(layer) {
	const id = "ZONE-" + (zones.length + 1);

	layer.zoneId = id;
	layer.on("click", (e) => {
		L.DomEvent.stopPropagation(e);
		selectZone(id, layer);
	});
	drawnItems.addLayer(layer);
	zones.push({ id: id, layer: layer });

	selectZone(id, layer);
}

function updateMapMarkers() {
	robots.forEach(r => {
		let color = "#555";

		if (r.connected) {
			if (r.status === "working") color = accentSecondary // Amber;
			else if (r.status === "manual") color = accentDanger // Red;
			else color = accentPrimary // Cyan;
		}

		if (r.marker) {
			r.marker.setLatLng([r.lat, r.lng]);
			r.marker.setStyle({ color: color, fillColor: color });
		} else {
			r.marker = L.circleMarker([r.lat, r.lng], {
				color: color,
				fillColor: color,
				fillOpacity: 0.8,
				radius: 6,
				weight: 2
			}).addTo(map);

			r.marker.bindTooltip(r.id, { permanent: true, direction: "right" });

			r.marker.on("dblclick", () => {
				const idx = robots.findIndex(item => item.id === r.id);
				openTeleop(idx);
			});
		}
	});
}

function selectZone(id, layer) {
	selectedZoneId = id;
	listHeader.innerText = `ACTIVE: ${id}`;
	listHeader.style.color = "var(--accentPrimary)";

	const isLight = document.body.classList.contains("light-mode");
	const selectionColor = isLight ? "#000" : "#fff";

	drawnItems.eachLayer(l => l.setStyle({ color: accentPrimary, dashArray: null }));
	layer.setStyle({ color: selectionColor, dashArray: "5, 5" });
	layer.bindPopup(`
		<div style="font-family: monospace;">
			<strong>${id}</strong><br>
			<button onclick="startZoneOps('${id}')"
				style="margin-top:5px; background:var(--accentPrimary); border:none; padding:5px 10px; cursor:pointer; font-weight:bold;">
				START OPS
			</button>
		</div>
	`).openPopup();

	renderSidebar();
}

function deselectZone() {
	selectedZoneId = null;
	listHeader.innerText = "NO ACTIVE ZONE SELECTED";
	listHeader.style.color = "var(--textMuted)";

	drawnItems.eachLayer(l => l.setStyle({ color: accentPrimary, dashArray: null }));

	renderSidebar();
	map.closePopup();
}

/* ----------------------------- PRECISION ZONES ---------------------------- */
function openPrecisionZoneModal() {
	document.getElementById("precisionZoneOverlay").style.display = "flex";
}

function closePrecisionZoneModal() {
	document.getElementById("precisionZoneOverlay").style.display = "none";
}

function switchPrecisionZoneTab(tabId) {
	document.querySelectorAll(".precisionZoneTabContent").forEach(el => el.classList.remove("active"));
	document.querySelectorAll(".tabButton").forEach(el => el.classList.remove("active"));
	document.getElementById(tabId).classList.add("active"); event.target.classList.add("active");
}

function areValidCoords(lat, lng) {
	return (
		lat >= -90 && lat <= 90 &&
		lng >= -180 && lng <= 180
	);
}

function createPrecisionZone() {
	let bounds;
	const activeTab = document.querySelector(".precisionZoneTabContent.active").id;

	if (activeTab === "precisionZoneTab1") {
		const topLeftLat = parseFloat(document.getElementById("topLeftLat").value);
		const topLeftLng = parseFloat(document.getElementById("topLeftLng").value);
		const bottomRightLat = parseFloat(document.getElementById("bottomRightLat").value);
		const bottomRightLng = parseFloat(document.getElementById("bottomRightLng").value);

		bounds = [[topLeftLat, topLeftLng], [bottomRightLat, bottomRightLng]];

		if (!areValidCoords(bounds[0][0], bounds[0][1]) || !areValidCoords(bounds[1][0], bounds[1][1])) {
			return alert("Invalid Coordinates - Please ensure:\n - latitude values are between -90 and 90\n - longitude values are between -180 and 180");
		}
	} else if (activeTab === "precisionZoneTab2") {
		const lat = parseFloat(document.getElementById("anchorLat").value);
		const lng = parseFloat(document.getElementById("anchorLng").value);
		const w = parseFloat(document.getElementById("dimWidth").value);
		const h = parseFloat(document.getElementById("dimHeight").value);

		const latDelta = h / 111111; // 111111 = ~1 degree of latitude
		const lngDelta = w / (111111 * Math.cos(lat * Math.PI / 180));

		bounds = [[lat, lng], [lat + latDelta, lng + lngDelta]];

		if (!areValidCoords(bounds[0][0], bounds[0][1]) || !areValidCoords(bounds[1][0], bounds[1][1])) {
			return alert("Invalid Coordinates - Please ensure:\n - latitude values are between -90 and 90\n - longitude values are between -180 and 180\n - height and width values are normal");
		}
	} else if (activeTab === "precisionZoneTab3") {
		if ("geolocation" in navigator) {
			navigator.geolocation.getCurrentPosition((pos) => {
				const lat = pos.coords.latitude;
				const lng = pos.coords.longitude;
				const w = parseFloat(document.getElementById("meWidth").value);
				const h = parseFloat(document.getElementById("meHeight").value);
				const latDelta = (h / 2) / 111111; // 111111 = ~1 degree of latitude
				const lngDelta = (w / 2) / (111111 * Math.cos(lat * Math.PI / 180));

				bounds = [[lat + latDelta, lng - lngDelta], [lat - latDelta, lng + lngDelta]];

				if (!areValidCoords(bounds[0][0], bounds[0][1]) || !areValidCoords(bounds[1][0], bounds[1][1])) {
					return alert("Invalid Coordinates - Please ensure:\n - height and width values are normal");
				}

				finaliseZoneCreation(bounds);
			});

			return;
		}
	}

	finaliseZoneCreation(bounds);
}

function finaliseZoneCreation(bounds) {
	const rect = L.rectangle(bounds, { color: accentPrimary, fillOpacity: 0.1, weight: 2 });

	createZoneFromLayer(rect);
	map.fitBounds(bounds);
	closePrecisionZoneModal();
}

/* -------------------------------------------------------------------------- */
/*                                   SIDEBAR                                  */
/* -------------------------------------------------------------------------- */
function renderSidebar() {
	const hasActiveZone = !!selectedZoneId;
	zoneDivider.style.display = hasActiveZone ? "block" : "none";

	// Keep track of which robots were rendered so we can remove stale ones
	const renderedIds = new Set();

	robots.forEach((r, index) => {
		const isActive = (selectedZoneId && r.zoneId === selectedZoneId);
		const targetList = isActive ? activeList : inactiveList;
		const otherList = isActive ? inactiveList : activeList;

		let card = document.getElementById(`card-${r.id}`);

		if (!card) {
			card = createRobotCard(r, index, isActive);
			card.id = `card-${r.id}`;
			targetList.appendChild(card);
		} else {
			// Update existing card
			updateRobotCard(card, r, index, isActive);
			// Move to correct list if it changed
			if (!targetList.contains(card)) {
				otherList.removeChild(card);
				targetList.appendChild(card);
			}
		}
		renderedIds.add(`card-${r.id}`);
	});

	// Remove cards for robots no longer in the list
	[activeList, inactiveList].forEach(list => {
		Array.from(list.children).forEach(card => {
			if (card.id.startsWith("card-") && !renderedIds.has(card.id)) {
				card.classList.add("removing");

				card.addEventListener("animationend", () => {
					list.removeChild(card);
				}, { once: true });
			}
		});
	});

	if (hasActiveZone && activeList.children.length === 0) {
		if (!activeList.querySelector(".emptyStateBox")) {
			const emptyBox = document.createElement("div");
			emptyBox.className = "emptyStateBox";
			emptyBox.innerText = `Currently no platforms assigned to ${selectedZoneId}`;
			activeList.appendChild(emptyBox);
		}
	} else {
		const emptyBox = activeList.querySelector(".emptyStateBox");
		if (emptyBox) activeList.removeChild(emptyBox);
	}
}

function updateRobotCard(card, r, index, isActive) {
	// Only update classes if they changed to avoid re-triggering animations/reflows
	const connectedClass = r.connected ? "connected" : "unpaired";
	const highlightClass = isActive ? "highlighted" : "";
	const newClassName = `robotCard ${connectedClass} ${highlightClass}`;

	if (card.className !== newClassName) {
		card.className = newClassName;
	}

	let statusText = "Disconnected";
	let dotClass = "";

	if (!r.connected) {
		if (r.status === "pairing") { statusText = "Pairing..."; dotClass = "pairing"; }
		else if (r.status === "failed") { statusText = "Unsuccessful"; dotClass = "manual"; }
	} else {
		statusText = "Online"; dotClass = "connected";
		if (r.status === "working") { statusText = "Working"; dotClass = "working"; }
		if (r.status === "manual") { statusText = "Manual"; dotClass = "manual"; }
	}

	const latStr = r.lat.toFixed(5);
	const lngStr = r.lng.toFixed(5);

	// Update only the necessary parts of the card
	const statusDot = card.querySelector(".dot");
	if (statusDot.className !== `dot ${dotClass}`) statusDot.className = `dot ${dotClass}`;

	const statusSpan = card.querySelector(".connectionStatus span");
	if (statusSpan.innerText !== statusText) statusSpan.innerText = statusText;

	const connectBtn = card.querySelector(".actionButton");
	if (connectBtn) {
		const shouldShow = !r.connected && r.status !== "pairing";
		connectBtn.style.display = shouldShow ? "block" : "none";
	}

	const statVals = card.querySelectorAll(".statVal");
	if (statVals[0].innerText !== `${r.batt}%`) statVals[0].innerText = `${r.batt}%`;
	if (statVals[1].innerText !== `${latStr}, ${lngStr}`) statVals[1].innerText = `${latStr}, ${lngStr}`;

	const taskVal = statVals[2];
	const taskText = r.zoneId ? r.zoneId : "IDLE";
	if (taskVal.innerText !== taskText) {
		taskVal.innerText = taskText;
		taskVal.style.color = r.zoneId ? "var(--accentPrimary)" : "";
	}
}

function createRobotCard(r, index, isActive) {
	const card = document.createElement("div");
	card.className = `robotCard ${r.connected ? "connected" : "unpaired"} ${isActive ? "highlighted" : ""}`;

	card.addEventListener("dblclick", () => openTeleop(index));

	card.addEventListener("contextmenu", (e) => {
		e.preventDefault();
		showContextMenu(e.pageX, e.pageY, index);
	});

	let statusText = "Disconnected";
	let dotClass = "";

	if (!r.connected) {
		if (r.status === "pairing") { statusText = "Pairing..."; dotClass = "pairing"; }
		else if (r.status === "failed") { statusText = "Unsuccessful"; dotClass = "manual"; }
	} else {
		statusText = "Online"; dotClass = "connected";
		if (r.status === "working") { statusText = "Working"; dotClass = "working"; }
		if (r.status === "manual") { statusText = "Manual"; dotClass = "manual"; }
	}

	const latStr = r.lat.toFixed(5);
	const lngStr = r.lng.toFixed(5);

	card.innerHTML = `
		<div class="cardTop">
			<span class="robotId">${r.id}</span>
			<div class="connectionStatus">
				<div class="dot ${dotClass}"></div>
				<span>${statusText}</span>
			</div>
		</div>
		<button class="actionButton" onclick="pairRobot(${index})" style="display: ${!r.connected && r.status !== "pairing" ? "block" : "none"}">Connect</button>
		<div class="cardStats">
			<div class="statRow"><span>BATTERY</span> <span class="statVal">${r.batt}%</span></div>
			<div class="statRow"><span>LOCATION</span> <span class="statVal">${latStr}, ${lngStr}</span></div>
			<div class="statRow"><span>TASK</span> <span class="statVal" style="color:${r.zoneId ? "var(--accentPrimary)" : ""}">${r.zoneId ? r.zoneId : "IDLE"}</span></div>
			<div class="statRow">
				<span>BINBOT</span>
				<select class="binBotSelect" onchange="assignBinBot(${index}, this.value)" onclick="event.stopPropagation()">
					<option value="">None</option>
					${robots
						.filter((other, i) => i !== index && other.connected)
						.map(other => `<option value="${other.id}" ${r.binbotId === other.id ? "selected" : ""}>${other.id}</option>`)
						.join("")
					}
				</select>
			</div>
		</div>
	`;

	return card;
}

function pairRobot(index) {
	const r = robots[index];
	r.status = "pairing";

	renderSidebar();

	socket.emit("pair_robot", { id: r.id, mac: r.mac });
};

function startZoneOps(zId) {
	let count = 0;
	const targetZone = zones.find(z => z.id === zId);

	if (targetZone) {
		const bounds = targetZone.layer.getBounds();
		const points = targetZone.layer.getLatLngs()[0];

		robots.forEach(r => {
			if (r.connected && bounds.contains(L.latLng(r.lat, r.lng))) {
				r.status = "working";
				r.zoneId = zId;
				count++;

				socket.emit("send_data", {
					cmdName: "areaCoords",
					payload: {
						"topLeftLatitude": points[1].lat, // Top-Left Lat
						"topLeftLongitude": points[1].lng, // Top-Left Lng
						"bottomRightLatitude": points[3].lat, // Bottom-Right Lat
						"bottomRightLongitude": points[3].lng  // Bottom-Right Lng
					},
					nodeId: r.intId
				});
			}
		});
	}

	updateMapMarkers();
	renderSidebar();
	map.closePopup();
}

function showContextMenu(x, y, robotIndex) {
	const r = robots[robotIndex];

	contextMenu.style.left = `${x}px`;
	contextMenu.style.top = `${y}px`;
	contextMenu.style.display = "block";
	contextMenu.innerHTML = "";

	if (!r.connected) {
		contextMenu.innerHTML += `<div class="ctxItem" onclick="pairRobot(${robotIndex})">Connect</div>`;
	} else {
		contextMenu.innerHTML += `<div class="ctxItem" onclick="disconnectRobot(${robotIndex})">Power Off</div>`;

		if (r.status === "manual") {
			contextMenu.innerHTML += `<div class="ctxItem" onclick="resumeAutoContext(${robotIndex})">Manual</div>`;
		} else {
			contextMenu.innerHTML += `<div class="ctxItem" onclick="pauseRobot(${robotIndex})">Pause</div>`;
		}
	}

	contextMenu.innerHTML += `<div class="ctxDivider"></div>`;

	if (r.connected) {
		contextMenu.innerHTML += `<div class="ctxItem" onclick="openTeleop(${robotIndex})">Remote Control</div>`;
	}

	if (selectedZoneId && r.zoneId !== selectedZoneId) {
		contextMenu.innerHTML += `<div class="ctxItem" onclick="assignRobot(${robotIndex}, '${selectedZoneId}')">Assign to ${selectedZoneId}</div>`;
	}

	if (r.zoneId) {
		contextMenu.innerHTML += `<div class="ctxItem" onclick="unassignRobot(${robotIndex})">Deselect from Zone</div>`;
	}
}

function pauseRobot(index) {
	robots[index].status = "idle";

	updateMapMarkers();
	renderSidebar();
}

function resumeAutoContext(index) {
	robots[index].status = robots[index].zoneId ? "working" : "online";

	updateMapMarkers();
	renderSidebar();
}

function disconnectRobot(index) {
	const disconnectedRobotId = robots[index].id;

	robots[index].connected = false;
	robots[index].status = "idle";
	robots[index].zoneId = null;
	robots[index].binbotId = null;

	// Clear this robot from being anyone else's BinBot
	robots.forEach(r => {
		if (r.binbotId === disconnectedRobotId) {
			r.binbotId = null;
		}
	});

	updateMapMarkers();
	renderSidebar();
}

function assignRobot(index, zoneId) {
	const selectedZone = zones.find(zone => zone.id === zoneId);
	robots[index].zoneId = zoneId;

	if (selectedZone) {
		const center = selectedZone.layer.getBounds().getCenter();

		robots[index].lat = center.lat; robots[index].lng = center.lng;

		updateMapMarkers();
	}

	renderSidebar();
}

function unassignRobot(index) {
	robots[index].zoneId = null;
	robots[index].status = "idle";

	renderSidebar();
}

function assignBinBot(index, binBotId) {
	const robot = robots[index];
	const oldBinBotId = robot.binBotId;

	robot.binBotId = binBotId || null;

	if (oldBinBotId !== robot.binBotId) {
		socket.emit("send_data", {
			cmdName: "assignBinbot",
			payload: { "binbotNodeId": robot.binBotId },
			nodeId: robot.id
		});
	}

	renderSidebar();
}

/* -------------------------------------------------------------------------- */
/*                                   TELEOP                                   */
/* -------------------------------------------------------------------------- */
function openTeleop(index) {
	const r = robots[index];

	if (!r.connected) {
		alert("Cannot manually control disconnected unit");
		return;
	}

	teleopIndex = index;
	document.getElementById("teleopRobotID").innerText = `Unit: ${r.id}`;
	updateTeleopStats();
	document.getElementById("teleopOverlay").style.display = "flex";
}

function closeTeleop() {
	document.getElementById("teleopOverlay").style.display = "none";
	teleopIndex = null;
}

function updateTeleopStats() {
	if (teleopIndex === null) return;

	const r = robots[teleopIndex];
	document.getElementById("teleBatt").innerText = `${r.batt}%`;
	document.getElementById("teleLoc").innerText = `${r.lat.toFixed(4)}, ${r.lng.toFixed(4)}`;

	const statusEl = document.getElementById("teleStatus");
	statusEl.innerText = r.status.toUpperCase();
	statusEl.style.color = r.status === "manual" ? "var(--accentDanger)" : "var(--accentPrimary)";
}

function setupTeleopArrowListeners() {
	document.addEventListener("keydown", (e) => {
		if (teleopIndex === null) return;

		const key = e.key;

		// Prevent scrolling for arrows
		if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(key)) {
			e.preventDefault();
		}

		const el = document.getElementById("k-" + key);

		if (el) {
			el.classList.add("active");
			console.log(`Sending Command: ${key} to Robot ${robots[teleopIndex].id}`);

			// Map arrows to a single 1-byte character
			const keyMap = { "ArrowUp": "U", "ArrowDown": "D", "ArrowLeft": "L", "ArrowRight": "R" };
			const direction = keyMap[key];

			socket.emit("send_data", {
				cmdName: "movement",
				payload: { "direction": direction },
				nodeId: robots[teleopIndex].intId
			});

			if (robots[teleopIndex].status !== "manual") {
				robots[teleopIndex].status = "manual";

				updateMapMarkers();
				renderSidebar();
				updateTeleopStats();
			}
		}
	});

	document.addEventListener("keyup", (e) => {
		if (teleopIndex === null) return;

		const el = document.getElementById("k-" + e.key);
		if (el) el.classList.remove("active");
	});
}

{
	// Close teleop modal
	document.getElementById("closeTeleopButton").addEventListener("click", () => {
		closeTeleop();
	});

	// Teleop controls
	document.getElementById("teleopResumeAutoButton").addEventListener("click", () => {
		resumeAuto();
	});

	document.getElementById("teleopToggleArmButton").addEventListener("click", () => {
		toggleArm();
	});

	document.getElementById("teleopSoundBeeperButton").addEventListener("click", () => {
		soundBeeper();
	});

	// Close precision zone modal
	document.getElementById("closePrecisionZoneModal").addEventListener("click", () => {
		closePrecisionZoneModal();
	});

	// Precision zone tab selectors
	document.getElementById("precisionZoneTab1Selector").addEventListener("click", () => {
		switchPrecisionZoneTab("precisionZoneTab1");
	});

	document.getElementById("precisionZoneTab2Selector").addEventListener("click", () => {
		switchPrecisionZoneTab("precisionZoneTab2");
	});

	document.getElementById("precisionZoneTab3Selector").addEventListener("click", () => {
		switchPrecisionZoneTab("precisionZoneTab3");
	});

	// Precision zone actions
	document.getElementById("createPrecisionZoneButton").addEventListener("click", () => {
		createPrecisionZone();
	});
}

function resumeAuto() {
	if (teleopIndex !== null && robots[teleopIndex].status == "manual") {
		robots[teleopIndex].status = robots[teleopIndex].zoneId ? "working" : "online";
		console.log(`Resuming Auto-Navigation for ${robots[teleopIndex].id}`);

		updateMapMarkers();
		renderSidebar();
		updateTeleopStats();
	}
}

function toggleArm() {
	console.log(`Toggling Manipulator Arm for ${robots[teleopIndex].id}`);
}

function soundBeeper() {
	console.log(`Beep Sent to ${robots[teleopIndex].id}`);
}

init();
