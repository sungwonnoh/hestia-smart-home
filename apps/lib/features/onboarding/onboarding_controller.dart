import 'package:flutter/foundation.dart';

import '../../core/network/hestia_exception.dart';
import '../../models/device.dart';
import '../../models/home_setup.dart';
import '../../models/room.dart';
import '../../models/user_preferences.dart';
import '../../repositories/hestia_repository.dart';

enum OnboardingStep { welcome, rooms, devices, placement, preferences, done }

/// 최초 설정 입력 상태.
///
/// 사용자 상태(HOME, SLEEPING 등)는 받지 않는다. Context Engine이 판단한다.
class OnboardingController extends ChangeNotifier {
  OnboardingController(this._repository);

  final HestiaRepository _repository;

  OnboardingStep _step = OnboardingStep.welcome;
  final Set<String> _roomIds = {'living', 'bedroom', 'kitchen', 'entrance'};
  final Set<DeviceType> _devices = {
    DeviceType.tv,
    DeviceType.light,
    DeviceType.airConditioner,
    DeviceType.airPurifier,
    DeviceType.refrigerator,
  };
  final Map<DeviceType, String> _placements = {};
  UserPreferences _preferences = const UserPreferences();
  bool _saving = false;
  String? _error;

  OnboardingStep get step => _step;
  Set<String> get selectedRoomIds => Set.unmodifiable(_roomIds);
  Set<DeviceType> get selectedDevices => Set.unmodifiable(_devices);
  Map<DeviceType, String> get placements => Map.unmodifiable(_placements);
  UserPreferences get preferences => _preferences;
  bool get saving => _saving;
  String? get error => _error;

  /// 선택한 방. 카탈로그 순서를 유지한다.
  List<Room> get rooms => [
        for (final t in RoomTemplate.catalog)
          if (_roomIds.contains(t.id)) t.toRoom(),
      ];

  /// 선택한 가전. enum 순서를 유지한다.
  List<DeviceType> get devices =>
      DeviceType.selectable.where(_devices.contains).toList();

  bool get canProceed => switch (_step) {
        OnboardingStep.rooms => _roomIds.isNotEmpty,
        OnboardingStep.placement =>
          _devices.every((d) => _placements.containsKey(d)),
        _ => true,
      };

  void toggleRoom(String roomId) {
    if (!_roomIds.remove(roomId)) _roomIds.add(roomId);
    _placements.removeWhere((_, room) => !_roomIds.contains(room));
    notifyListeners();
  }

  void toggleDevice(DeviceType type) {
    if (!_devices.remove(type)) _devices.add(type);
    _placements.removeWhere((d, _) => !_devices.contains(d));
    notifyListeners();
  }

  void place(DeviceType type, String roomId) {
    _placements[type] = roomId;
    notifyListeners();
  }

  void updatePreferences(UserPreferences value) {
    _preferences = value;
    notifyListeners();
  }

  void next() {
    if (!canProceed) return;
    if (_step == OnboardingStep.devices) _fillDefaultPlacements();
    if (_step.index < OnboardingStep.preferences.index) {
      _step = OnboardingStep.values[_step.index + 1];
      _error = null;
      notifyListeners();
    }
  }

  void back() {
    if (_step == OnboardingStep.welcome || _step == OnboardingStep.done) return;
    _step = OnboardingStep.values[_step.index - 1];
    _error = null;
    notifyListeners();
  }

  HomeSetup buildSetup() {
    final roomsById = {for (final r in rooms) r.id: r};
    return HomeSetup(
      rooms: rooms,
      devices: [
        for (final type in devices)
          Device(
            id: '${type.wireName.toLowerCase().replaceAll('_', '-')}-01',
            name: '${roomsById[_placements[type]]?.name ?? ''} ${type.label}'
                .trim(),
            type: type,
            roomId: _placements[type] ?? '',
          ),
      ],
      preferences: _preferences,
    );
  }

  /// 저장에 성공하면 완료 단계로 넘어간다.
  Future<bool> complete() async {
    if (_saving) return false;
    _saving = true;
    _error = null;
    notifyListeners();
    try {
      await _repository.saveSetup(buildSetup());
      _step = OnboardingStep.done;
      return true;
    } on HestiaException catch (e) {
      _error = e.message;
      return false;
    } finally {
      _saving = false;
      notifyListeners();
    }
  }

  /// 입력 편의를 위한 기본 배치. 사용자가 바꿀 수 있다.
  void _fillDefaultPlacements() {
    final selected = rooms;
    if (selected.isEmpty) return;
    for (final type in _devices) {
      if (_placements.containsKey(type)) continue;
      final preferred = type.preferredRoomIds.firstWhere(
        _roomIds.contains,
        orElse: () => selected.first.id,
      );
      _placements[type] = preferred;
    }
  }
}
