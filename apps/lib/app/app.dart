import 'package:flutter/material.dart';

import '../core/constants/app_config.dart';
import '../repositories/hestia_repository.dart';
import 'hestia_scope.dart';
import 'notification_center.dart';
import 'router.dart';
import 'routes.dart';
import 'theme.dart';

class HestiaApp extends StatefulWidget {
  const HestiaApp({super.key, required this.repository});

  /// Phase 1은 Mock, Phase 2부터 API 구현체를 넣는다.
  final HestiaRepository repository;

  @override
  State<HestiaApp> createState() => _HestiaAppState();
}

class _HestiaAppState extends State<HestiaApp> {
  late final NotificationCenter _notifications =
      NotificationCenter(widget.repository);

  @override
  void dispose() {
    _notifications.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return HestiaScope(
      repository: widget.repository,
      notifications: _notifications,
      child: MaterialApp(
        title: AppConfig.appName,
        debugShowCheckedModeBanner: false,
        theme: HestiaTheme.light(),
        initialRoute: AppRoutes.splash,
        onGenerateRoute: AppRouter.onGenerateRoute,
      ),
    );
  }
}
