import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';

import 'package:hestia_flutter_test/core/network/hestia_exception.dart';
import 'package:hestia_flutter_test/core/utils/display_labels.dart';
import 'package:hestia_flutter_test/models/context_state.dart';
import 'package:hestia_flutter_test/models/device.dart';
import 'package:hestia_flutter_test/models/notification_item.dart';
import 'package:hestia_flutter_test/models/user_preferences.dart';
import 'package:hestia_flutter_test/repositories/api_hestia_repository.dart';
import 'package:hestia_flutter_test/services/api_client.dart';

import 'support/fake_api.dart';

const notificationJson = {
  'id': 'n-001',
  'scenario': 'MEDICATION_PROMPT',
  'type': 'REMINDER',
  'priority': 'normal',
  'title': '복약 시간입니다',
  'message': '식사 후 복약 시간을 확인해주세요.',
  'createdAt': '2026-09-30T19:30:00+09:00',
  'roomId': 'living',
  'explanationId': '3',
  'delivered': true,
  'seen': false,
};

void main() {
  late FakeApi api;
  late ApiHestiaRepository repo;

  setUp(() async {
    api = FakeApi();
    await api.start();
    repo = ApiHestiaRepository(
      ApiClient(baseUrl: 'http://127.0.0.1:${api.port}'),
      pollInterval: const Duration(milliseconds: 50),
    );
  });

  tearDown(() async {
    repo.dispose();
    await api.stop();
  });

  test('설정 전(404)이면 getSetup은 null', () async {
    expect(await repo.getSetup(), isNull);
  });

  test('FastAPI 설정 응답을 모델로 바꾼다', () async {
    api.routes['GET /api/v1/setup'] = (200, {
      'rooms': [
        {'id': 'living', 'name': '거실', 'roles': ['LIVING']},
      ],
      'devices': [
        {
          'id': 'air-conditioner-01',
          'name': '거실 에어컨',
          'type': 'AIR_CONDITIONER',
          'roomId': 'living',
          'status': {'state': 'ON', 'mode': 'COOL', 'temp_set': 24, 'targetTemp': 24},
          'online': true,
          'virtualId': 'vd-03',
          'lastSeenAt': '2026-10-02T10:00:00+09:00',
        },
      ],
      'preferences': {
        'notificationsEnabled': true,
        'quietHours': {'enabled': true, 'start': '23:00', 'end': '06:30'},
        'sensitivity': 'high',
        'safety': {'enabled': true, 'emergencyContact': null},
      },
    });

    final setup = (await repo.getSetup())!;
    expect(setup.rooms.single.roles, ['LIVING']);
    final ac = setup.devices.single;
    expect(ac.type, DeviceType.airConditioner);
    expect(ac.status.attributes['targetTemp'], 24);
    expect(DeviceLabels.status(ac), '24°C');
    expect(setup.preferences.quietHours.start, const DayTime(23, 0));
    expect(setup.preferences.sensitivity, NotificationSensitivity.high);
  });

  test('presence는 state 없이 area로 표시한다', () async {
    api.routes['GET /api/v1/context/current'] = (200, {
      'activity': {
        'state': 'EATING',
        'since': 1790760000,
        'confidence': 0.87,
        'factors': {'area': 'kitchen', 'presence': true},
      },
      'presence': {
        'area': 'kitchen',
        'areas': {'kitchen': true},
        'confidence': 0.87,
      },
    });

    final context = await repo.getCurrentContext();
    final presence = context[ContextName.presence]!;
    expect(presence.state, ContextState.unknown);
    expect(presence.area, 'kitchen');
    expect(ContextLabels.describe(presence), '주방에 있음');

    final activity = context[ContextName.activity]!;
    expect(ContextLabels.describe(activity), '식사 중');
    expect(activity.since, DateTime.fromMillisecondsSinceEpoch(1790760000 * 1000));
  });

  test('알림 시각은 로컬 시각으로 바꾼다', () async {
    api.routes['GET /api/v1/notifications'] = (200, [notificationJson]);
    final n = (await repo.getNotifications()).single;
    expect(n.createdAt.isUtc, isFalse);
    expect(n.createdAt, DateTime.parse('2026-09-30T19:30:00+09:00'));
    expect(n.explanationId, '3');
  });

  test('확인(SEEN)은 ackType을 담아 POST한다', () async {
    api.routes['POST /api/v1/notifications/n-001/ack'] =
        (200, {'success': true, 'forwarded': true});
    await repo.acknowledgeNotification('n-001');

    final (method, path, body) = api.requests.single;
    expect(method, 'POST');
    expect(path, '/api/v1/notifications/n-001/ack');
    expect(jsonDecode(body), {'ackType': 'SEEN'});
  });

  test('판단 근거: 없으면 null, 있으면 label을 쓴다', () async {
    expect(await repo.getLatestExplanation(), isNull);
    expect(await repo.getExplanation('999'), isNull);

    api.routes['GET /api/v1/explanations/3'] = (200, {
      'id': '3',
      'contextName': 'activity',
      'state': 'MEAL_DONE',
      'confidence': 0.87,
      'factors': [
        {'key': 'area', 'value': 'kitchen', 'label': '주방 재실 감지', 'satisfied': true},
      ],
      'action': "'복약 시간입니다' 알림을 보냈습니다.",
      'createdAt': '2026-09-30T19:30:00+09:00',
    });
    final e = (await repo.getExplanation('3'))!;
    expect(e.factors.single.label, '주방 재실 감지');
    expect(e.action, contains('복약'));
  });

  test('설정 저장은 Flutter 모델 JSON 그대로 PUT한다', () async {
    api.routes['PUT /api/v1/preferences'] = (200, {});
    const prefs = UserPreferences(sensitivity: NotificationSensitivity.low);
    await repo.savePreferences(prefs);
    expect(jsonDecode(api.requests.single.$3), prefs.toJson());
  });

  test('서버 오류는 detail을 메시지로 쓴다', () async {
    api.routes['GET /api/v1/devices'] = (500, {'detail': 'boom'});
    await expectLater(
      repo.getDevices(),
      throwsA(isA<HestiaHttpException>()
          .having((e) => e.statusCode, 'statusCode', 500)
          .having((e) => e.message, 'message', 'boom')),
    );

    // FastAPI 검증 오류(422)는 detail이 목록이라 상태 코드로 안내한다.
    api.routes['PUT /api/v1/preferences'] = (422, {'detail': [{'msg': 'bad'}]});
    await expectLater(
      repo.savePreferences(const UserPreferences()),
      throwsA(isA<HestiaHttpException>()
          .having((e) => e.message, 'message', 'HTTP 422')),
    );
  });

  test('서버가 없으면 HestiaOfflineException', () async {
    await api.stop();
    await expectLater(repo.getRooms(), throwsA(isA<HestiaOfflineException>()));
  });

  test('응답 형식이 다르면 HestiaException', () async {
    api.routes['GET /api/v1/rooms'] = (200, {'not': 'a list'});
    await expectLater(repo.getRooms(), throwsA(isA<HestiaException>()));
  });

  test('주기 조회로 새로 생긴 알림만 내보낸다', () async {
    api.routes['GET /api/v1/notifications'] = (200, [notificationJson]);
    final received = <HestiaNotification>[];
    final sub = repo.watchNotifications().listen(received.add);

    await Future<void>.delayed(const Duration(milliseconds: 120));
    expect(received, isEmpty); // 처음 목록은 기준으로만 쓴다

    api.routes['GET /api/v1/notifications'] = (200, [
      {...notificationJson, 'id': 'n-002', 'title': '세탁이 완료되었습니다'},
      notificationJson,
    ]);
    await Future<void>.delayed(const Duration(milliseconds: 150));
    expect(received.map((n) => n.id), ['n-002']);

    await sub.cancel();
  });
}
