import 'dart:async';

import '../core/constants/app_config.dart';
import '../core/network/hestia_exception.dart';
import '../models/context_state.dart';
import '../models/device.dart';
import '../models/explanation.dart';
import '../models/home_setup.dart';
import '../models/medication.dart';
import '../models/notification_item.dart';
import '../models/room.dart';
import '../models/user_preferences.dart';
import '../services/api_client.dart';
import 'hestia_repository.dart';

/// HESTIA FastAPI(`/api/v1`) 기반 Repository.
///
/// Mock과 같은 인터페이스를 구현하므로 Widget/Controller는 바뀌지 않는다.
/// 새 알림 실시간 수신(Phase 3) 전까지는 목록을 주기적으로 다시 읽어 흉내 낸다.
class ApiHestiaRepository implements HestiaRepository {
  ApiHestiaRepository(
    this._client, {
    this.pollInterval = const Duration(seconds: 5),
  });

  factory ApiHestiaRepository.fromConfig() => ApiHestiaRepository(
        ApiClient(
          baseUrl: AppConfig.apiBaseUrl,
          timeout: AppConfig.requestTimeout,
        ),
        pollInterval: AppConfig.notificationPollInterval,
      );

  static const _api = 'api/v1';

  final ApiClient _client;
  final Duration pollInterval;

  // ---------------------------------------------------------------- setup

  @override
  Future<HomeSetup?> getSetup() => _orNull(() async =>
      HomeSetup.fromJson(_map(await _client.get('$_api/setup'))));

  /// devices는 DeviceIn 형태로 보낸다. status/online은 서버가 MQTT로 채우는 값이라 빼고,
  /// virtualId는 비워 두면 서버가 같은 종류·공간의 장치에 연결한다.
  @override
  Future<void> saveSetup(HomeSetup setup) async {
    await _client.put('$_api/setup', {
      ...setup.toJson(),
      'devices': [
        for (final d in setup.devices)
          {
            'id': d.id,
            'name': d.name,
            'type': d.type.wireName,
            'roomId': d.roomId,
          },
      ],
    });
  }

  // ---------------------------------------------------------------- home

  @override
  Future<List<Room>> getRooms() async =>
      _list(await _client.get('$_api/rooms')).map(Room.fromJson).toList();

  @override
  Future<List<Device>> getDevices() async =>
      _list(await _client.get('$_api/devices')).map(Device.fromJson).toList();

  @override
  Future<HestiaContext> getCurrentContext() async {
    final json = _map(await _client.get('$_api/context/current'));
    final context = HestiaContext.fromJson(json);
    return HestiaContext(states: context.states, updatedAt: DateTime.now());
  }

  // ---------------------------------------------------------------- notify

  @override
  Future<List<HestiaNotification>> getNotifications() async =>
      _list(await _client.get('$_api/notifications'))
          .map(HestiaNotification.fromJson)
          .toList();

  @override
  Future<void> acknowledgeNotification(
    String notificationId, {
    AckType ackType = AckType.seen,
  }) async {
    await _client.post(
      '$_api/notifications/${Uri.encodeComponent(notificationId)}/ack',
      {'ackType': ackType.wireName},
    );
  }

  // ---------------------------------------------------------------- explain

  @override
  Future<Explanation?> getLatestExplanation() => _orNull(() async =>
      Explanation.fromJson(_map(await _client.get('$_api/explanations/latest'))));

  @override
  Future<Explanation?> getExplanation(String explanationId) =>
      _orNull(() async => Explanation.fromJson(_map(await _client
          .get('$_api/explanations/${Uri.encodeComponent(explanationId)}'))));

  // ---------------------------------------------------------------- prefs

  @override
  Future<UserPreferences> getPreferences() async =>
      UserPreferences.fromJson(_map(await _client.get('$_api/preferences')));

  @override
  Future<void> savePreferences(UserPreferences preferences) async {
    await _client.put('$_api/preferences', preferences.toJson());
  }

  // ---------------------------------------------------------------- medication

  @override
  Future<List<Medication>> getMedications() async =>
      _list(await _client.get('$_api/medications'))
          .map(Medication.fromJson)
          .toList();

  @override
  Future<Medication> addMedication(Medication medication) async =>
      Medication.fromJson(
          _map(await _client.post('$_api/medications', medication.toJson())));

  @override
  Future<Medication> updateMedication(Medication medication) async =>
      Medication.fromJson(_map(await _client.put(
          '$_api/medications/${Uri.encodeComponent(medication.id)}',
          medication.toJson())));

  @override
  Future<void> deleteMedication(String medicationId) async {
    await _client
        .delete('$_api/medications/${Uri.encodeComponent(medicationId)}');
  }

  // ---------------------------------------------------------------- polling

  late final StreamController<HestiaNotification> _incoming =
      StreamController<HestiaNotification>.broadcast(
    onListen: _startPolling,
    onCancel: _stopPolling,
  );
  Timer? _timer;
  Set<String>? _knownIds;
  bool _polling = false;

  @override
  Stream<HestiaNotification> watchNotifications() => _incoming.stream;

  void _startPolling() {
    _timer ??= Timer.periodic(pollInterval, (_) => _poll());
    _poll();
  }

  void _stopPolling() {
    _timer?.cancel();
    _timer = null;
  }

  /// 처음 읽은 목록은 기준으로만 쓰고, 이후 새로 생긴 id만 내보낸다.
  Future<void> _poll() async {
    if (_polling) return;
    _polling = true;
    try {
      final items = await getNotifications();
      final known = _knownIds;
      _knownIds = {...?known, ...items.map((n) => n.id)};
      if (known == null) return;
      for (final n in items.reversed) {
        if (!known.contains(n.id) && !_incoming.isClosed) _incoming.add(n);
      }
    } on HestiaException {
      // 연결이 끊겼다. 다음 주기에 다시 시도한다.
    } finally {
      _polling = false;
    }
  }

  void dispose() {
    _stopPolling();
    _incoming.close();
    _client.close();
  }

  // ---------------------------------------------------------------- helpers

  /// 404는 "아직 없음"으로 본다 (설정 전, 판단 없음).
  static Future<T?> _orNull<T>(Future<T> Function() call) async {
    try {
      return await call();
    } on HestiaHttpException catch (e) {
      if (e.isNotFound) return null;
      rethrow;
    }
  }

  static Map<String, dynamic> _map(Object? json) {
    if (json is Map<String, dynamic>) return json;
    throw const HestiaException('서버 응답 형식이 올바르지 않습니다.');
  }

  static List<Map<String, dynamic>> _list(Object? json) {
    if (json is List && json.every((e) => e is Map<String, dynamic>)) {
      return json.cast<Map<String, dynamic>>();
    }
    throw const HestiaException('서버 응답 형식이 올바르지 않습니다.');
  }
}
