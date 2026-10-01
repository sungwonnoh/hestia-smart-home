import 'dart:async';

import '../core/network/hestia_exception.dart';
import '../models/context_state.dart';
import '../models/device.dart';
import '../models/explanation.dart';
import '../models/home_setup.dart';
import '../models/notification_item.dart';
import '../models/room.dart';
import '../models/user_preferences.dart';
import 'hestia_repository.dart';

/// 시연/개발용 조작. Mock 구현체만 제공한다.
abstract interface class DemoControls {
  bool get offline;

  /// 서버 연결 끊김을 흉내 낸다.
  void setOffline(bool value);

  /// 새 알림이 도착한 것처럼 만든다.
  HestiaNotification pushDemoNotification();

  /// 최초 설정을 지워 Onboarding부터 다시 시작한다.
  Future<void> resetSetup();
}

/// 서버 없이 동작하는 메모리 기반 Repository.
class MockHestiaRepository implements HestiaRepository, DemoControls {
  MockHestiaRepository({
    this.latency = const Duration(milliseconds: 300),
    HomeSetup? initialSetup,
    DateTime Function()? clock,
  }) : _clock = clock ?? DateTime.now {
    if (initialSetup != null) _applySetup(initialSetup);
    _notifications = _seedNotifications(_clock());
  }

  final Duration latency;
  final DateTime Function() _clock;

  HomeSetup? _setup;
  UserPreferences _preferences = const UserPreferences();
  List<Device> _devices = const [];
  late List<HestiaNotification> _notifications;
  bool _offline = false;
  int _demoCursor = 0;

  final _notificationController =
      StreamController<HestiaNotification>.broadcast();

  // ---------------------------------------------------------------- setup

  @override
  Future<HomeSetup?> getSetup() async {
    await _respond();
    return _setup;
  }

  @override
  Future<void> saveSetup(HomeSetup setup) async {
    await _respond();
    _applySetup(setup);
  }

  void _applySetup(HomeSetup setup) {
    // 저장된 가전에 Mock 상태를 붙인다. 실제로는 Device가 보고한 값이다.
    _devices = [
      for (final d in setup.devices)
        d.copyWith(
          status: _mockStatusFor(d.type),
          online: d.type != DeviceType.robotCleaner,
        ),
    ];
    _preferences = setup.preferences;
    _setup = HomeSetup(
      rooms: setup.rooms,
      devices: _devices,
      preferences: _preferences,
    );
  }

  // ---------------------------------------------------------------- home

  @override
  Future<List<Room>> getRooms() async {
    await _respond();
    return List.unmodifiable(_setup?.rooms ?? const <Room>[]);
  }

  @override
  Future<List<Device>> getDevices() async {
    await _respond();
    return List.unmodifiable(_devices);
  }

  @override
  Future<HestiaContext> getCurrentContext() async {
    await _respond();
    final now = _clock();
    return HestiaContext(
      updatedAt: now,
      states: {
        ContextName.away: const ContextState(
          name: ContextName.away,
          state: 'HOME',
        ),
        ContextName.activity: ContextState(
          name: ContextName.activity,
          state: 'MEAL_DONE',
          since: now.subtract(const Duration(minutes: 3)),
          confidence: 0.87,
          factors: const {'area': 'kitchen', 'presence': true},
        ),
        ContextName.presence: const ContextState(
          name: ContextName.presence,
          state: 'PRESENT',
          factors: {'area': 'living'},
        ),
        ContextName.wake: const ContextState(
          name: ContextName.wake,
          state: 'AWAKE',
        ),
        ContextName.occupancy: const ContextState(
          name: ContextName.occupancy,
          state: 'SINGLE',
        ),
      },
    );
  }

  // ---------------------------------------------------------------- notify

  @override
  Future<List<HestiaNotification>> getNotifications() async {
    await _respond();
    final sorted = [..._notifications]
      ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
    return List.unmodifiable(sorted);
  }

