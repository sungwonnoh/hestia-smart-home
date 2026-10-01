import '../models/context_state.dart';
import '../models/device.dart';
import '../models/explanation.dart';
import '../models/home_setup.dart';
import '../models/notification_item.dart';
import '../models/room.dart';
import '../models/user_preferences.dart';

/// UI와 서버 사이의 경계.
///
/// Phase 1은 [MockHestiaRepository], Phase 2는 REST 구현체로 교체한다.
/// Widget은 이 인터페이스만 알고, HTTP/MQTT를 직접 다루지 않는다.
///
/// 연결 실패 시 구현체는 `HestiaOfflineException`을 던진다.
abstract interface class HestiaRepository {
  /// 최초 설정. 아직 설정하지 않았으면 null.
  Future<HomeSetup?> getSetup();

  Future<void> saveSetup(HomeSetup setup);

  Future<List<Room>> getRooms();

  Future<List<Device>> getDevices();

  Future<HestiaContext> getCurrentContext();

  Future<List<HestiaNotification>> getNotifications();

  /// 사용자가 실제로 확인했을 때만 [AckType.seen]으로 호출한다.
  Future<void> acknowledgeNotification(
    String notificationId, {
    AckType ackType = AckType.seen,
  });

  Future<Explanation?> getLatestExplanation();

  Future<Explanation?> getExplanation(String explanationId);

  Future<UserPreferences> getPreferences();

  Future<void> savePreferences(UserPreferences preferences);

  /// 새 알림 스트림. 실시간 방식(MQTT/WebSocket/SSE)은 구현체가 정한다.
  Stream<HestiaNotification> watchNotifications();
}
