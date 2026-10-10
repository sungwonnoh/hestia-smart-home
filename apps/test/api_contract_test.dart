import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:hestia_flutter_test/core/utils/display_labels.dart';
import 'package:hestia_flutter_test/models/context_state.dart';
import 'package:hestia_flutter_test/models/device.dart';
import 'package:hestia_flutter_test/models/home_setup.dart';
import 'package:hestia_flutter_test/models/medication.dart';
import 'package:hestia_flutter_test/models/room.dart';
import 'package:hestia_flutter_test/models/user_preferences.dart';
import 'package:hestia_flutter_test/models/weather.dart';
import 'package:hestia_flutter_test/repositories/api_hestia_repository.dart';
import 'package:hestia_flutter_test/services/api_client.dart';

import 'support/fake_api.dart';

/// Flutter ↔ FastAPI 계약 테스트.
///
/// test/fixtures/api/responses/는 실제 FastAPI가 만든 응답이다
/// (`backend/scripts/export_api_fixtures.py`). 손으로 쓴 JSON이 아니므로
/// 필드명·enum·시각 형식이 어긋나면 여기서 드러난다.
///
/// 모델은 모르는 enum 값을 unknown/기본값으로 받아 조용히 넘어가므로,
/// 파싱 성공만 보지 않고 원본 값이 그대로 살아남았는지 비교한다.
///
/// test/fixtures/api/requests/는 이 앱이 보내는 JSON이다.
/// `backend/tests/test_contract.py`가 같은 파일을 서버에 보내 저장되는지 확인한다.
const _fixtures = 'test/fixtures/api';

Object? _load(String path) =>
    jsonDecode(File('$_fixtures/$path').readAsStringSync());

Map<String, dynamic> _response(String name) =>
    _load('responses/$name.json') as Map<String, dynamic>;

List<Map<String, dynamic>> _responseList(String name) =>
    (_load('responses/$name.json') as List).cast<Map<String, dynamic>>();

