/// 빌드 시 주입하는 설정.
///
/// 서버 위치는 아직 정해지지 않았으므로 코드에 고정하지 않는다.
/// `flutter run --dart-define=API_BASE_URL=http://<host>:<port>`
abstract final class AppConfig {
  static const appName = 'HESTIA';
  static const appVersion = '0.1.0';

  static const apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://127.0.0.1:8000',
  );
}
