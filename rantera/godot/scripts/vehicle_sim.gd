extends RefCounted
## Véhicule simulé : suit l'itinéraire, régulateur de vitesse, événements, génération de télémétrie.

const RouteData = preload("res://scripts/route.gd")
const CrashEvent = preload("res://scripts/crash_event.gd")
const G := 9.81

var route: RouteData
var s: float = 0.0              # abscisse curviligne (m)
var speed_ms: float = 13.9
var target_kmh: float = 50.0
var event: CrashEvent = null
var gps_lost: bool = false
var clock: float = 0.0          # temps Unix simulé (s), espacement exact entre échantillons
var last_pos: Dictionary = {}
var crashed: bool = false


func _init(p_route: RouteData) -> void:
	route = p_route
	clock = Time.get_unix_time_from_system()
	last_pos = route.position_at(0.0)


func reset() -> void:
	s = 0.0
	speed_ms = 13.9
	target_kmh = 50.0
	event = null
	crashed = false
	last_pos = route.position_at(0.0)


func speed_kmh() -> float:
	return speed_ms * 3.6


## Lance un événement. Retourne "" si accepté, sinon un message d'erreur.
func start_event(kind: String) -> String:
	if event != null or crashed:
		return "Un événement est déjà en cours (touche R pour réinitialiser)"
	var v0: float = speed_kmh()
	var dv: float = 0.0
	match kind:
		"frontal":
			if v0 < 20.0:
				return "Vitesse insuffisante : accélérez à plus de 20 km/h (flèche haut)"
			dv = randf_range(8.0, maxf(9.0, v0 * 0.95))
		"lateral":
			dv = randf_range(8.0, 55.0)
		"rear":
			dv = randf_range(8.0, 50.0)
		"rollover":
			dv = randf_range(15.0, 50.0)
		"hard_brake":
			if v0 < 70.0:
				return "Freinage brutal : roulez à plus de 70 km/h"
		"pothole":
			pass
		_:
			return "Scénario inconnu : " + kind
	event = CrashEvent.new(kind, dv)
	return ""


func step(dt: float) -> void:
	clock += dt
	var ax: float = 0.0
	var ay: float = 0.0
	var az: float = 1.0
	var roll: float = 0.0
	var yaw: float = 0.0
	if crashed:
		# Véhicule immobilisé après un accident : il ne repart pas, le roulis final est conservé.
		speed_ms = 0.0
		roll = _last_roll
	elif event == null:
		var a: float = clampf((target_kmh / 3.6 - speed_ms) * 0.8, -3.0, 2.5)
		speed_ms = maxf(0.0, speed_ms + a * dt)
		ax = clampf(a / G, -0.3, 0.3)
	else:
		var out: Dictionary = event.step(dt, speed_ms)
		speed_ms = out["speed"]
		ax = out["ax"]
		ay = out["ay"]
		az = out["az"]
		roll = out["roll"]
		yaw = out["yaw"]
		if event.finished:
			if event.is_accident():
				crashed = true
				_last_roll = roll
				speed_ms = 0.0
			event = null
	s += speed_ms * dt
	last_pos = route.position_at(s)
	_ax = ax
	_ay = ay
	_az = az
	_roll = roll
	_yaw = yaw


var _ax: float = 0.0
var _ay: float = 0.0
var _az: float = 1.0
var _roll: float = 0.0
var _yaw: float = 0.0
var _last_roll: float = 0.0


## Échantillon de télémétrie au format de l'API (POST /api/telemetry).
func sample() -> Dictionary:
	var smp: Dictionary = {
		"t": snappedf(clock, 0.0001),
		"speed": snappedf(maxf(0.0, speed_kmh() + randfn(0.0, 0.4)), 0.01),
		"ax": snappedf(_ax + randfn(0.0, 0.03), 0.0001),
		"ay": snappedf(_ay + randfn(0.0, 0.03), 0.0001),
		"az": snappedf(_az + randfn(0.0, 0.03), 0.0001),
		"roll": snappedf(_roll + randfn(0.0, 0.3), 0.001),
		"yaw_rate": snappedf(_yaw + randfn(0.0, 0.5), 0.001),
		"heading": snappedf(last_pos["heading"], 0.1),
	}
	if not gps_lost:
		smp["lat"] = snappedf(last_pos["lat"], 0.000001)
		smp["lon"] = snappedf(last_pos["lon"], 0.000001)
	return smp
