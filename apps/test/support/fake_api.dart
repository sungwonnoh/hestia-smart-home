import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// FastAPI 대신 응답을 돌려주는 로컬 HTTP 서버.
class FakeApi {
  late HttpServer _server;
  bool _stopped = false;
  final routes = <String, (int, Object?)>{};
  final requests = <(String, String, String)>[];

  int get port => _server.port;

  Future<void> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server.listen((req) async {
      final body = await utf8.decoder.bind(req).join();
      requests.add((req.method, req.uri.path, body));
      final (status, json) =
          routes['${req.method} ${req.uri.path}'] ?? (404, {'detail': 'not found'});
      req.response
        ..statusCode = status
        ..headers.contentType = ContentType.json
        ..write(jsonEncode(json));
      await req.response.close();
    });
  }

  Future<void> stop() async {
    if (_stopped) return;
    _stopped = true;
    await _server.close(force: true);
  }
}
