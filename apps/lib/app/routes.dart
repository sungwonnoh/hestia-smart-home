/// 화면 이름. feature끼리 서로를 import하지 않고 이 이름으로 이동한다.
abstract final class AppRoutes {
  static const splash = '/';
  static const onboarding = '/onboarding';
  static const shell = '/main';

  /// arguments: notification id (String)
  static const notificationDetail = '/notifications/detail';

  /// arguments: explanation id (String) 또는 null(최신 판단)
  static const explanation = '/explanation';

  static const settingsRooms = '/settings/rooms';
  static const settingsDevices = '/settings/devices';
  static const settingsNotifications = '/settings/notifications';
  static const settingsSystem = '/settings/system';
}
