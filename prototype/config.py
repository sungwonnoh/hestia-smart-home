"""프로토타입 설정.

엔진 코드는 물리 ID를 직접 참조하지 않는다.
sensor_id / device_id -> area / role 매핑은 전부 여기에만 있다.
나중에 areas/roles/sensors YAML로 옮길 자리.
"""

MQTT_HOST = "localhost"
MQTT_PORT = 1883
PREFIX = "home"

# sensor_id -> 이 센서가 어느 공간의 무슨 타입인가
SENSORS = {
    "livingroom_presence": {"area": "LIVINGROOM", "type": "presence"},
    "kitchen_presence":    {"area": "KITCHEN",    "type": "presence"},
    "bedroom_presence":    {"area": "BEDROOM",    "type": "presence"},
}

# device_id -> 이 기기가 무슨 역할인가
DEVICES = {
    "tv":     {"role": "TV",     "area": "LIVINGROOM"},
    "washer": {"role": "WASHER", "area": "UTILITY"},
    "light":  {"role": "LIGHT",  "area": "LIVINGROOM"},
}

# 같은 종류 알림을 다시 보내기까지의 최소 간격(초)
NOTIFY_COOLDOWN_SEC = 600