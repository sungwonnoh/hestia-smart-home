enum NotificationSensitivity {
  low('low', '낮음', '꼭 필요한 알림만'),
  normal('normal', '보통', '권장'),
  high('high', '높음', '작은 변화도 알림');

  const NotificationSensitivity(this.wireName, this.label, this.description);
  final String wireName;
  final String label;
  final String description;

  static NotificationSensitivity fromWire(String? value) {
    for (final s in values) {
      if (s.wireName == value) return s;
    }
    return normal;
  }
}

/// 하루 중 시각. "HH:mm" 문자열로 직렬화한다.
class DayTime {
  const DayTime(this.hour, this.minute);

  final int hour;
  final int minute;

  factory DayTime.parse(String value) {
    final parts = value.split(':');
    return DayTime(int.parse(parts[0]), int.parse(parts[1]));
  }

  String format() =>
      '${hour.toString().padLeft(2, '0')}:${minute.toString().padLeft(2, '0')}';

  @override
  bool operator ==(Object other) =>
      other is DayTime && other.hour == hour && other.minute == minute;

  @override
  int get hashCode => Object.hash(hour, minute);
}

/// 방해 금지 시간.
class QuietHours {
  const QuietHours({
    this.enabled = true,
    this.start = const DayTime(22, 0),
    this.end = const DayTime(7, 0),
  });

  final bool enabled;
  final DayTime start;
  final DayTime end;

  QuietHours copyWith({bool? enabled, DayTime? start, DayTime? end}) =>
      QuietHours(
        enabled: enabled ?? this.enabled,
        start: start ?? this.start,
        end: end ?? this.end,
      );

  factory QuietHours.fromJson(Map<String, dynamic> json) => QuietHours(
        enabled: json['enabled'] as bool? ?? true,
        start: DayTime.parse((json['start'] ?? '22:00') as String),
        end: DayTime.parse((json['end'] ?? '07:00') as String),
      );

  Map<String, dynamic> toJson() => {
        'enabled': enabled,
        'start': start.format(),
        'end': end.format(),
      };
}

/// 안전 알림 설정. 일반 알림과 분리해서 다룬다.
///
/// 방해 금지 시간이나 일반 알림 OFF와 무관하게 동작해야 하므로
/// 앱에서는 끌 수 없게 둔다.
class SafetyPreferences {
  const SafetyPreferences({
    this.enabled = true,
    this.emergencyContact,
  });

  final bool enabled;
  final String? emergencyContact;

  SafetyPreferences copyWith({String? emergencyContact}) => SafetyPreferences(
        enabled: enabled,
        emergencyContact: emergencyContact ?? this.emergencyContact,
      );

  factory SafetyPreferences.fromJson(Map<String, dynamic> json) =>
      SafetyPreferences(
        enabled: json['enabled'] as bool? ?? true,
        emergencyContact: json['emergencyContact'] as String?,
      );

  Map<String, dynamic> toJson() => {
        'enabled': enabled,
        if (emergencyContact != null) 'emergencyContact': emergencyContact,
      };
}

class UserPreferences {
  const UserPreferences({
    this.notificationsEnabled = true,
    this.quietHours = const QuietHours(),
    this.sensitivity = NotificationSensitivity.normal,
    this.safety = const SafetyPreferences(),
  });

  /// 일반 알림 ON/OFF. 안전 알림은 [safety]에서 따로 다룬다.
  final bool notificationsEnabled;
  final QuietHours quietHours;
  final NotificationSensitivity sensitivity;
  final SafetyPreferences safety;

  UserPreferences copyWith({
    bool? notificationsEnabled,
    QuietHours? quietHours,
    NotificationSensitivity? sensitivity,
    SafetyPreferences? safety,
  }) =>
      UserPreferences(
        notificationsEnabled: notificationsEnabled ?? this.notificationsEnabled,
        quietHours: quietHours ?? this.quietHours,
        sensitivity: sensitivity ?? this.sensitivity,
        safety: safety ?? this.safety,
      );

  factory UserPreferences.fromJson(Map<String, dynamic> json) =>
      UserPreferences(
        notificationsEnabled: json['notificationsEnabled'] as bool? ?? true,
        quietHours: QuietHours.fromJson(
          json['quietHours'] as Map<String, dynamic>? ?? const {},
        ),
        sensitivity:
            NotificationSensitivity.fromWire(json['sensitivity'] as String?),
        safety: SafetyPreferences.fromJson(
          json['safety'] as Map<String, dynamic>? ?? const {},
        ),
      );

  Map<String, dynamic> toJson() => {
        'notificationsEnabled': notificationsEnabled,
        'quietHours': quietHours.toJson(),
        'sensitivity': sensitivity.wireName,
        'safety': safety.toJson(),
      };
}
