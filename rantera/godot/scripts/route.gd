extends RefCounted
## Itinéraire GPS (même fichier JSON que le simulateur Python).

const M_PER_DEG := 111320.0

var xy: PackedVector2Array = PackedVector2Array()  # mètres : x = est, y = nord
var cum: PackedFloat64Array = PackedFloat64Array()
var length: float = 0.0
var lat0: float = 0.0
var lon0: float = 0.0
var kx: float = 1.0
var loop: bool = true


func load_from(path: String) -> bool:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		push_error("Itinéraire introuvable : " + path)
		return false
	var data = JSON.parse_string(f.get_as_text())
	if typeof(data) != TYPE_DICTIONARY or not data.has("waypoints"):
		push_error("Format d'itinéraire invalide : " + path)
		return false
	loop = bool(data.get("loop", true))
	var wps: Array = data["waypoints"]
	if wps.size() < 2:
		push_error("Itinéraire trop court")
		return false
	var pts: Array = []
	for w in wps:
		pts.append(Vector2(float(w["lat"]), float(w["lon"])))
	if loop and pts[0] != pts[pts.size() - 1]:
		pts.append(pts[0])
	lat0 = pts[0].x
	lon0 = pts[0].y
	kx = M_PER_DEG * cos(deg_to_rad(lat0))
	xy.clear()
	cum.clear()
	for p in pts:
		xy.append(Vector2((p.y - lon0) * kx, (p.x - lat0) * M_PER_DEG))
	cum.append(0.0)
	for i in range(1, xy.size()):
		cum.append(cum[i - 1] + xy[i].distance_to(xy[i - 1]))
	length = cum[cum.size() - 1]
	return length > 0.0


func position_at(s: float) -> Dictionary:
	if loop:
		s = fposmod(s, length)
	else:
		s = clampf(s, 0.0, length)
	var i: int = 0
	while i < xy.size() - 2 and cum[i + 1] <= s:
		i += 1
	var seg: float = cum[i + 1] - cum[i]
	var f: float = 0.0
	if seg > 0.0:
		f = (s - cum[i]) / seg
	var a: Vector2 = xy[i]
	var b: Vector2 = xy[i + 1]
	var p: Vector2 = a.lerp(b, f)
	var heading: float = fposmod(rad_to_deg(atan2(b.x - a.x, b.y - a.y)), 360.0)
	return {
		"x": p.x, "y": p.y,
		"lat": lat0 + p.y / M_PER_DEG, "lon": lon0 + p.x / kx,
		"heading": heading,
	}
