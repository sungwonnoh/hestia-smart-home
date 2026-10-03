import 'package:flutter/material.dart';

import 'app/app.dart';
import 'core/constants/app_config.dart';
import 'repositories/api_hestia_repository.dart';
import 'repositories/hestia_repository.dart';
import 'repositories/mock_hestia_repository.dart';

void main() {
  // Repository만 바꾼다. 화면 코드는 데이터 출처를 모른다.
  final HestiaRepository repository = AppConfig.useApi
      ? ApiHestiaRepository.fromConfig()
      : MockHestiaRepository();
  runApp(HestiaApp(repository: repository));
}
