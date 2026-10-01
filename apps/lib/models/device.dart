/// HESTIA가 관리하는 가전 종류.
///
/// 서버/설정에서 모르는 값이 와도 깨지지 않도록 [unknown]으로 받는다.
enum DeviceType {
  tv('TV', 'TV', ['living', 'studio', 'bedroom']),
  light('LIGHT', '조명', ['living', 'studio', 'bedroom']),
  airConditioner('AIR_CONDITIONER', '에어컨', ['living', 'studio', 'bedroom']),
  airPurifier('AIR_PURIFIER', '공기청정기', ['living', 'studio', 'bedroom']),
  refrigerator('REFRIGERATOR', '냉장고', ['kitchen', 'studio']),
  washer('WASHER', '세탁기', ['utility', 'bathroom', 'studio']),
  waterPurifier('WATER_PURIFIER', '정수기', ['kitchen', 'studio']),
  robotCleaner('ROBOT_CLEANER', '로봇청소기', ['living', 'studio']),
  unknown('UNKNOWN', '기타 가전', []);

  const DeviceType(this.wireName, this.label, this.preferredRoomIds);

  /// API/MQTT에서 쓰는 문자열.
  final String wireName;
  final String label;

  /// 가전 배치 단계의 기본 선택 순서. 판단 로직이 아닌 입력 편의용.
  final List<String> preferredRoomIds;

  static DeviceType fromWire(String? value) {
    for (final t in values) {
      if (t.wireName == value) return t;
    }
    return unknown;
  }

  /// 사용자가 고를 수 있는 가전 목록.
  static List<DeviceType> get selectable =>
      values.where((t) => t != unknown).toList();
}

/// 가전의 마지막 상태. Context Engine/Device가 보고한 값을 그대로 담는다.
class DeviceStatus {
  const DeviceStatus({
    required this.state,
    this.attributes = const {},
  });

  /// ON / OFF / STANDBY / AUTO / RUNNING / DONE 등.
  final String state;

  /// 장치별 추가 값 (예: targetTemp: 24).
  final Map<String, dynamic> attributes;

  static const unknown = DeviceStatus(state: 'UNKNOWN');

  factory DeviceStatus.fromJson(Object? json) {
    if (json is String) return DeviceStatus(state: json);
    if (json is Map<String, dynamic>) {
      return DeviceStatus(
        state: (json['state'] ?? 'UNKNOWN').toString(),
        attributes: Map<String, dynamic>.from(json)..remove('state'),
      );
    }
    return unknown;
  }

  Map<String, dynamic> toJson() => {'state': state, ...attributes};
}

class Device {
  const Device({
    required this.id,
    required this.name,
    required this.type,
    required this.roomId,
    this.status = DeviceStatus.unknown,
    this.online = true,
  });

  final String id;
  final String name;
  final DeviceType type;
  final String roomId;
  final DeviceStatus status;
  final bool online;

  Device copyWith({
    String? name,
    String? roomId,
    DeviceStatus? status,
    bool? online,
  }) =>
      Device(
        id: id,
        name: name ?? this.name,
        type: type,
        roomId: roomId ?? this.roomId,
        status: status ?? this.status,
        online: online ?? this.online,
      );

  factory Device.fromJson(Map<String, dynamic> json) => Device(
        id: json['id'] as String,
        name: json['name'] as String,
        type: DeviceType.fromWire(json['type'] as String?),
        roomId: (json['roomId'] ?? '') as String,
        status: DeviceStatus.fromJson(json['status']),
        online: json['online'] as bool? ?? false,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'type': type.wireName,
        'roomId': roomId,
        'status': status.toJson(),
        'online': online,
      };
}
