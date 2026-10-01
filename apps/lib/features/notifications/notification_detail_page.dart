import 'package:flutter/material.dart';

import '../../app/hestia_scope.dart';
import '../../app/routes.dart';
import '../../app/theme.dart';
import '../../core/network/hestia_exception.dart';
import '../../core/utils/display_labels.dart';
import '../../core/utils/time_format.dart';
import '../../core/widgets/state_views.dart';
import '../../models/notification_item.dart';
import '../../models/room.dart';

/// 알림 상세.
///
/// 이 화면을 연 것만으로는 SEEN 처리하지 않는다.
/// 사용자가 [확인]을 눌렀을 때만 SEEN ACK를 보낸다.
class NotificationDetailPage extends StatefulWidget {
  const NotificationDetailPage({super.key, required this.notificationId});

  final String notificationId;

  @override
  State<NotificationDetailPage> createState() => _NotificationDetailPageState();
}

class _NotificationDetailPageState extends State<NotificationDetailPage> {
  List<Room> _rooms = const [];
  bool _acking = false;

  @override
  void initState() {
    super.initState();
    final scope = HestiaScope.of(context);
    if (scope.notifications.byId(widget.notificationId) == null) {
      // build 도중 notifyListeners가 불리지 않도록 프레임 이후로 미룬다.
      Future.microtask(scope.notifications.load);
    }
    _loadRooms();
  }

  Future<void> _loadRooms() async {
    try {
      final rooms = await HestiaScope.of(context).repository.getRooms();
      if (mounted) setState(() => _rooms = rooms);
    } on HestiaException {
      // 위치 이름만 못 보여줄 뿐 화면은 동작해야 한다.
    }
  }

  String? _roomName(String? roomId) {
    if (roomId == null) return null;
    for (final r in _rooms) {
      if (r.id == roomId) return r.name;
    }
    return RoomTemplate.byId(roomId)?.name ?? roomId;
  }

  Future<void> _acknowledge() async {
    setState(() => _acking = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await HestiaScope.of(context).notifications.markSeen(widget.notificationId);
    } on HestiaException catch (e) {
      messenger.showSnackBar(
        SnackBar(content: Text('확인 처리를 전달하지 못했습니다. ${e.message}')),
      );
    } finally {
      if (mounted) setState(() => _acking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final center = HestiaScope.of(context).notifications;
    return Scaffold(
      appBar: AppBar(title: const Text('알림 상세')),
      body: ListenableBuilder(
        listenable: center,
        builder: (context, _) {
          final n = center.byId(widget.notificationId);
          if (n == null) {
            return center.hasData
                ? const MessageView(
                    icon: Icons.search_off_rounded,
                    title: '알림을 찾을 수 없습니다',
                  )
                : const LoadingView();
          }
          return _DetailBody(
            notification: n,
            roomName: _roomName(n.roomId),
            acking: _acking,
            onAcknowledge: _acknowledge,
          );
        },
      ),
    );
  }
}

class _DetailBody extends StatelessWidget {
  const _DetailBody({
    required this.notification,
    required this.roomName,
    required this.acking,
    required this.onAcknowledge,
  });

  final HestiaNotification notification;
  final String? roomName;
  final bool acking;
  final VoidCallback onAcknowledge;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final style = NotificationStyle.of(notification, theme.colorScheme);
    final n = notification;

    return Column(
      children: [
        Expanded(
          child: Scrollbar(
            child: ListView(
              padding: const EdgeInsets.fromLTRB(24, 8, 24, 24),
              children: [
                Container(
                  padding: const EdgeInsets.all(20),
                  decoration: BoxDecoration(
                    color: style.container,
                    borderRadius: BorderRadius.circular(HestiaSizes.radius),
                    border: n.isSafety
                        ? Border.all(color: style.color, width: 2)
                        : null,
                  ),
                  child: Row(
                    children: [
                      Icon(style.icon, color: style.color, size: 44),
                      const SizedBox(width: 16),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              style.label,
                              style: TextStyle(
                                color: style.color,
                                fontWeight: FontWeight.w800,
                                fontSize: 16,
                              ),
                            ),
                            Text(
                              n.title,
                              style: theme.textTheme.headlineSmall?.copyWith(
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 20),
                Text(
                  n.message,
                  style: theme.textTheme.bodyLarge?.copyWith(
                    fontSize: 20,
                    height: 1.5,
                  ),
                ),
                const SizedBox(height: 24),
                _InfoRow(
                  icon: Icons.schedule_rounded,
                  label: '발생 시각',
                  value: TimeFormat.full(n.createdAt, DateTime.now()),
                ),
                if (roomName != null)
                  _InfoRow(
                    icon: Icons.place_rounded,
                    label: '전달 위치',
                    value: roomName!,
                  ),
                _InfoRow(
                  icon: n.seen
                      ? Icons.check_circle_rounded
                      : Icons.mark_email_unread_rounded,
                  label: '상태',
                  value: n.seen ? '확인함' : '확인 전',
                ),
                if (n.explanationId != null) ...[
                  const SizedBox(height: 16),
                  OutlinedButton.icon(
                    onPressed: () => Navigator.of(context).pushNamed(
                      AppRoutes.explanation,
                      arguments: n.explanationId,
                    ),
                    icon: const Icon(Icons.psychology_alt_rounded),
                    label: const Text('HESTIA 판단 과정 보기'),
                  ),
                ],
              ],
            ),
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(24, 8, 24, 20),
          child: SizedBox(
            width: double.infinity,
            child: n.seen
                ? FilledButton.tonalIcon(
                    onPressed: () => Navigator.of(context).maybePop(),
                    icon: const Icon(Icons.check_rounded),
                    label: const Text('확인했습니다 · 닫기'),
                  )
                : FilledButton(
                    style: n.isSafety
                        ? FilledButton.styleFrom(
                            backgroundColor: HestiaColors.safety,
                            foregroundColor: Colors.white,
                          )
                        : null,
                    onPressed: acking ? null : onAcknowledge,
                    child: acking
                        ? const SizedBox.square(
                            dimension: 28,
                            child: CircularProgressIndicator(strokeWidth: 3),
                          )
                        : const Text('확인'),
                  ),
          ),
        ),
      ],
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({
    required this.icon,
    required this.label,
    required this.value,
  });

  final IconData icon;
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        children: [
          Icon(icon, color: theme.colorScheme.onSurfaceVariant),
          const SizedBox(width: 12),
          SizedBox(
            width: 96,
            child: Text(label, style: theme.textTheme.bodyLarge),
          ),
          Expanded(
            child: Text(
              value,
              style: theme.textTheme.titleMedium,
            ),
          ),
        ],
      ),
    );
  }
}
