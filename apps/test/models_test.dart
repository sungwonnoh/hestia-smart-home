import 'package:flutter_test/flutter_test.dart';

import 'package:hestia_flutter_test/models/context_state.dart';
import 'package:hestia_flutter_test/models/device.dart';
import 'package:hestia_flutter_test/models/explanation.dart';
import 'package:hestia_flutter_test/models/home_setup.dart';
import 'package:hestia_flutter_test/models/notification_item.dart';
import 'package:hestia_flutter_test/models/room.dart';
import 'package:hestia_flutter_test/models/user_preferences.dart';

void main() {
  group('Device', () {
    test('모르는 가전 종류는 unknown으로 받는다', () {
      final d = Device.fromJson({
        'id': 'x-01',
        'name': '무언가',
        'type': 'TOASTER',
        'roomId': 'kitchen',
        'status': 'ON',
        'online': true,
      });
      expect(d.type, DeviceType.unknown);
      expect(d.status.state, 'ON');
    });

    test('상태 객체의 추가 값은 attributes로 보관한다', () {
      final status = DeviceStatus.fromJson({'state': 'ON', 'targetTemp': 24});
      expect(status.state, 'ON');
      expect(status.attributes, {'targetTemp': 24});
      expect(status.toJson(), {'state': 'ON', 'targetTemp': 24});
    });
  });

  test('명세 예시 Current Context 응답을 파싱한다', () {
    final ctx = HestiaContext.fromJson({
      'activity': {
        'state': 'EATING',
        'since': 1790760000,
        'confidence': 0.87,
        'factors': {'area': 'kitchen', 'presence': true},
      },
      'away': {'state': 'HOME'},
      'occupancy': {'state': 'SINGLE'},
    });
    expect(ctx[ContextName.activity]!.confidence, 0.87);
    expect(ctx[ContextName.activity]!.factors['area'], 'kitchen');
    expect(
      ctx.ordered.map((s) => s.name),
      [ContextName.away, ContextName.activity, ContextName.occupancy],
    );
  });

  test('명세 예시 Notification 응답을 파싱한다', () {
    final n = HestiaNotification.fromJson({
      'id': 'n-20260930-001',
      'scenario': 'MEDICATION_PROMPT',
      'priority': 'normal',
      'title': '복약 시간입니다',
      'message': '식사 후 복약 시간을 확인해주세요.',
      'createdAt': '2026-09-30T19:30:00+09:00',
      'delivered': true,
      'seen': false,
    });
    expect(n.priority, NotificationPriority.normal);
    expect(n.delivered, isTrue);
    expect(n.seen, isFalse);
    expect(n.isSafety, isFalse);
    expect(n.copyWith(seen: true).seen, isTrue);
  });

  test('Explanation factors는 List와 Map을 모두 받는다', () {
    final fromList = Explanation.fromJson({
      'contextName': 'activity',
      'state': 'MEAL_DONE',
      'factors': [
        '주방 재실 감지',
        {'label': '식사 시간대와 일치', 'satisfied': false},
      ],
    });
    expect(fromList.factors.map((f) => f.label),
        ['주방 재실 감지', '식사 시간대와 일치']);
    expect(fromList.factors[1].satisfied, isFalse);

    final fromMap = Explanation.fromJson({
      'contextName': 'activity',
      'state': 'EATING',
      'factors': {'area': 'kitchen'},
    });
    expect(fromMap.factors.single.label, 'area: kitchen');
  });

  test('HomeSetup JSON 왕복', () {
    const setup = HomeSetup(
      rooms: [Room(id: 'studio', name: '원룸', roles: ['LIVING', 'SLEEP'])],
      devices: [
        Device(
          id: 'tv-01',
          name: '원룸 TV',
          type: DeviceType.tv,
          roomId: 'studio',
        ),
      ],
      preferences: UserPreferences(
        quietHours: QuietHours(start: DayTime(23, 30), end: DayTime(6, 0)),
        safety: SafetyPreferences(emergencyContact: '010-0000-0000'),
      ),
    );
    final restored = HomeSetup.fromJson(setup.toJson());
    expect(restored.rooms.single.roles, ['LIVING', 'SLEEP']);
    expect(restored.devices.single.type, DeviceType.tv);
    expect(restored.preferences.quietHours.start, const DayTime(23, 30));
    expect(restored.preferences.safety.emergencyContact, '010-0000-0000');
    expect(restored.preferences.safety.enabled, isTrue);
  });

  test('원룸 템플릿은 여러 role을 가진다', () {
    final studio = RoomTemplate.byId('studio')!.toRoom();
    expect(studio.roles, containsAll(['LIVING', 'SLEEP', 'MEAL']));
  });
}
