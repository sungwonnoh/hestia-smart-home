/// Repository가 던지는 예외. UI는 이 타입으로 error/offline을 구분한다.
class HestiaException implements Exception {
  const HestiaException(this.message);

  final String message;

  @override
  String toString() => 'HestiaException: $message';
}

/// HESTIA 서버(또는 Mock)에 연결할 수 없음.
class HestiaOfflineException extends HestiaException {
  const HestiaOfflineException([
    super.message = 'HESTIA 서버와 연결할 수 없습니다.',
  ]);
}
