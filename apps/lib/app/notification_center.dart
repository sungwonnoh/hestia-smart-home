import 'dart:async';

import '../core/state/async_controller.dart';
import '../models/notification_item.dart';
import '../repositories/hestia_repository.dart';

/// 앱 전체가 공유하는 알림 상태.
///
/// 홈의 최근 알림, 알림 탭 배지, 알림 목록/상세가 같은 데이터를 본다.
class NotificationCenter extends AsyncController<List<HestiaNotification>> {
  NotificationCenter(this._repository) {
    _subscription = _repository.watchNotifications().listen(_onIncoming);
  }

  final HestiaRepository _repository;
  late final StreamSubscription<HestiaNotification> _subscription;
  final _incoming = StreamController<HestiaNotification>.broadcast();

  /// 새로 도착한 알림. 셸에서 배너를 띄울 때 쓴다.
  Stream<HestiaNotification> get incoming => _incoming.stream;

  List<HestiaNotification> get items => data ?? const [];

  int get unseenCount => items.where((n) => !n.seen).length;

  HestiaNotification? byId(String id) {
    for (final n in items) {
      if (n.id == id) return n;
    }
    return null;
  }

  @override
  Future<List<HestiaNotification>> fetch() => _repository.getNotifications();

  @override
  bool isEmptyData(List<HestiaNotification> data) => data.isEmpty;

  /// 사용자가 [확인]을 눌렀을 때만 호출한다. 화면 표시만으로 부르지 않는다.
  ///
  /// 실패하면 예외를 그대로 던져 화면이 안내하게 한다.
  Future<void> markSeen(String id) async {
    await _repository.acknowledgeNotification(id, ackType: AckType.seen);
    setData([
      for (final n in items) n.id == id ? n.copyWith(seen: true) : n,
    ]);
  }

  void _onIncoming(HestiaNotification notification) {
    setData([notification, ...items.where((n) => n.id != notification.id)]);
    _incoming.add(notification);
  }

  @override
  void dispose() {
    _subscription.cancel();
    _incoming.close();
    super.dispose();
  }
}
