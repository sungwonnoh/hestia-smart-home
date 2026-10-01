import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../state/async_controller.dart';

class LoadingView extends StatelessWidget {
  const LoadingView({super.key, this.message = '불러오는 중입니다'});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const SizedBox.square(
            dimension: 48,
            child: CircularProgressIndicator(strokeWidth: 4),
          ),
          const SizedBox(height: 20),
          Text(message, style: Theme.of(context).textTheme.bodyLarge),
        ],
      ),
    );
  }
}

/// empty / error / offline 화면 공용.
class MessageView extends StatelessWidget {
  const MessageView({
    super.key,
    required this.icon,
    required this.title,
    this.message,
    this.actionLabel,
    this.onAction,
    this.color,
  });

  final IconData icon;
  final String title;
  final String? message;
  final String? actionLabel;
  final VoidCallback? onAction;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 64, color: color ?? theme.colorScheme.outline),
            const SizedBox(height: 16),
            Text(
              title,
              style: theme.textTheme.titleLarge,
              textAlign: TextAlign.center,
            ),
            if (message != null) ...[
              const SizedBox(height: 8),
              Text(
                message!,
                style: theme.textTheme.bodyLarge,
                textAlign: TextAlign.center,
              ),
            ],
            if (onAction != null) ...[
              const SizedBox(height: 24),
              OutlinedButton.icon(
                onPressed: onAction,
                icon: const Icon(Icons.refresh_rounded),
                label: Text(actionLabel ?? '다시 시도'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// 연결이 끊겼지만 마지막 데이터는 보여줄 때 상단에 붙는 안내.
class StatusBanner extends StatelessWidget {
  const StatusBanner({
    super.key,
    required this.status,
    this.message,
    this.onRetry,
  });

  final LoadStatus status;
  final String? message;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final offline = status == LoadStatus.offline;
    final title = offline
        ? (message ?? 'HESTIA 서버와 연결할 수 없습니다.')
        : (message ?? '정보를 새로 불러오지 못했습니다.');
    return Material(
      color: HestiaColors.warningContainer,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 8, 8, 8),
        child: Row(
          children: [
            Icon(
              offline ? Icons.cloud_off_rounded : Icons.error_outline_rounded,
              color: HestiaColors.warning,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                '$title\n마지막으로 확인된 상태를 표시합니다.',
                style: const TextStyle(fontSize: 15, height: 1.3),
              ),
            ),
            if (onRetry != null)
              TextButton(onPressed: onRetry, child: const Text('재시도')),
          ],
        ),
      ),
    );
  }
}

/// [AsyncController]의 상태에 맞는 화면을 고른다.
///
/// - 데이터 없음: loading / error / offline / empty 전체 화면
/// - 데이터 있음: 내용 + (error/offline이면) 상단 배너
class AsyncStateView<T> extends StatelessWidget {
  const AsyncStateView({
    super.key,
    required this.controller,
    required this.builder,
    this.emptyIcon = Icons.inbox_rounded,
    this.emptyTitle = '표시할 내용이 없습니다',
    this.emptyMessage,
  });

  final AsyncController<T> controller;
  final Widget Function(BuildContext context, T data) builder;
  final IconData emptyIcon;
  final String emptyTitle;
  final String? emptyMessage;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final data = controller.data;
        final status = controller.status;

        if (data == null) {
          return switch (status) {
            LoadStatus.offline => MessageView(
                icon: Icons.cloud_off_rounded,
                title: 'HESTIA 서버와 연결할 수 없습니다',
                message: '연결 상태를 확인한 뒤 다시 시도해주세요.',
                onAction: controller.load,
                color: HestiaColors.warning,
              ),
            LoadStatus.error => MessageView(
                icon: Icons.error_outline_rounded,
                title: '정보를 불러오지 못했습니다',
                message: controller.message,
                onAction: controller.load,
                color: HestiaColors.safety,
              ),
            _ => const LoadingView(),
          };
        }

        if (status == LoadStatus.empty) {
          return MessageView(
            icon: emptyIcon,
            title: emptyTitle,
            message: emptyMessage,
          );
        }

        final content = builder(context, data);
        if (status != LoadStatus.offline && status != LoadStatus.error) {
          return content;
        }
        return Column(
          children: [
            StatusBanner(
              status: status,
              message: controller.message,
              onRetry: controller.load,
            ),
            Expanded(child: content),
          ],
        );
      },
    );
  }
}
