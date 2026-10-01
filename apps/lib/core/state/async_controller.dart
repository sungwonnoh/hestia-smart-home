import 'package:flutter/foundation.dart';

import '../network/hestia_exception.dart';

/// 화면이 반드시 구분해야 하는 상태.
enum LoadStatus { loading, success, empty, error, offline }

/// Repository 호출 하나를 감싸는 Controller 기반 클래스.
///
/// 연결이 끊겨도 마지막으로 받은 [data]는 유지한다.
abstract class AsyncController<T> extends ChangeNotifier {
  LoadStatus _status = LoadStatus.loading;
  T? _data;
  String? _message;
  bool _disposed = false;

  LoadStatus get status => _status;
  T? get data => _data;
  String? get message => _message;

  bool get hasData => _data != null;

  @protected
  Future<T> fetch();

  /// 받은 데이터가 비었는지. empty 상태 표시에 쓴다.
  @protected
  bool isEmptyData(T data) => false;

  Future<void> load() async {
    if (_data == null && _status != LoadStatus.loading) {
      _status = LoadStatus.loading;
      _notify();
    }
    try {
      final result = await fetch();
      _data = result;
      _message = null;
      _status = isEmptyData(result) ? LoadStatus.empty : LoadStatus.success;
    } on HestiaOfflineException catch (e) {
      _status = LoadStatus.offline;
      _message = e.message;
    } on HestiaException catch (e) {
      _status = LoadStatus.error;
      _message = e.message;
    } catch (e) {
      _status = LoadStatus.error;
      _message = '알 수 없는 오류가 발생했습니다.';
      debugPrint('AsyncController.load failed: $e');
    }
    _notify();
  }

  /// 로컬에서 바뀐 데이터를 반영한다 (예: 알림 확인 처리).
  @protected
  void setData(T value) {
    _data = value;
    if (_status == LoadStatus.success || _status == LoadStatus.empty) {
      _status = isEmptyData(value) ? LoadStatus.empty : LoadStatus.success;
    }
    _notify();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
