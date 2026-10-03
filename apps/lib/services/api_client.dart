import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../core/network/hestia_exception.dart';

/// HESTIA FastAPI 호출. Repository만 쓰고 Widget은 직접 쓰지 않는다.
///
/// ATLAS에서 플러그인 패키지 문제가 없도록 dart:io HttpClient만 쓴다.
/// 연결 실패·시간 초과는 [HestiaOfflineException], 2xx가 아닌 응답은
/// [HestiaHttpException]으로 바꿔 던진다.
class ApiClient {
  ApiClient({
    required String baseUrl,
    this.timeout = const Duration(seconds: 5),
    HttpClient? httpClient,
  })  : _base = Uri.parse(baseUrl.endsWith('/') ? baseUrl : '$baseUrl/'),
        _http = httpClient ?? HttpClient() {
    _http.connectionTimeout = timeout;
  }

  final Uri _base;
  final HttpClient _http;
  final Duration timeout;

  Uri get baseUri => _base;

  Future<Object?> get(String path) => _send('GET', path);

  Future<Object?> put(String path, Object? body) =>
      _send('PUT', path, body: body);

  Future<Object?> post(String path, Object? body) =>
      _send('POST', path, body: body);

  /// [path]는 base 기준 상대 경로 (예: 'api/v1/setup').
  Future<Object?> _send(String method, String path, {Object? body}) async {
    final uri = _base.resolve(path.startsWith('/') ? path.substring(1) : path);
    try {
      final request = await _http.openUrl(method, uri).timeout(timeout);
      request.headers.set(HttpHeaders.acceptHeader, 'application/json');
      if (body != null) {
        request.headers.contentType = ContentType.json;
        request.write(jsonEncode(body));
      }
      final response = await request.close().timeout(timeout);
      final text =
          await response.transform(utf8.decoder).join().timeout(timeout);

      if (response.statusCode >= 200 && response.statusCode < 300) {
        return text.isEmpty ? null : jsonDecode(text);
      }
      throw HestiaHttpException(
        response.statusCode,
        _detail(text) ?? 'HTTP ${response.statusCode}',
      );
    } on SocketException {
      throw const HestiaOfflineException();
    } on HttpException {
      throw const HestiaOfflineException();
    } on TimeoutException {
      throw const HestiaOfflineException('HESTIA 서버 응답이 없습니다.');
    } on FormatException {
      throw const HestiaException('서버 응답을 해석할 수 없습니다.');
    }
  }

  /// FastAPI 오류 본문 {"detail": "..."}. 검증 오류(422)는 detail이 목록이라 쓰지 않는다.
  static String? _detail(String text) {
    try {
      final json = jsonDecode(text);
      if (json is Map && json['detail'] is String) return json['detail'] as String;
    } on FormatException {
      // 본문이 JSON이 아님
    }
    return null;
  }

  void close() => _http.close(force: true);
}
