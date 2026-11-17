extends Node2D
## Simulation RANTERA : véhicule sur un itinéraire GPS, injection d'accidents, envoi de la télémétrie.
## Commandes : ↑/↓ vitesse cible · 1 frontal · 2 latéral · 3 arrière · 4 tonneau · 5 freinage brutal
##             6 nid-de-poule · G perte/retour GPS · R réinitialiser
## Mode automatique (vérification sans interface) : variable d'environnement RANTERA_AUTORUN=frontal

const RouteData = preload("res://scripts/route.gd")
const VehicleSim = preload("res://scripts/vehicle_sim.gd")
const TelemetryClient = preload("res://scripts/telemetry_client.gd")

const PX_PER_M := 1.5
const KEY_SCENARIOS := {
	KEY_1: "frontal", KEY_2: "lateral", KEY_3: "rear",
	KEY_4: "rollover", KEY_5: "hard_brake", KEY_6: "pothole",
}

var settings: Dictionary = {
	"api_url": "http://127.0.0.1:8000",
	"api_key": "rantera-dev-key",
	"vehicle_id": "GODOT-001",
	"source": "simulated",
}
var route: RouteData
var vehicle: VehicleSim
var client: TelemetryClient
var cam: Camera2D
var hud: Label
var message: String = ""
var message_time: float = 0.0
var incidents: Array = []        # incidents reçus de l'API
var markers: Array = []          # positions (px) des incidents sur la carte
var autorun: String = ""
var elapsed: float = 0.0
var autorun_triggered: bool = false


func _ready() -> void:
	_load_settings()
	route = RouteData.new()
	if not route.load_from("res://data/route.json"):
		_show("Itinéraire invalide : voir la console")
		return
	vehicle = VehicleSim.new(route)
	client = TelemetryClient.new()
	client.api_url = String(settings["api_url"])
	client.api_key = String(settings["api_key"])
	client.vehicle_id = String(settings["vehicle_id"])
	client.source = String(settings["source"])
	add_child(client)
	client.incident_received.connect(_on_incident)
	client.connection_changed.connect(_on_connection)
	cam = Camera2D.new()
	add_child(cam)
	cam.make_current()
	RenderingServer.set_default_clear_color(Color(0.11, 0.14, 0.17))
	var layer := CanvasLayer.new()
	add_child(layer)
	hud = Label.new()
	hud.position = Vector2(14, 10)
	hud.add_theme_font_size_override("font_size", 16)
	hud.add_theme_color_override("font_outline_color", Color.BLACK)
	hud.add_theme_constant_override("outline_size", 5)
	layer.add_child(hud)
	autorun = OS.get_environment("RANTERA_AUTORUN")
	vehicle.target_kmh = 80.0 if autorun != "" else 50.0
	print("[RANTERA] API : ", client.api_url, " · véhicule : ", client.vehicle_id)


func _load_settings() -> void:
	var f := FileAccess.open("res://config.json", FileAccess.READ)
	if f != null:
		var data = JSON.parse_string(f.get_as_text())
		if typeof(data) == TYPE_DICTIONARY:
			for k in data.keys():
				settings[k] = data[k]
	var env_url := OS.get_environment("RANTERA_API_URL")
	var env_key := OS.get_environment("RANTERA_API_KEY")
	if env_url != "":
		settings["api_url"] = env_url
	if env_key != "":
		settings["api_key"] = env_key


func _show(text: String) -> void:
	message = text
	message_time = 6.0
	print("[RANTERA] ", text)


func _on_incident(inc: Dictionary) -> void:
	incidents.append(inc)
	markers.append(Vector2(vehicle.last_pos["x"], -vehicle.last_pos["y"]) * PX_PER_M)
	var sev: String = String(inc.get("severity", "?"))
	_show("%s détecté : %s — gravité %s (%s)" % [
		"Accident" if inc.get("kind", "") == "accident" else "Événement",
		inc.get("type", "?"), sev, inc.get("incident_id", "?")])


func _on_connection(online: bool) -> void:
	_show("Connexion API rétablie" if online else "API injoignable : télémétrie mise en tampon")


