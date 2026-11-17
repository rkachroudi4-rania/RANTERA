extends Node
## Client HTTP vers l'API RANTERA. Regroupe les échantillons en lots (1 s), les met en file
## et les renvoie dans l'ordre tant que l'API est injoignable (batch_id stable : l'API est
## idempotente, donc pas de doublon d'incident à la reconnexion).

signal incident_received(incident: Dictionary)
signal connection_changed(online: bool)

var api_url: String = "http://127.0.0.1:8000"
var api_key: String = "rantera-dev-key"
var vehicle_id: String = "GODOT-001"
var source: String = "simulated"
var send_interval: float = 1.0
var max_queue: int = 1200      # ~20 minutes de télémétrie à 1 lot/s
var online: bool = false
var dropped: int = 0
var last_error: String = ""
var label: String = ""

var _http: HTTPRequest
var _samples: Array = []
var _queue: Array = []
var _inflight: bool = false
var _timer: float = 0.0
var _backoff: float = 0.0
var _seq: int = 0


func _ready() -> void:
	_http = HTTPRequest.new()
	_http.timeout = 5.0
	add_child(_http)
	_http.request_completed.connect(_on_completed)


func queue_size() -> int:
	return _queue.size()


func push_sample(smp: Dictionary) -> void:
	_samples.append(smp)


func _process(delta: float) -> void:
	_timer += delta
	if _timer >= send_interval:
		_timer = 0.0
		_seal_batch()
	if _backoff > 0.0:
		_backoff -= delta
	_try_send()


func _seal_batch() -> void:
	if _samples.is_empty():
		return
	_seq += 1
	var batch: Dictionary = {
		"vehicle_id": vehicle_id,
		"source": source,
		"samples": _samples,
		"batch_id": "%s-%d-%d-%d" % [vehicle_id, Time.get_ticks_msec(), _seq, randi()],
	}
	if label != "":
		batch["scenario_label"] = label
	_queue.append(batch)
	_samples = []
	while _queue.size() > max_queue:
		_queue.pop_front()
		dropped += 1


func _try_send() -> void:
	if _inflight or _queue.is_empty() or _backoff > 0.0:
		return
	var headers: PackedStringArray = PackedStringArray([
		"Content-Type: application/json",
		"X-API-Key: " + api_key,
	])
	var err: int = _http.request(api_url + "/api/telemetry", headers, HTTPClient.METHOD_POST, JSON.stringify(_queue[0]))
	if err != OK:
		last_error = "Requête impossible (code %d)" % err
		_set_online(false)
		_backoff = 2.0
		return
	_inflight = true


func _on_completed(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	_inflight = false
	if result == HTTPRequest.RESULT_SUCCESS and code == 200:
		_set_online(true)
		last_error = ""
		var data = JSON.parse_string(body.get_string_from_utf8())
		if typeof(data) == TYPE_DICTIONARY:
			for inc in data.get("incidents", []):
				incident_received.emit(inc)
		_queue.pop_front()
		_backoff = 0.0
	elif result == HTTPRequest.RESULT_SUCCESS and code == 401:
		last_error = "Clé API refusée (vérifier config.json)"
		_set_online(false)
		_backoff = 5.0  # on conserve le lot : ne rien perdre
	elif result == HTTPRequest.RESULT_SUCCESS and code >= 400 and code < 500 and code != 429:
		last_error = "Lot rejeté (HTTP %d) : %s" % [code, body.get_string_from_utf8().left(120)]
		push_warning(last_error)
		_queue.pop_front()  # un lot invalide ne doit pas bloquer la file
	else:
		last_error = "API injoignable ou erreur serveur (résultat %d, HTTP %d)" % [result, code]
		_set_online(false)
		_backoff = 2.0


func _set_online(v: bool) -> void:
	if v != online:
		online = v
		connection_changed.emit(v)
