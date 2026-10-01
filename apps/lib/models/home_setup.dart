import 'device.dart';
import 'room.dart';
import 'user_preferences.dart';

/// 최초 설정 결과 (`PUT /api/v1/setup`).
///
/// 사용자 상태(HOME, SLEEPING 등)는 포함하지 않는다.
/// 그 값은 Context Engine이 판단한다.
class HomeSetup {
  const HomeSetup({
    required this.rooms,
    required this.devices,
    required this.preferences,
  });

  final List<Room> rooms;
  final List<Device> devices;
  final UserPreferences preferences;

  factory HomeSetup.fromJson(Map<String, dynamic> json) => HomeSetup(
        rooms: (json['rooms'] as List<dynamic>? ?? const [])
            .map((e) => Room.fromJson(e as Map<String, dynamic>))
            .toList(),
        devices: (json['devices'] as List<dynamic>? ?? const [])
            .map((e) => Device.fromJson(e as Map<String, dynamic>))
            .toList(),
        preferences: UserPreferences.fromJson(
          json['preferences'] as Map<String, dynamic>? ?? const {},
        ),
      );

  Map<String, dynamic> toJson() => {
        'rooms': rooms.map((r) => r.toJson()).toList(),
        'devices': devices.map((d) => d.toJson()).toList(),
        'preferences': preferences.toJson(),
      };
}
