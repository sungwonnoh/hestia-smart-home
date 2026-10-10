import 'package:flutter/material.dart';

import '../../app/hestia_scope.dart';
import '../../app/routes.dart';
import '../../app/theme.dart';
import '../../core/utils/display_labels.dart';
import '../../core/widgets/hestia_card.dart';
import '../../core/widgets/state_views.dart';
import '../../models/medication.dart';
import 'medication_controllers.dart';

/// 복약 관리: 등록한 약 목록, 추가·수정·삭제.
class MedicationListPage extends StatefulWidget {
  const MedicationListPage({super.key});

  @override
  State<MedicationListPage> createState() => _MedicationListPageState();
}

class _MedicationListPageState extends State<MedicationListPage> {
  late final MedicationListController _controller;

  @override
  void initState() {
    super.initState();
    _controller =
        MedicationListController(HestiaScope.of(context).repository)..load();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _edit([Medication? medication]) async {
    final saved = await Navigator.of(context)
        .pushNamed(AppRoutes.medicationEdit, arguments: medication);
    if (saved != null) await _controller.load();
  }

  Future<void> _delete(Medication medication) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('${medication.name}을(를) 삭제할까요?'),
        content: const Text('이 약의 복약 알림도 더 이상 받지 않아요.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('취소'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('삭제'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    final error = await _controller.delete(medication);
    if (error != null && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('삭제하지 못했습니다. $error')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('복약 관리')),
      body: SafeArea(
        child: AsyncStateView<List<Medication>>(
          controller: _controller,
          builder: (context, items) => Column(
            children: [
              Expanded(
                child: items.isEmpty
                    ? const MessageView(
                        icon: Icons.medication_rounded,
                        title: '등록한 약이 없어요',
                        message: '드시는 약을 추가하면 약 드실 때 알려드려요.',
                      )
                    : Scrollbar(
                        child: ListView(
                          padding: const EdgeInsets.fromLTRB(24, 8, 24, 24),
                          children: [
                            for (final m in items)
                              Padding(
                                padding: const EdgeInsets.only(bottom: 12),
                                child: _MedicationCard(
                                  medication: m,
                                  onTap: () => _edit(m),
                                  onDelete: () => _delete(m),
                                ),
                              ),
                          ],
                        ),
                      ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(24, 8, 24, 16),
                child: SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: () => _edit(),
                    icon: const Icon(Icons.add_rounded, size: 28),
                    label: const Text('약 추가하기'),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _MedicationCard extends StatelessWidget {
  const _MedicationCard({
    required this.medication,
    required this.onTap,
    required this.onDelete,
  });

  final Medication medication;
  final VoidCallback onTap;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final (period, warn) = MedicationLabels.period(medication, DateTime.now());
    return HestiaCard(
      onTap: onTap,
      borderColor: warn ? HestiaColors.warning : null,
      padding: const EdgeInsets.fromLTRB(20, 16, 8, 16),
      child: Row(
        children: [
          Icon(Icons.medication_rounded,
              size: 36, color: theme.colorScheme.primary),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(medication.name, style: theme.textTheme.titleLarge),
                const SizedBox(height: 2),
                Text(medication.scheduleLabel,
                    style: theme.textTheme.bodyLarge),
                const SizedBox(height: 6),
                Row(
                  children: [
                    if (warn) ...[
                      const Icon(Icons.warning_amber_rounded,
                          size: 20, color: HestiaColors.warning),
                      const SizedBox(width: 4),
                    ],
                    Text(
                      period,
                      style: theme.textTheme.titleSmall?.copyWith(
                        color: warn ? HestiaColors.warning : null,
                      ),
                    ),
                    if (medication.refillRequired && !warn) ...[
                      const SizedBox(width: 10),
                      Icon(Icons.event_repeat_rounded,
                          size: 18, color: theme.colorScheme.outline),
                      const SizedBox(width: 2),
                      Text('정기 처방', style: theme.textTheme.bodyMedium),
                    ],
                  ],
                ),
              ],
            ),
          ),
          IconButton(
            tooltip: '삭제',
            iconSize: 28,
            constraints: const BoxConstraints.tightFor(
              width: HestiaSizes.minTouch,
              height: HestiaSizes.minTouch,
            ),
            onPressed: onDelete,
            icon: const Icon(Icons.delete_outline_rounded),
          ),
        ],
      ),
    );
  }
}
