import '../../core/state/async_controller.dart';
import '../../models/context_state.dart';
import '../../models/device.dart';
import '../../models/room.dart';
import '../../repositories/hestia_repository.dart';

class HomeData {
  const HomeData({
    required this.context,
    required this.rooms,
    required this.devices,
  });

  final HestiaContext context;
  final List<Room> rooms;
  final List<Device> devices;

  String roomName(String roomId) {
    for (final r in rooms) {
      if (r.id == roomId) return r.name;
    }
    return '미지정';
  }

  bool get hasSensorFault =>
      context.states.values.any((s) => s.state == 'SENSOR_FAULT');
}

class HomeController extends AsyncController<HomeData> {
  HomeController(this._repository);

  final HestiaRepository _repository;

  /// 가전 목록 필터. null이면 전체.
  String? _roomFilter;
  String? get roomFilter => _roomFilter;

  void selectRoom(String? roomId) {
    _roomFilter = _roomFilter == roomId ? null : roomId;
    notifyListeners();
  }

  List<Device> visibleDevices(HomeData data) => _roomFilter == null
      ? data.devices
      : data.devices.where((d) => d.roomId == _roomFilter).toList();

  @override
  Future<HomeData> fetch() async {
    final results = await Future.wait([
      _repository.getCurrentContext(),
      _repository.getRooms(),
      _repository.getDevices(),
    ]);
    return HomeData(
      context: results[0] as HestiaContext,
      rooms: results[1] as List<Room>,
      devices: results[2] as List<Device>,
    );
  }
}
