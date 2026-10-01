import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../models/context_state.dart';
import '../../models/device.dart';
import '../../models/notification_item.dart';

/// Context Engine 값을 사람이 읽는 문자열로 바꾼다.
///
/// 표시용 변환만 한다. 상태를 새로 추론하지 않는다.
abstract final class ContextLabels {
  static const _names = {
    ContextName.activity: '활동',
    ContextName.presence: '재실',
    ContextName.wake: '기상',
    ContextName.away: '외출',
    ContextName.occupancy: '인원',
    ContextName.suppression: '알림 억제',
  };

  static const _states = {
    'AWAY': '외출 중',
    'HOME': '집에 있음',
    'SLEEPING': '수면 중',
    'EATING': '식사 중',
    'MEAL_PREP': '식사 준비 중',
    'MEAL_DONE': '식사 완료',
    'WORKING': '집중 중',
    'IDLE': '휴식 중',
    'PRESENT': '재실 중',
    'ABSENT': '자리 비움',
    'AWAKE': '깨어 있음',
    'SINGLE': '1명',
    'MULTI': '여러 명',
    'NONE': '없음',
    'ACTIVE': '적용 중',
    'UNKNOWN': '상태 확인 중',
    'SENSOR_FAULT': '센서 확인 필요',
  };

  static String name(String contextName) => _names[contextName] ?? contextName;

  /// 모르는 값은 원문 그대로 보여준다.
  static String state(String state) => _states[state] ?? state;

  static bool isFault(String state) => state == 'SENSOR_FAULT';

  static IconData icon(String contextName, String state) {
    switch (state) {
      case 'HOME':
        return Icons.home_rounded;
      case 'AWAY':
        return Icons.directions_walk_rounded;
      case 'SLEEPING':
        return Icons.bedtime_rounded;
      case 'EATING':
      case 'MEAL_DONE':
        return Icons.restaurant_rounded;
      case 'MEAL_PREP':
        return Icons.soup_kitchen_rounded;
      case 'WORKING':
        return Icons.laptop_rounded;
      case 'ABSENT':
        return Icons.person_off_rounded;
      case 'SENSOR_FAULT':
        return Icons.sensors_off_rounded;
      case 'UNKNOWN':
        return Icons.help_outline_rounded;
    }
    return switch (contextName) {
      ContextName.presence => Icons.person_rounded,
      ContextName.wake => Icons.wb_sunny_rounded,
      ContextName.occupancy => Icons.groups_rounded,
      ContextName.suppression => Icons.notifications_paused_rounded,
      ContextName.away => Icons.home_rounded,
      _ => Icons.insights_rounded,
    };
  }
}

abstract final class DeviceLabels {
  static IconData icon(DeviceType type) => switch (type) {
        DeviceType.tv => Icons.tv_rounded,
        DeviceType.light => Icons.lightbulb_rounded,
        DeviceType.airConditioner => Icons.ac_unit_rounded,
        DeviceType.airPurifier => Icons.air_rounded,
        DeviceType.refrigerator => Icons.kitchen_rounded,
        DeviceType.washer => Icons.local_laundry_service_rounded,
        DeviceType.waterPurifier => Icons.water_drop_rounded,
        DeviceType.robotCleaner => Icons.cleaning_services_rounded,
        DeviceType.unknown => Icons.devices_other_rounded,
      };

  static const _states = {
    'ON': '켜짐',
    'OFF': '꺼짐',
    'STANDBY': '대기',
    'AUTO': '자동',
    'RUNNING': '동작 중',
    'DONE': '완료',
    'UNKNOWN': '알 수 없음',
  };

  static String status(Device device) {
    if (!device.online) return '연결 끊김';
    final status = device.status;
    final temp = status.attributes['targetTemp'];
    if (status.state == 'ON' && temp != null) return '$temp°C';
    return _states[status.state] ?? status.state;
  }

  static bool isActive(Device device) =>
      device.online &&
      const {'ON', 'AUTO', 'RUNNING'}.contains(device.status.state);
}

class NotificationStyle {
  const NotificationStyle({
    required this.icon,
    required this.color,
    required this.container,
    required this.label,
  });

  final IconData icon;
  final Color color;
  final Color container;
  final String label;

  static NotificationStyle of(HestiaNotification n, ColorScheme scheme) {
    if (n.isSafety) {
      return const NotificationStyle(
        icon: Icons.health_and_safety_rounded,
        color: HestiaColors.safety,
        container: HestiaColors.safetyContainer,
        label: '안전',
      );
    }
    if (n.type == NotificationType.warning ||
        n.priority == NotificationPriority.high) {
      return NotificationStyle(
        icon: _scenarioIcon(n.scenario) ?? Icons.warning_amber_rounded,
        color: HestiaColors.warning,
        container: HestiaColors.warningContainer,
        label: n.type == NotificationType.warning ? '주의' : '중요',
      );
    }
    return NotificationStyle(
      icon: _scenarioIcon(n.scenario) ??
          (n.type == NotificationType.reminder
              ? Icons.alarm_rounded
              : Icons.notifications_rounded),
      color: scheme.primary,
      container: scheme.primaryContainer,
      label: n.type == NotificationType.reminder ? '리마인더' : '알림',
    );
  }

  static IconData? _scenarioIcon(String scenario) => switch (scenario) {
        'MEDICATION' => Icons.medication_rounded,
        'LAUNDRY_DONE' => Icons.local_laundry_service_rounded,
        'VISITOR' => Icons.door_front_door_rounded,
        'AIR_QUALITY' => Icons.air_rounded,
        _ => null,
      };
}
