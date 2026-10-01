import 'package:flutter_test/flutter_test.dart';

import 'package:hestia_flutter_test/app/app.dart';
import 'package:hestia_flutter_test/repositories/mock_hestia_repository.dart';

void main() {
  testWidgets('설정이 없으면 Splash 다음 Welcome 화면을 보여준다', (tester) async {
    await tester.pumpWidget(
      HestiaApp(repository: MockHestiaRepository(latency: Duration.zero)),
    );
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();

    expect(find.text('시작하기'), findsOneWidget);
  });
}
