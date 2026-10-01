import 'package:flutter/widgets.dart';

import '../repositories/hestia_repository.dart';
import 'notification_center.dart';

/// Repository와 앱 공용 상태를 하위 위젯에 전달한다.
///
/// 상태관리 패키지를 추가하지 않기 위해 InheritedWidget으로 둔다.
class HestiaScope extends InheritedWidget {
  const HestiaScope({
    super.key,
    required this.repository,
    required this.notifications,
    required super.child,
  });

  final HestiaRepository repository;
  final NotificationCenter notifications;

  /// initState에서도 호출할 수 있다 (의존 관계를 만들지 않음).
  static HestiaScope of(BuildContext context) {
    final scope = context.getInheritedWidgetOfExactType<HestiaScope>();
    assert(scope != null, 'HestiaScope가 위젯 트리에 없습니다.');
    return scope!;
  }

  @override
  bool updateShouldNotify(HestiaScope oldWidget) =>
      repository != oldWidget.repository ||
      notifications != oldWidget.notifications;
}
