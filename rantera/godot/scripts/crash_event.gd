extends RefCounted
## Événement simulé : accident (impulsion demi-sinusoïdale), freinage brutal ou nid-de-poule.
## Reprend le modèle de rantera/scenarios.py (mêmes unités : g, km/h, degrés).

const G := 9.81

var kind: String = ""
var t: float = 0.0
var pulse_t: float = 0.14
var amp_g: float = 0.0
var side: float = 1.0
var roll_final: float = 0.0
var brake_g: float = 0.5
var hb_g: float = 0.8
var hb_drop_ms: float = 12.0
var v_start: float = -1.0
var finished: bool = false


func _init(p_kind: String, dv_kmh: float) -> void:
	kind = p_kind
	pulse_t = randf_range(0.10, 0.18)
	side = 1.0 if randf() < 0.5 else -1.0
	brake_g = randf_range(0.35, 0.8)
	roll_final = side * randf_range(90.0, 180.0)
	hb_g = randf_range(0.65, 0.95)
	hb_drop_ms = randf_range(35.0, 70.0) / 3.6
	amp_g = (dv_kmh / 3.6) * PI / (2.0 * pulse_t) / G


func is_accident() -> bool:
	return kind in ["frontal", "lateral", "rear", "rollover"]


## Avance d'un pas. Retourne {ax, ay, az, roll, yaw, speed} (speed en m/s).
func step(dt: float, speed_ms: float) -> Dictionary:
	var ax: float = 0.0
	var ay: float = 0.0
	var az: float = 1.0
	var roll: float = 0.0
	var yaw: float = 0.0
	var v: float = speed_ms
	if v_start < 0.0:
		v_start = speed_ms

	if is_accident():
		if t < pulse_t:
			var a: float = amp_g * sin(PI * t / pulse_t)
			if kind == "frontal":
				ax = -a
			elif kind == "rear":
				ax = a
			else:  # lateral ou début de tonneau
				ay = side * a
			v = maxf(0.0, v + ax * G * dt)
		else:
			var decel: float = brake_g
			if kind == "rollover":
				decel = maxf(decel, 0.7)
			if v > 0.2:
				ax = -decel
				v = maxf(0.0, v - decel * G * dt)
		if kind == "rollover":
			var prog: float = clampf(t / 1.2, 0.0, 1.0)
			roll = roll_final * (prog * prog * (3.0 - 2.0 * prog))
			if t < 1.4:
				az = 1.0 + 1.5 * sin(TAU * t / 0.45)
				yaw = side * 220.0 * exp(-t)
		if t > 8.0:
			finished = true
	elif kind == "hard_brake":
		if (v_start - v) < hb_drop_ms and v > 0.5:
			ax = -hb_g
			v = maxf(0.0, v + ax * G * dt)
		else:
			finished = true
	elif kind == "pothole":
		if t < 0.08:
			az = 1.0 + 3.5 * sin(PI * t / 0.08)
			ax = -0.25 * sin(PI * t / 0.08)
		else:
			finished = true

	t += dt
	return {"ax": ax, "ay": ay, "az": az, "roll": roll, "yaw": yaw, "speed": v}
