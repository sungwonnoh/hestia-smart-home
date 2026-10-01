import 'package:flutter_test/flutter_test.dart';

import 'package:hestia_flutter_test/app/notification_center.dart';
import 'package:hestia_flutter_test/core/network/hestia_exception.dart';
import 'package:hestia_flutter_test/core/state/async_controller.dart';
import 'package:hestia_flutter_test/features/onboarding/onboarding_controller.dart';
import 'package:hestia_flutter_test/models/device.dart';
import 'package:hestia_flutter_test/repositories/mock_hestia_repository.dart';

MockHestiaRepository newRepo() => MockHestiaRepository(latency: Duration.zero);

void main() {
  group('MockHestiaRepository', () {
    test('처음에는 설정이 없다', () async {
      expect(await newRepo().getSetup(), isNull);
    });

    test('SEEN ACK를 받으면 해당 알림만 확인 처리한다', () async {
      final repo = newRepo();
      await repo.acknowledgeNotification('n-demo-001');
      final items = await repo.getNotifications();
      expect(items.firstWhere((n) => n.id == 'n-demo-001').seen, isTrue);
      expect(items.firstWhere((n) => n.id == 'n-demo-002').seen, isFalse);
    });

    test('오프라인이면 HestiaOfflineException을 던진다', () async {
      final repo = newRepo()..setOffline(true);
      await expectLater(
        repo.getDevices(),
        throwsA(isA<HestiaOfflineException>()),
      );
    });
  });

  group('OnboardingController', () {
    test('가전 선택 후 다음 단계에서 기본 위치를 채운다', () {
      final c = OnboardingController(newRepo())
        ..next() // welcome → rooms
        ..next() // rooms → devices
        ..next(); // devices → placement
      expect(c.step, OnboardingStep.placement);
      expect(c.placements[DeviceType.tv], 'living');
      expect(c.placements[DeviceType.refrigerator], 'kitchen');
      expect(c.canProceed, isTrue);
    });

    test('공간을 빼면 그 공간에 둔 가전 배치가 지워진다', () {
      final c = OnboardingController(newRepo())
        ..next()
        ..next()
        ..next()
        ..toggleRoom('kitchen');
      expect(c.placements.containsKey(DeviceType.refrigerator), isFalse);
      expect(c.canProceed, isFalse);
    });

    test('공간이 하나도 없으면 다음으로 갈 수 없다', () {
      final c = OnboardingController(newRepo())..next();
      for (final id in [...c.selectedRoomIds]) {
        c.toggleRoom(id);
      }
      expect(c.canProceed, isFalse);
    });

    test('완료하면 Repository에 설정을 저장한다', () async {
      final repo = newRepo();
      final c = OnboardingController(repo)
        ..next()
        ..next()
        ..next()
        ..next();
      expect(await c.complete(), isTrue);
      expect(c.step, OnboardingStep.done);

      final setup = await repo.getSetup();
      expect(setup!.rooms.map((r) => r.id), contains('living'));
      final tv = setup.devices.firstWhere((d) => d.type == DeviceType.tv);
      expect(tv.roomId, 'living');
      expect(tv.name, '거실 TV');
    });
  });

  group('NotificationCenter', () {
    test('확인하면 미확인 수가 줄어든다', () async {
      final center = NotificationCenter(newRepo());
      await center.load();
      final before = center.unseenCount;
      await center.markSeen('n-demo-001');
      expect(center.unseenCount, before - 1);
      expect(center.byId('n-demo-001')!.seen, isTrue);
      center.dispose();
    });

    test('새 알림이 오면 맨 앞에 추가된다', () async {
      final repo = newRepo();
      final center = NotificationCenter(repo);
      await center.load();
      final n = repo.pushDemoNotification();
      await Future<void>.delayed(Duration.zero);
      expect(center.items.first.id, n.id);
      expect(center.items.first.seen, isFalse);
      center.dispose();
    });

    test('오프라인이어도 마지막 목록을 유지한다', () async {
      final repo = newRepo();
      final center = NotificationCenter(repo);
      await center.load();
      final count = center.items.length;
      repo.setOffline(true);
      await center.load();
      expect(center.status, LoadStatus.offline);
      expect(center.items.length, count);
      center.dispose();
    });
  });
}
