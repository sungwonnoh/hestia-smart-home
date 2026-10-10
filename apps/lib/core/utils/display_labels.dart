import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../models/context_state.dart';
import '../../models/device.dart';
import '../../models/medication.dart';
import '../../models/notification_item.dart';
import '../../models/room.dart';
import '../../models/weather.dart';
import 'time_format.dart';

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

  /// 화면에 보일 문장. presence는 state가 없고 area를 보내므로 공간 이름으로 표시한다.
  static String describe(ContextState s) {
    final area = s.area;
    if (s.name == ContextName.presence && area != null) {
      return '${RoomLabels.name(area)}에 있음';
    }
    return state(s.state);
  }

  static IconData iconOf(ContextState s) {
    if (s.name == ContextName.presence && s.area != null) {
      return Icons.person_pin_circle_rounded;
    }
    return icon(s.name, s.state);
  }

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

abstract final class RoomLabels {
  /// 공간 id → 이름. 설정 카탈로그에 없으면 id를 그대로 보여준다.
  static String name(String roomId) => RoomTemplate.byId(roomId)?.name ?? roomId;

  static IconData icon(String roomId) => switch (roomId) {
        'living' => Icons.weekend_rounded,
        'bedroom' => Icons.bed_rounded,
        'kitchen' => Icons.restaurant_rounded,
        'bathroom' => Icons.bathtub_rounded,
        'utility' => Icons.wash_rounded,
        'entrance' => Icons.door_front_door_rounded,
        'studio' => Icons.cottage_rounded,
        _ => Icons.meeting_room_rounded,
      };
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
        'MEDICATION_PROMPT' => Icons.medication_rounded,
        'LAUNDRY_DONE' => Icons.local_laundry_service_rounded,
        'VISITOR' => Icons.door_front_door_rounded,
        'AIR_QUALITY' => Icons.air_rounded,
        _ => null,
      };
}

/// 복약 일정 표시 문구와 아이콘.
abstract final class MedicationLabels {
  /// 남은 기간 문구와 강조 여부.
  static (String, bool) period(Medication m, DateTime now) {
    final left = m.remainingDays(now);
    if (left == null) return ('${m.days}일분', false);
    if (left == 0) {
      return (m.refillRequired ? '복용 기간 끝 · 처방 확인' : '복용 기간 끝', true);
    }
    if (m.needsRefill(now)) return ('$left일 남음 · 처방 확인', true);
    return ('$left일 남음', false);
  }

  static IconData slotIcon(DoseSlot slot) => switch (slot) {
        DoseSlot.breakfast => Icons.wb_twilight_rounded,
        DoseSlot.lunch => Icons.wb_sunny_rounded,
        DoseSlot.dinner => Icons.dinner_dining_rounded,
        DoseSlot.bedtime => Icons.bedtime_rounded,
      };

  static IconData timingIcon(MealTiming timing) => switch (timing) {
        MealTiming.beforeMeal => Icons.no_meals_rounded,
        MealTiming.rightAfterMeal => Icons.restaurant_rounded,
        MealTiming.afterMeal30 => Icons.timer_rounded,
      };
}

/// 바깥 날씨 표시 문구와 아이콘.
abstract final class WeatherLabels {
  /// 예: "습도 61% · 비 1.5mm · 바람 1.8m/s"
  static String details(Weather w) {
    final h = w.humidityPct;
    final mm = w.precipitationMm;
    final wind = w.windSpeedMs;
    return [
      if (h != null) '습도 ${h.round()}%',
      if (w.raining)
        mm != null && mm > 0
            ? '${w.precipType.label} ${_num(mm)}mm'
            : w.precipType.label,
      if (wind != null) '바람 ${_num(wind)}m/s',
    ].join(' · ');
  }

  static String temperature(Weather w) {
    final t = w.temperatureC;
    return t == null ? '--°C' : '${_num(t)}°C';
  }

  /// 예: "오늘 14:00 관측", 오래됐으면 " · 업데이트 지연"
  static String observed(Weather w, DateTime now) {
    final t = w.observedAt;
    final base =
        t == null ? '관측 시각 모름' : '${TimeFormat.full(t, now)} 관측';
    return w.stale ? '$base · 업데이트 지연' : base;
  }

  static IconData icon(Weather w) {
    if (w.precipType.isSnow) return Icons.ac_unit_rounded;
    if (w.raining) return Icons.umbrella_rounded;
    return Icons.thermostat_rounded;
  }

  /// 소수 첫째 자리까지, .0 은 뺀다 (24.0 → 24, 1.5 → 1.5).
  static String _num(double v) {
    final s = v.toStringAsFixed(1);
    return s.endsWith('.0') ? s.substring(0, s.length - 2) : s;
  }
}