void main() {
  late FakeApi api;
  late ApiHestiaRepository repo;

  final rawNotifications = _responseList('notifications');
  final rawDevices = _responseList('devices');
  final rawSetup = _response('setup');
  final rawPreferences = _response('preferences');
  final rawContext = _response('context_current');
  final rawExplanation = _response('explanation_by_id');
  final rawMedications = _responseList('medications');
  final rawWeather = _response('weather');
  final notificationId = rawNotifications.first['id'] as String;
  final explanationId = rawExplanation['id'] as String;

  setUp(() async {
    api = FakeApi();
    await api.start();
    api.routes
      ..['GET /api/v1/setup'] = (200, rawSetup)
      ..['GET /api/v1/rooms'] = (200, _load('responses/rooms.json'))
      ..['GET /api/v1/devices'] = (200, rawDevices)
      ..['GET /api/v1/context/current'] = (200, rawContext)
      ..['GET /api/v1/notifications'] = (200, rawNotifications)
      ..['GET /api/v1/explanations/latest'] =
          (200, _response('explanations_latest'))
      ..['GET /api/v1/explanations/$explanationId'] = (200, rawExplanation)
      ..['GET /api/v1/preferences'] = (200, rawPreferences)
      ..['POST /api/v1/notifications/$notificationId/ack'] =
          (200, _response('ack'))
      ..['PUT /api/v1/setup'] = (200, rawSetup)
      ..['PUT /api/v1/preferences'] = (200, rawPreferences)
      ..['GET /api/v1/medications'] = (200, rawMedications)
      ..['POST /api/v1/medications'] = (201, rawMedications.first)
      ..['GET /api/v1/weather'] = (200, rawWeather);
    repo = ApiHestiaRepository(
      ApiClient(baseUrl: 'http://127.0.0.1:${api.port}'),
    );
  });

  tearDown(() async {
    repo.dispose();
    await api.stop();
  });

  group('FastAPI 응답 → Flutter 모델', () {
    test('setup: 공간·가전·설정 값이 그대로 들어온다', () async {
      final setup = (await repo.getSetup())!;
      final rooms = (rawSetup['rooms'] as List).cast<Map<String, dynamic>>();
      expect(setup.rooms.map((r) => r.id), rooms.map((r) => r['id']));
      expect(setup.rooms.map((r) => r.roles), rooms.map((r) => r['roles']));

      final devices =
          (rawSetup['devices'] as List).cast<Map<String, dynamic>>();
      expect(setup.devices.map((d) => d.type.wireName),
          devices.map((d) => d['type']));
      expect(setup.preferences.toJson(),
          _withoutNulls(rawSetup['preferences'] as Map<String, dynamic>));
    });

    test('rooms', () async {
      final rooms = await repo.getRooms();
      expect(rooms.map((r) => r.toJson()), _load('responses/rooms.json'));
    });

    test('devices: 종류가 unknown으로 떨어지지 않고 상태가 표시된다', () async {
      final devices = await repo.getDevices();
      expect(devices, hasLength(rawDevices.length));
      for (final (i, d) in devices.indexed) {
        final raw = rawDevices[i];
        expect(d.type, isNot(DeviceType.unknown), reason: '${raw['type']}');
        expect(d.type.wireName, raw['type']);
        expect(d.roomId, raw['roomId']);
        expect(d.online, raw['online']);
        expect(d.status.state, (raw['status'] as Map)['state']);
        expect(d.status.state, isNot('UNKNOWN'), reason: d.id);
        expect(DeviceLabels.status(d), isNotEmpty);
      }

      final ac = devices.firstWhere((d) => d.type == DeviceType.airConditioner);
      expect(DeviceLabels.status(ac), '${ac.status.attributes['targetTemp']}°C');
    });

    test('context: 받은 context가 모두 들어오고 화면 문구로 바뀐다', () async {
      final context = await repo.getCurrentContext();
      expect(context.states.keys.toSet(), rawContext.keys.toSet());

      for (final s in context.ordered) {
        final raw = rawContext[s.name] as Map<String, dynamic>;
        expect(s.state, raw['state'] ?? ContextState.unknown, reason: s.name);
        expect(s.confidence, raw['confidence'], reason: s.name);
        if (raw['since'] != null) expect(s.since, isNotNull, reason: s.name);

        // 사전에 없는 state면 원문이 그대로 화면에 나온다.
        final text = ContextLabels.describe(s);
        if (s.name == ContextName.presence) {
          expect(s.area, raw['area']);
          expect(text, '${RoomLabels.name(s.area!)}에 있음');
        } else {
          expect(text, isNot(s.state), reason: '${s.name}=${s.state} 문구 없음');
        }
      }
    });

    test('notifications: type·priority·시각·판단 근거 id', () async {
      final items = await repo.getNotifications();
      expect(items, hasLength(rawNotifications.length));
      for (final (i, n) in items.indexed) {
        final raw = rawNotifications[i];
        expect(n.id, raw['id']);
        expect(n.type.wireName, raw['type']);
        expect(n.priority.wireName, raw['priority']);
        expect(n.title, raw['title']);
        expect(n.message, raw['message']);
        // DateTime ==는 UTC/로컬 여부까지 비교하므로 같은 시각인지만 본다.
        expect(
            n.createdAt
                .isAtSameMomentAs(DateTime.parse(raw['createdAt'] as String)),
            isTrue);
        expect(n.createdAt.isUtc, isFalse);
        expect(n.roomId, raw['roomId']);
        expect(n.explanationId, raw['explanationId']);
        expect(n.seen, raw['seen']);
      }
    });

    test('explanation: 판단 id로 판단 근거를 연다', () async {
      // 알림 explanationId는 decision_id ↔ notify_id 연결이 확정될 때까지 null이다.
      // 앱은 null이면 최신 판단 근거를 보여준다.
      expect(rawNotifications.every((n) => n['explanationId'] == null), isTrue);

      final e = (await repo.getExplanation(explanationId))!;
      expect(e.id, rawExplanation['id']);
      expect(e.contextName, rawExplanation['contextName']);
      expect(e.state, rawExplanation['state']);
      expect(e.action, rawExplanation['action']);
      expect(e.createdAt, isNotNull);

      final factors =
          (rawExplanation['factors'] as List).cast<Map<String, dynamic>>();
      expect(e.factors.map((f) => f.label), factors.map((f) => f['label']));
      expect(e.factors.every((f) => f.label.isNotEmpty), isTrue);

      final latest = (await repo.getLatestExplanation())!;
      expect(latest.factors, isNotEmpty);
    });

    test('preferences', () async {
      final prefs = await repo.getPreferences();
      expect(prefs.toJson(), _withoutNulls(rawPreferences));
    });

    test('medications: 시점·식사 기준·기간이 그대로 들어온다', () async {
      final items = await repo.getMedications();
      expect(items, hasLength(rawMedications.length));
      for (final (i, m) in items.indexed) {
        final raw = rawMedications[i];
        expect(m.id, raw['id']);
        expect(m.name, raw['name']);
        // 모르는 값이 조용히 빠지지 않았는지 원본과 비교한다.
        expect(m.slots.map((s) => s.wireName), raw['slots']);
        expect(m.mealTiming?.wireName, raw['mealTiming']);
        expect(m.days, raw['days']);
        expect(Medication.formatDate(m.startDate!), raw['startDate']);
        expect(Medication.formatDate(m.endDate!), raw['endDate']);
        expect(m.refillRequired, raw['refillRequired']);
      }
    });

    test('weather: 값·강수형태·특보가 그대로 들어온다', () async {
      final w = (await repo.getWeather())!;
      expect(w.locationName, rawWeather['locationName']);
      expect(w.observedAt,
          DateTime.parse(rawWeather['observedAt'] as String).toLocal());
      expect(w.temperatureC, rawWeather['temperatureC']);
      expect(w.humidityPct, rawWeather['humidityPct']);
      expect(w.precipitationMm, rawWeather['precipitationMm']);
      // 모르는 값이 unknown으로 떨어지지 않았는지 원본과 비교한다.
      expect(w.precipType, isNot(PrecipType.unknown));
      expect(w.precipType.wireName, rawWeather['precipType']);
      expect(w.windSpeedMs, rawWeather['windSpeedMs']);
      expect(w.stale, rawWeather['stale']);
      expect(w.warnings!.map((x) => x.toJson()), rawWeather['warnings']);
      expect(WeatherLabels.details(w), isNotEmpty);
    });

    test('weather: 아직 못 받았으면(404) null', () async {
      api.routes.remove('GET /api/v1/weather');
      expect(await repo.getWeather(), isNull);
    });

    test('ack: 응답을 받아도 예외가 없다', () async {
      await repo.acknowledgeNotification(notificationId);
      expect(api.requests.last.$2, '/api/v1/notifications/$notificationId/ack');
    });
  });

  group('Flutter 요청 → FastAPI (requests/ fixture)', () {
    test('최초 설정 저장 JSON', () async {
      // 온보딩이 만드는 형태와 같게 만든다. (id: 종류-01, status/online은 보내지 않는다)
      const living = Room(id: 'living', name: '거실', roles: [RoomRole.living]);
      const kitchen = Room(id: 'kitchen', name: '주방', roles: [RoomRole.meal]);
      const utility =
          Room(id: 'utility', name: '다용도실', roles: [RoomRole.laundry]);
      const setup = HomeSetup(
        rooms: [living, kitchen, utility],
        devices: [
          Device(id: 'tv-01', name: '거실 TV', type: DeviceType.tv, roomId: 'living'),
          Device(id: 'light-01', name: '거실 조명', type: DeviceType.light, roomId: 'living'),
          Device(
              id: 'air-conditioner-01',
              name: '거실 에어컨',
              type: DeviceType.airConditioner,
              roomId: 'living'),
          Device(
              id: 'refrigerator-01',
              name: '주방 냉장고',
              type: DeviceType.refrigerator,
              roomId: 'kitchen'),
          Device(
              id: 'washer-01',
              name: '다용도실 세탁기',
              type: DeviceType.washer,
              roomId: 'utility'),
        ],
        preferences: UserPreferences(
          quietHours: QuietHours(start: DayTime(22, 30), end: DayTime(7, 0)),
          sensitivity: NotificationSensitivity.high,
          safety: SafetyPreferences(emergencyContact: '010-1234-5678'),
        ),
      );

      await repo.saveSetup(setup);
      final (method, path, body) = api.requests.last;
      expect('$method $path', 'PUT /api/v1/setup');
      expect(jsonDecode(body), _load('requests/setup.json'));
    });

    test('설정 변경 JSON', () async {
      const prefs = UserPreferences(
        notificationsEnabled: false,
        quietHours: QuietHours(
            enabled: false, start: DayTime(23, 0), end: DayTime(6, 30)),
        sensitivity: NotificationSensitivity.low,
      );
      await repo.savePreferences(prefs);
      final (method, path, body) = api.requests.last;
      expect('$method $path', 'PUT /api/v1/preferences');
      expect(jsonDecode(body), _load('requests/preferences.json'));
    });

    test('복약 추가 JSON', () async {
      const draft = Medication(
        name: '혈압약',
        slots: [DoseSlot.breakfast, DoseSlot.dinner],
        mealTiming: MealTiming.afterMeal30,
        days: 30,
        refillRequired: true,
      );
      final saved = await repo.addMedication(draft);
      final (method, path, body) = api.requests.last;
      expect('$method $path', 'POST /api/v1/medications');
      expect(jsonDecode(body), _load('requests/medication.json'));
      expect(saved.id, rawMedications.first['id']);
    });

    test('확인(SEEN) JSON', () async {
      await repo.acknowledgeNotification(notificationId);
      expect(jsonDecode(api.requests.last.$3), _load('requests/ack_seen.json'));
    });
  });
}

/// 서버는 빈 값을 null로 주고, 앱 toJson은 키를 뺀다. 둘은 같은 뜻이다.
Map<String, dynamic> _withoutNulls(Map<String, dynamic> json) => {
      for (final e in json.entries)
        if (e.value != null)
          e.key: e.value is Map<String, dynamic>
              ? _withoutNulls(e.value as Map<String, dynamic>)
              : e.value,
    };