  @override
  Future<void> acknowledgeNotification(
    String notificationId, {
    AckType ackType = AckType.seen,
  }) async {
    await _respond();
    final index = _notifications.indexWhere((n) => n.id == notificationId);
    if (index < 0) {
      throw HestiaException('알림을 찾을 수 없습니다: $notificationId');
    }
    final current = _notifications[index];
    _notifications[index] = switch (ackType) {
      AckType.delivered => current.copyWith(delivered: true),
      AckType.seen => current.copyWith(delivered: true, seen: true),
    };
  }

  @override
  Stream<HestiaNotification> watchNotifications() =>
      _notificationController.stream;

  // ---------------------------------------------------------------- explain

  @override
  Future<Explanation?> getLatestExplanation() async {
    await _respond();
    return _explanations['exp-meal-done'];
  }

  @override
  Future<Explanation?> getExplanation(String explanationId) async {
    await _respond();
    return _explanations[explanationId];
  }

  // ---------------------------------------------------------------- prefs

  @override
  Future<UserPreferences> getPreferences() async {
    await _respond();
    return _preferences;
  }

  @override
  Future<void> savePreferences(UserPreferences preferences) async {
    await _respond();
    _preferences = preferences;
    final setup = _setup;
    if (setup != null) {
      _setup = HomeSetup(
        rooms: setup.rooms,
        devices: setup.devices,
        preferences: preferences,
      );
    }
  }

  // ---------------------------------------------------------------- demo

  @override
  bool get offline => _offline;

  @override
  void setOffline(bool value) => _offline = value;

  @override
  HestiaNotification pushDemoNotification() {
    final template = _demoTemplates[_demoCursor % _demoTemplates.length];
    _demoCursor++;
    final notification = HestiaNotification(
      id: 'n-demo-live-${_clock().millisecondsSinceEpoch}',
      scenario: template.scenario,
      type: template.type,
      priority: template.priority,
      title: template.title,
      message: template.message,
      createdAt: _clock(),
      roomId: _nearestRoomId(),
      explanationId: template.explanationId,
      delivered: true,
    );
    _notifications.add(notification);
    _notificationController.add(notification);
    return notification;
  }

  @override
  Future<void> resetSetup() async {
    _setup = null;
    _devices = const [];
    _preferences = const UserPreferences();
    _notifications = _seedNotifications(_clock());
  }

  void dispose() => _notificationController.close();

  // ---------------------------------------------------------------- helpers

  Future<void> _respond() async {
    if (latency > Duration.zero) await Future<void>.delayed(latency);
    if (_offline) throw const HestiaOfflineException();
  }

  String? _nearestRoomId() {
    final rooms = _setup?.rooms ?? const <Room>[];
    for (final id in const ['living', 'studio']) {
      if (rooms.any((r) => r.id == id)) return id;
    }
    return rooms.isEmpty ? null : rooms.first.id;
  }

  static DeviceStatus _mockStatusFor(DeviceType type) => switch (type) {
        DeviceType.tv => const DeviceStatus(state: 'ON'),
        DeviceType.light => const DeviceStatus(state: 'ON'),
        DeviceType.airConditioner =>
          const DeviceStatus(state: 'ON', attributes: {'targetTemp': 24}),
        DeviceType.airPurifier => const DeviceStatus(state: 'AUTO'),
        DeviceType.refrigerator => const DeviceStatus(state: 'ON'),
        DeviceType.washer => const DeviceStatus(state: 'DONE'),
        DeviceType.waterPurifier => const DeviceStatus(state: 'STANDBY'),
        DeviceType.robotCleaner => const DeviceStatus(state: 'STANDBY'),
        DeviceType.unknown => DeviceStatus.unknown,
      };

