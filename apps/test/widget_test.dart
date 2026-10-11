import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:hestia_flutter_test/app/app.dart';
import 'package:hestia_flutter_test/models/home_setup.dart';
import 'package:hestia_flutter_test/models/room.dart';
import 'package:hestia_flutter_test/models/user_preferences.dart';
import 'package:hestia_flutter_test/repositories/mock_hestia_repository.dart';

Future<void> _boot(WidgetTester tester, MockHestiaRepository repo) async {
  await tester.pumpWidget(HestiaApp(repository: repo));
  await tester.pump(const Duration(seconds: 1));
  await tester.pumpAndSettle();
}

Future<void> _tap(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('설정이 없으면 Splash 다음 Welcome 화면을 보여준다', (tester) async {
    await _boot(tester, MockHestiaRepository(latency: Duration.zero));
    expect(find.text('시작하기'), findsOneWidget);
  });

  testWidgets('시연 흐름: 최초 설정 → 홈 → 알림 상세 → 확인(SEEN) → 판단 과정',
      (tester) async {
    final repo = MockHestiaRepository(latency: Duration.zero);
    await _boot(tester, repo);

    // 최초 설정 (기본 선택값 사용)
    await _tap(tester, find.text('시작하기'));
    expect(find.text('어떤 공간을 사용하고 있나요?'), findsOneWidget);
    await _tap(tester, find.text('다음'));
    expect(find.text('사용 중인 가전을 선택해주세요'), findsOneWidget);
    await _tap(tester, find.text('다음'));
    expect(find.text('가전이 어디에 있나요?'), findsOneWidget);
    await _tap(tester, find.text('다음'));
    expect(find.text('알림을 어떻게 받을까요?'), findsOneWidget);
    await _tap(tester, find.text('설정 완료'));
    expect(find.text('설정이 완료되었습니다'), findsOneWidget);
    await _tap(tester, find.text('HESTIA 시작하기'));

    // 홈: Context Engine이 준 상태와 가전 상태
    expect(find.text('집에 있음'), findsOneWidget);
    expect(find.text('식사 완료'), findsOneWidget);
    // 바깥 날씨 (Mock은 서울 고정. 가전 에어컨 24°C와 겹치지 않게 24.1°C)
    expect(find.text('바깥 날씨 · 서울'), findsOneWidget);
    expect(find.text('24.1°C'), findsOneWidget);
    // 가전 목록은 현재 상태·공간 아래에 있어 화면 밖일 수 있다. 스크롤해서 확인한다.
    await tester.scrollUntilVisible(
      find.text('24°C'),
      200,
      scrollable: find.descendant(
        of: find.byType(ListView),
        matching: find.byType(Scrollable),
      ),
    );
    expect(find.text('24°C'), findsOneWidget);

    // 알림 탭 → 상세
    await _tap(tester, find.text('알림'));
    await _tap(tester, find.text('식사 후 복약 시간입니다.'));
    expect(find.text('알림 상세'), findsOneWidget);
    expect(find.text('확인 전'), findsOneWidget);

    // 상세를 연 것만으로는 SEEN이 아니다
    final before = await repo.getNotifications();
    expect(before.firstWhere((n) => n.id == 'n-demo-001').seen, isFalse);

    await _tap(tester, find.widgetWithText(FilledButton, '확인'));
    expect(find.text('확인함'), findsOneWidget);
    final after = await repo.getNotifications();
    expect(after.firstWhere((n) => n.id == 'n-demo-001').seen, isTrue);

    // 판단 과정
    await _tap(tester, find.text('HESTIA 판단 과정 보기'));
    expect(find.text('판단 근거'), findsOneWidget);
    expect(find.text('주방 재실 감지'), findsOneWidget);
    expect(find.text('87%'), findsOneWidget);
  });

  testWidgets('서버 연결이 없으면 앱이 멈추지 않고 재시도를 안내한다', (tester) async {
    final repo = MockHestiaRepository(latency: Duration.zero)..setOffline(true);
    await _boot(tester, repo);
    expect(find.text('설정 정보를 불러오지 못했습니다'), findsOneWidget);

    repo.setOffline(false);
    await _tap(tester, find.text('다시 시도'));
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(find.text('시작하기'), findsOneWidget);
  });

  testWidgets('홈에서 복약 알림을 추가하면 홈 카드에 보인다', (tester) async {
    final repo = MockHestiaRepository(
      latency: Duration.zero,
      initialSetup: const HomeSetup(
        rooms: [Room(id: 'living', name: '거실')],
        devices: [],
        preferences: UserPreferences(),
      ),
    );
    await _boot(tester, repo);

    await _tap(tester, find.text('복약 알림 추가하기'));
    expect(find.text('무슨 약인가요?'), findsOneWidget);
    await _tap(tester, find.text('혈압약'));
    await _tap(tester, find.text('다음'));

    // 아침·저녁만 먹는 약은 정해진 시각으로 등록한다
    await _tap(tester, find.text('정해진 시각'));
    await _tap(tester, find.text('다음'));

    await _tap(tester, find.text('아침'));
    await _tap(tester, find.text('저녁'));
    await _tap(tester, find.text('다음'));

    await _tap(tester, find.text('30일'));
    await _tap(tester, find.text('다음'));

    await _tap(tester, find.text('예'));
    await _tap(tester, find.text('다음'));

    expect(find.text('이대로 저장할까요?'), findsOneWidget);
    await _tap(tester, find.text('저장'));

    expect(find.text('복약 알림 추가하기'), findsNothing);
    expect(find.text('혈압약'), findsOneWidget);
    expect(find.text('08:00 · 18:00'), findsOneWidget);
    expect(find.text('30일 남음'), findsOneWidget);
  });
}
