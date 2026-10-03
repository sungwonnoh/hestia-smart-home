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

/// 서버가 2xx가 아닌 응답을 줌. 404는 "없음"으로 처리할 수 있게 상태 코드를 둔다.
class HestiaHttpException extends HestiaException {
  const HestiaHttpException(this.statusCode, super.message);

  final int statusCode;

  bool get isNotFound => statusCode == 404;
}