func _unhandled_input(event: InputEvent) -> void:
	var key := event as InputEventKey
	if vehicle == null or key == null or not key.pressed or key.echo:
		return
	if KEY_SCENARIOS.has(key.keycode):
		_trigger(KEY_SCENARIOS[key.keycode])
	elif key.keycode == KEY_G:
		vehicle.gps_lost = not vehicle.gps_lost
		_show("GPS perdu" if vehicle.gps_lost else "GPS rétabli")
	elif key.keycode == KEY_R:
		vehicle.reset()
		client.label = ""
		_show("Simulation réinitialisée")


func _trigger(kind: String) -> void:
	var err: String = vehicle.start_event(kind)
	if err != "":
		_show(err)
		return
	client.label = kind
	_show("Scénario lancé : " + kind)


func _physics_process(delta: float) -> void:
	if vehicle == null:
		return
	elapsed += delta
	if Input.is_key_pressed(KEY_UP):
		vehicle.target_kmh = minf(130.0, vehicle.target_kmh + 25.0 * delta)
	if Input.is_key_pressed(KEY_DOWN):
		vehicle.target_kmh = maxf(0.0, vehicle.target_kmh - 25.0 * delta)
	vehicle.step(delta)
	client.push_sample(vehicle.sample())
	cam.position = Vector2(vehicle.last_pos["x"], -vehicle.last_pos["y"]) * PX_PER_M
	if message_time > 0.0:
		message_time -= delta
	_autorun_step()
	_update_hud()
	queue_redraw()


func _autorun_step() -> void:
	if autorun == "":
		return
	if not autorun_triggered and elapsed >= 8.0:
		autorun_triggered = true
		_trigger(autorun)
	if elapsed >= 22.0:
		print("[RANTERA] AUTORUN terminé : %d incident(s) reçu(s), file=%d, en ligne=%s" % [incidents.size(), client.queue_size(), str(client.online)])
		for inc in incidents:
			print("[RANTERA]   ", inc)
		get_tree().quit(0 if (incidents.size() > 0 or autorun in ["pothole", "cruise"]) else 2)


func _update_hud() -> void:
	var gps: String = "PERDU"
	if not vehicle.gps_lost:
		gps = "%.5f, %.5f" % [vehicle.last_pos["lat"], vehicle.last_pos["lon"]]
	var net: String = "connecté" if client.online else "HORS LIGNE"
	var text: String = "Vitesse : %3.0f km/h (cible %3.0f)   GPS : %s\n" % [vehicle.speed_kmh(), vehicle.target_kmh, gps]
	text += "API : %s · lots en attente : %d · perdus : %d\n" % [net, client.queue_size(), client.dropped]
	if client.last_error != "" and not client.online:
		text += "Erreur : " + client.last_error + "\n"
	if vehicle.event != null:
		text += "Événement en cours : " + vehicle.event.kind + "\n"
	elif vehicle.crashed:
		text += "Véhicule immobilisé (R pour réinitialiser)\n"
	if incidents.size() > 0:
		var last: Dictionary = incidents[incidents.size() - 1]
		text += "Dernier incident : %s — gravité %s\n" % [last.get("type", "?"), last.get("severity", "?")]
	if message_time > 0.0:
		text += ">> " + message + "\n"
	text += "\n↑↓ vitesse · 1 frontal · 2 latéral · 3 arrière · 4 tonneau · 5 freinage · 6 nid-de-poule · G GPS · R reset"
	hud.text = text


func _draw() -> void:
	if route == null:
		return
	var pts: PackedVector2Array = PackedVector2Array()
	for p in route.xy:
		pts.append(Vector2(p.x, -p.y) * PX_PER_M)
	draw_polyline(pts, Color(0.30, 0.33, 0.37), 22.0, true)
	draw_polyline(pts, Color(0.75, 0.78, 0.80), 1.5, true)
	for m in markers:
		draw_circle(m, 12.0, Color(0.85, 0.2, 0.2, 0.9))
	if vehicle == null:
		return
	var pos: Vector2 = Vector2(vehicle.last_pos["x"], -vehicle.last_pos["y"]) * PX_PER_M
	draw_set_transform(pos, deg_to_rad(float(vehicle.last_pos["heading"])), Vector2.ONE)
	var col: Color = Color(0.95, 0.35, 0.3) if (vehicle.crashed or vehicle.event != null) else Color(0.35, 0.7, 0.95)
	draw_rect(Rect2(-6, -12, 12, 24), col)
	draw_rect(Rect2(-4, -10, 8, 6), Color(0.1, 0.12, 0.15))
	draw_set_transform(Vector2.ZERO, 0.0, Vector2.ONE)