  static List<HestiaNotification> _seedNotifications(DateTime now) => [
        HestiaNotification(
          id: 'n-demo-001',
          scenario: 'MEDICATION',
          type: NotificationType.reminder,
          priority: NotificationPriority.normal,
          title: '식사 후 복약 시간입니다.',
          message: 'HESTIA가 식사 완료를 감지했습니다.\n복약 시간을 확인해주세요.',
          createdAt: now.subtract(const Duration(minutes: 1)),
          roomId: 'living',
          explanationId: 'exp-meal-done',
          delivered: true,
        ),
        HestiaNotification(
          id: 'n-demo-002',
          scenario: 'LAUNDRY_DONE',
          type: NotificationType.info,
          priority: NotificationPriority.normal,
          title: '세탁이 완료되었습니다.',
          message: '세탁기 동작이 끝났습니다. 빨래를 꺼내 주세요.',
          createdAt: now.subtract(const Duration(hours: 1, minutes: 12)),
          roomId: 'living',
          delivered: true,
        ),
        HestiaNotification(
          id: 'n-demo-003',
          scenario: 'VISITOR',
          type: NotificationType.info,
          priority: NotificationPriority.high,
          title: '방문자가 감지되었습니다.',
          message: '현관에서 움직임이 감지되었습니다.',
          createdAt: now.subtract(const Duration(hours: 5, minutes: 40)),
          roomId: 'entrance',
          delivered: true,
          seen: true,
        ),
        HestiaNotification(
          id: 'n-demo-004',
          scenario: 'INACTIVITY',
          type: NotificationType.safety,
          priority: NotificationPriority.safety,
          title: '장시간 움직임이 없습니다.',
          message: '평소 활동 시간대에 움직임이 감지되지 않았습니다. 확인해주세요.',
          createdAt: now.subtract(const Duration(days: 1, hours: 2)),
          roomId: 'living',
          delivered: true,
          seen: true,
        ),
      ];

  static const _demoTemplates = [
    _DemoTemplate(
      scenario: 'COOKING_UNATTENDED',
      type: NotificationType.safety,
      priority: NotificationPriority.safety,
      title: '인덕션이 켜진 채 자리를 비웠습니다.',
      message: '주방을 떠난 지 5분이 지났습니다. 인덕션을 확인해주세요.',
      explanationId: 'exp-cooking-unattended',
    ),
    _DemoTemplate(
      scenario: 'MEDICATION',
      type: NotificationType.reminder,
      priority: NotificationPriority.normal,
      title: '식사 후 복약 시간입니다.',
      message: 'HESTIA가 식사 완료를 감지했습니다.\n복약 시간을 확인해주세요.',
      explanationId: 'exp-meal-done',
    ),
    _DemoTemplate(
      scenario: 'AIR_QUALITY',
      type: NotificationType.warning,
      priority: NotificationPriority.high,
      title: '실내 공기질이 나빠졌습니다.',
      message: '공기청정기를 자동 모드로 유지하고 환기를 권장합니다.',
    ),
  ];

  static const _explanations = <String, Explanation>{
    'exp-meal-done': Explanation(
      id: 'exp-meal-done',
      contextName: ContextName.activity,
      state: 'MEAL_DONE',
      confidence: 0.87,
      factors: [
        ExplanationFactor(label: '주방 재실 감지', detail: '식탁 mmWave'),
        ExplanationFactor(label: '식사 시간대와 일치', detail: '평소 19:00 전후'),
        ExplanationFactor(label: '식사 관련 가전 사용', detail: '인덕션 사용 후 종료'),
        ExplanationFactor(label: '일정 시간 이상 행동 지속', detail: '25분'),
      ],
      action: '복약 알림을 준비했습니다.',
    ),
    'exp-cooking-unattended': Explanation(
      id: 'exp-cooking-unattended',
      contextName: ContextName.presence,
      state: 'ABSENT',
      confidence: 0.93,
      factors: [
        ExplanationFactor(label: '인덕션 사용 중', detail: '약 1,200W'),
        ExplanationFactor(label: '주방 이석 5분 이상'),
        ExplanationFactor(label: '거실에서 재실 감지'),
      ],
      action: '안전 알림을 보냈습니다.',
    ),
  };
}

class _DemoTemplate {
  const _DemoTemplate({
    required this.scenario,
    required this.type,
    required this.priority,
    required this.title,
    required this.message,
    this.explanationId,
  });

  final String scenario;
  final NotificationType type;
  final NotificationPriority priority;
  final String title;
  final String message;
  final String? explanationId;
}
