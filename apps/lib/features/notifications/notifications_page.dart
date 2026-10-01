import 'package:flutter/material.dart';

import '../../app/hestia_scope.dart';
import '../../app/routes.dart';
import '../../core/utils/display_labels.dart';
import '../../core/utils/time_format.dart';
import '../../core/widgets/hestia_card.dart';
import '../../core/widgets/state_views.dart';
import '../../models/notification_item.dart';

class NotificationsPage extends StatelessWidget {
  const NotificationsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final center = HestiaScope.of(context).notifications;
    return Scaffold(
      appBar: AppBar(
        title: ListenableBuilder(
          listenable: center,
          builder: (context, _) => Text(
            center.unseenCount > 0 ? '알림 · 새 알림 ${center.unseenCount}' : '알림',
          ),
        ),
      ),
      body: AsyncStateView<List<HestiaNotification>>(
        controller: center,
        emptyIcon: Icons.notifications_none_rounded,
        emptyTitle: '알림이 없습니다',
        emptyMessage: 'HESTIA가 알려드릴 일이 생기면 여기에 표시됩니다.',
        builder: (context, items) {
          final now = DateTime.now();
          final groups = <String, List<HestiaNotification>>{};
          for (final n in items) {
            groups.putIfAbsent(TimeFormat.group(n.createdAt, now), () => []).add(n);
          }
          return RefreshIndicator(
            onRefresh: center.load,
            child: Scrollbar(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(24, 0, 24, 24),
                children: [
                  for (final entry in groups.entries) ...[
                    SectionHeader(entry.key),
                    for (final n in entry.value)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: NotificationTile(notification: n, now: now),
                      ),
                  ],
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}

class NotificationTile extends StatelessWidget {
  const NotificationTile({
    super.key,
    required this.notification,
    required this.now,
  });

  final HestiaNotification notification;
  final DateTime now;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final style = NotificationStyle.of(notification, theme.colorScheme);
    final unseen = !notification.seen;

    return HestiaCard(
      padding: const EdgeInsets.all(16),
      color: notification.isSafety ? style.container : null,
      borderColor: notification.isSafety
          ? style.color
          : (unseen ? theme.colorScheme.primary : null),
      onTap: () => Navigator.of(context).pushNamed(
        AppRoutes.notificationDetail,
        arguments: notification.id,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 52,
            height: 52,
            decoration: BoxDecoration(
              color: style.container,
              borderRadius: BorderRadius.circular(14),
            ),
            child: Icon(style.icon, color: style.color, size: 30),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    _Tag(label: style.label, color: style.color),
                    const SizedBox(width: 8),
                    Text(
                      TimeFormat.relative(notification.createdAt, now),
                      style: theme.textTheme.bodyMedium,
                    ),
                    const Spacer(),
                    if (unseen) _Tag(label: '새 알림', color: style.color, filled: true),
                  ],
                ),
                const SizedBox(height: 6),
                Text(
                  notification.title,
                  style: TextStyle(
                    fontSize: 19,
                    fontWeight: unseen ? FontWeight.w800 : FontWeight.w500,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  notification.message,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodyMedium,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Tag extends StatelessWidget {
  const _Tag({required this.label, required this.color, this.filled = false});

  final String label;
  final Color color;
  final bool filled;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: filled ? color : null,
        border: Border.all(color: color, width: 1.5),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 13,
          fontWeight: FontWeight.w700,
          color: filled ? Colors.white : color,
        ),
      ),
    );
  }
}
