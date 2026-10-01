import 'package:flutter/material.dart';

import '../core/widgets/state_views.dart';
import '../features/explanation/explanation_page.dart';
import '../features/notifications/notification_detail_page.dart';
import '../features/onboarding/onboarding_page.dart';
import '../features/settings/setup_edit_pages.dart';
import '../features/settings/system_info_page.dart';
import '../features/splash/splash_page.dart';
import 'main_shell.dart';
import 'routes.dart';

abstract final class AppRouter {
  static Route<dynamic> onGenerateRoute(RouteSettings settings) {
    final args = settings.arguments;
    final Widget page = switch (settings.name) {
      AppRoutes.splash => const SplashPage(),
      AppRoutes.onboarding => const OnboardingPage(),
      AppRoutes.shell => const MainShell(),
      AppRoutes.notificationDetail when args is String =>
        NotificationDetailPage(notificationId: args),
      AppRoutes.explanation =>
        ExplanationPage(explanationId: args is String ? args : null),
      AppRoutes.settingsRooms => const RoomsSettingsPage(),
      AppRoutes.settingsDevices => const DevicesSettingsPage(),
      AppRoutes.settingsNotifications => const NotificationSettingsPage(),
      AppRoutes.settingsSystem => const SystemInfoPage(),
      _ => const _UnknownRoutePage(),
    };
    return MaterialPageRoute<void>(builder: (_) => page, settings: settings);
  }
}

class _UnknownRoutePage extends StatelessWidget {
  const _UnknownRoutePage();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(),
      body: const MessageView(
        icon: Icons.explore_off_rounded,
        title: '화면을 찾을 수 없습니다',
      ),
    );
  }
}
