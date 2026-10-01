import 'dart:async';

import 'package:flutter/material.dart';

import '../features/home/home_page.dart';
import '../features/notifications/notifications_page.dart';
import '../features/settings/settings_page.dart';
import '../models/notification_item.dart';
import 'hestia_scope.dart';
import 'routes.dart';
import 'theme.dart';

/// [홈] [알림] [설정] 탭.
///
/// 가로가 넓은 터치스크린(예: 800x480)에서는 세로 공간을 아끼려고
/// 왼쪽 NavigationRail을, 그 외에는 하단 NavigationBar를 쓴다.
class MainShell extends StatefulWidget {
  const MainShell({super.key});

  @override
  State<MainShell> createState() => _MainShellState();
}

class _MainShellState extends State<MainShell> {
  int _index = 0;
  StreamSubscription<HestiaNotification>? _incoming;

  @override
  void initState() {
    super.initState();
    final center = HestiaScope.of(context).notifications;
    _incoming = center.incoming.listen(_showIncoming);
    // build 도중 notifyListeners가 불리지 않도록 프레임 이후로 미룬다.
    Future.microtask(center.load);
  }

  @override
  void dispose() {
    _incoming?.cancel();
    super.dispose();
  }

  void _select(int index) => setState(() => _index = index);

  void _showIncoming(HestiaNotification n) {
    if (!mounted) return;
    final messenger = ScaffoldMessenger.of(context)..hideCurrentSnackBar();
    messenger.showSnackBar(
      SnackBar(
        backgroundColor: n.isSafety ? HestiaColors.safety : null,
        duration: Duration(seconds: n.isSafety ? 10 : 5),
        behavior: SnackBarBehavior.floating,
        content: Row(
          children: [
            Icon(
              n.isSafety
                  ? Icons.health_and_safety_rounded
                  : Icons.notifications_active_rounded,
              color: Colors.white,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                n.title,
                style: const TextStyle(fontSize: 18, color: Colors.white),
              ),
            ),
          ],
        ),
        action: SnackBarAction(
          label: '보기',
          textColor: Colors.white,
          onPressed: () => Navigator.of(context).pushNamed(
            AppRoutes.notificationDetail,
            arguments: n.id,
          ),
        ),
      ),
    );
  }

  Widget _page() => switch (_index) {
        // 탭을 다시 열 때마다 새로 만들어 최신 상태를 읽는다.
        0 => HomePage(onOpenNotifications: () => _select(1)),
        1 => const NotificationsPage(),
        _ => const SettingsPage(),
      };

  @override
  Widget build(BuildContext context) {
    final center = HestiaScope.of(context).notifications;
    return ListenableBuilder(
      listenable: center,
      builder: (context, _) {
        final unseen = center.unseenCount;
        Widget badged(IconData icon) => Badge(
              isLabelVisible: unseen > 0,
              label: Text('$unseen'),
              child: Icon(icon),
            );

        final destinations = [
          (Icons.home_outlined, Icons.home_rounded, '홈'),
          (Icons.notifications_outlined, Icons.notifications_rounded, '알림'),
          (Icons.settings_outlined, Icons.settings_rounded, '설정'),
        ];

        return LayoutBuilder(
          builder: (context, constraints) {
            final useRail = constraints.maxWidth >= 720 &&
                constraints.maxWidth > constraints.maxHeight;

            if (useRail) {
              return Scaffold(
                body: Row(
                  children: [
                    SafeArea(
                      child: NavigationRail(
                        selectedIndex: _index,
                        onDestinationSelected: _select,
                        labelType: NavigationRailLabelType.all,
                        minWidth: 96,
                        groupAlignment: 0,
                        selectedLabelTextStyle: const TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w700,
                        ),
                        unselectedLabelTextStyle:
                            const TextStyle(fontSize: 16),
                        destinations: [
                          for (final (i, d) in destinations.indexed)
                            NavigationRailDestination(
                              icon: i == 1 ? badged(d.$1) : Icon(d.$1),
                              selectedIcon: i == 1 ? badged(d.$2) : Icon(d.$2),
                              label: Text(d.$3),
                              padding: const EdgeInsets.symmetric(vertical: 8),
                            ),
                        ],
                      ),
                    ),
                    const VerticalDivider(width: 1),
                    Expanded(child: _page()),
                  ],
                ),
              );
            }

            return Scaffold(
              body: _page(),
              bottomNavigationBar: NavigationBar(
                selectedIndex: _index,
                onDestinationSelected: _select,
                destinations: [
                  for (final (i, d) in destinations.indexed)
                    NavigationDestination(
                      icon: i == 1 ? badged(d.$1) : Icon(d.$1),
                      selectedIcon: i == 1 ? badged(d.$2) : Icon(d.$2),
                      label: d.$3,
                    ),
                ],
              ),
            );
          },
        );
      },
    );
  }
}
