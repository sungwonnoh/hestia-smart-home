enum NotificationType {
  info('INFO'),
  reminder('REMINDER'),
  warning('WARNING'),
  safety('SAFETY');

  const NotificationType(this.wireName);
  final String wireName;

  static NotificationType fromWire(String? value) {
    for (final t in values) {
      if (t.wireName == value) return t;
    }
    return info;
  }
}

enum NotificationPriority {
  low('low'),
  normal('normal'),
  high('high'),
  safety('safety');

  const NotificationPriority(this.wireName);
  final String wireName;

  static NotificationPriority fromWire(String? value) {
    for (final p in values) {
      if (p.wireName == value) return p;
    }
    return normal;
  }
}

/// ACK 종류. MQTT `hestia/notify/ack`와 의미를 맞춘다.
///
/// - [delivered]: 채널에 표시됨
/// - [seen]: 사용자가 실제로 확인함 — 화면에 표시했다고 seen이 아니다.
enum AckType {
  delivered('DELIVERED'),
  seen('SEEN');

  const AckType(this.wireName);
  final String wireName;
}

/// HESTIA가 생성한 알림.
class HestiaNotification {
  const HestiaNotification({
    required this.id,
    required this.scenario,
    required this.title,
    required this.message,
    required this.createdAt,
    this.type = NotificationType.info,
    this.priority = NotificationPriority.normal,
    this.roomId,
    this.explanationId,
    this.delivered = false,
    this.seen = false,
  });

  final String id;

  /// 시나리오 키 (예: MEDICATION, LAUNDRY_DONE, VISITOR).
  final String scenario;
  final NotificationType type;
  final NotificationPriority priority;
  final String title;
  final String message;
  final DateTime createdAt;

  /// 알림이 전달된 공간.
  final String? roomId;

  /// 이 알림을 만든 판단 근거.
  final String? explanationId;

  final bool delivered;
  final bool seen;

  bool get isSafety =>
      type == NotificationType.safety ||
      priority == NotificationPriority.safety;

  HestiaNotification copyWith({bool? delivered, bool? seen}) =>
      HestiaNotification(
        id: id,
        scenario: scenario,
        type: type,
        priority: priority,
        title: title,
        message: message,
        createdAt: createdAt,
        roomId: roomId,
        explanationId: explanationId,
        delivered: delivered ?? this.delivered,
        seen: seen ?? this.seen,
      );

  factory HestiaNotification.fromJson(Map<String, dynamic> json) =>
      HestiaNotification(
        id: json['id'] as String,
        scenario: (json['scenario'] ?? '') as String,
        type: NotificationType.fromWire(json['type'] as String?),
        priority: NotificationPriority.fromWire(json['priority'] as String?),
        title: json['title'] as String,
        message: (json['message'] ?? '') as String,
        createdAt: DateTime.parse(json['createdAt'] as String),
        roomId: json['roomId'] as String?,
        explanationId: json['explanationId'] as String?,
        delivered: json['delivered'] as bool? ?? false,
        seen: json['seen'] as bool? ?? false,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'scenario': scenario,
        'type': type.wireName,
        'priority': priority.wireName,
        'title': title,
        'message': message,
        'createdAt': createdAt.toIso8601String(),
        if (roomId != null) 'roomId': roomId,
        if (explanationId != null) 'explanationId': explanationId,
        'delivered': delivered,
        'seen': seen,
      };
}
