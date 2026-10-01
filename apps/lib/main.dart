import 'package:flutter/material.dart';

import 'app/app.dart';
import 'repositories/mock_hestia_repository.dart';

void main() {
  // Phase 1: 서버 없이 Mock 데이터로 실행한다.
  runApp(HestiaApp(repository: MockHestiaRepository()));
}
