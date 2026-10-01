import '../../core/state/async_controller.dart';
import '../../models/device.dart';
import '../../models/home_setup.dart';
import '../../models/room.dart';
import '../../models/user_preferences.dart';
import '../../repositories/hestia_repository.dart';

/// 설정 화면에서 현재 설정을 읽고 부분 저장한다.
class SetupController extends AsyncController<HomeSetup> {
  SetupController(this._repository);

  final HestiaRepository _repository;

  HomeSetup get setup =>
      data ??
      const HomeSetup(
        rooms: [],
        devices: [],
        preferences: UserPreferences(),
      );

  @override
  Future<HomeSetup> fetch() async {
    final setup = await _repository.getSetup();
    return setup ?? this.setup;
  }

  /// 공간을 바꾼다. 없어진 공간에 있던 가전은 위치 미지정이 된다.
  Future<void> saveRooms(Set<String> roomIds) async {
    final rooms = [
      for (final t in RoomTemplate.catalog)
        if (roomIds.contains(t.id)) t.toRoom(),
    ];
    final devices = [
      for (final d in setup.devices)
        roomIds.contains(d.roomId) ? d : d.copyWith(roomId: ''),
    ];
    await _save(HomeSetup(
      rooms: rooms,
      devices: devices,
      preferences: setup.preferences,
    ));
  }

  /// 가전 구성과 위치를 바꾼다. 기존 가전은 id와 상태를 유지한다.
  Future<void> saveDevices(Map<DeviceType, String> placements) async {
    final existing = {for (final d in setup.devices) d.type: d};
    final roomNames = {for (final r in setup.rooms) r.id: r.name};
    final devices = [
      for (final type in DeviceType.selectable)
        if (placements.containsKey(type))
          (existing[type] ??
                  Device(
                    id: '${type.wireName.toLowerCase().replaceAll('_', '-')}-01',
                    name: type.label,
                    type: type,
                    roomId: '',
                  ))
              .copyWith(
            roomId: placements[type],
            name: '${roomNames[placements[type]] ?? ''} ${type.label}'.trim(),
          ),
    ];
    await _save(HomeSetup(
      rooms: setup.rooms,
      devices: devices,
      preferences: setup.preferences,
    ));
  }

  Future<void> savePreferences(UserPreferences preferences) async {
    await _repository.savePreferences(preferences);
    setData(HomeSetup(
      rooms: setup.rooms,
      devices: setup.devices,
      preferences: preferences,
    ));
  }

  Future<void> _save(HomeSetup value) async {
    await _repository.saveSetup(value);
    await load();
  }
}
